import os
import sys
import json
import math
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
import requests
import config
import db
import s3_client
import runpod_client
import captions

log = logging.getLogger("opussb.processor")

# Tetos de tempo dos ffmpeg do pipeline. NÃO são limites de performance: são rede de
# proteção contra um ffmpeg travado (pipe/codec) que penduraria a thread pra sempre.
# O watchdog (JOB_STALE_MINUTES) só marca o registro como error; o processo/thread
# vazariam sem isto. Valores folgados pra não matar job legítimo longo.
_FFMPEG_AUDIO_TIMEOUT = 1800     # extrair áudio do vídeo inteiro (pode ser longo)
_FFMPEG_CUT_TIMEOUT = 900        # cortar/recodificar um clipe (curto)


class _Cancelled(Exception):
    """Sinaliza que o job foi cancelado externamente (status virou 'error')."""


def _check_cancel(job_id: str) -> None:
    """Aborta cooperativamente se o job sumiu ou foi marcado como error (cancelado).
    Chamado entre etapas pra a thread parar e não sobrescrever o cancelamento."""
    j = db.get_job(job_id)
    if j is None or j["status"] == "error":
        raise _Cancelled()


def run_thread1(job_id: str) -> None:
    """Thread 1: extract audio → upload to S3 → trigger N8N."""
    job = db.get_job(job_id)
    try:
        _check_cancel(job_id)
        db.update_job(job_id, status="extracting_audio", progress=15)
        audio_path = _extract_audio(job["local_path"], job_id)

        _check_cancel(job_id)
        db.update_job(job_id, status="uploading_audio", progress=25)
        audio_s3_key = f"jobs/{job_id}/audio/audio.mp3"
        _s3_upload_with_retry(audio_path, audio_s3_key)
        audio_s3_url = s3_client.get_presigned_url(audio_s3_key, expiry_seconds=86400)
        db.update_job(job_id, audio_s3_url=audio_s3_url, status="transcribing", progress=35)

        _trigger_n8n_with_retry(
            job_id, audio_s3_url, job["min_duration"], job["max_duration"]
        )
    except _Cancelled:
        log.info("[cancel] job %s cancelado; encerrando thread1 e limpando disco", job_id)
        shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)
    except Exception as e:
        _fail_job(job_id, str(e))


# ─── Fonte YouTube (yt-dlp): baixa e cai no MESMO pipeline do upload ──────────
# O yt-dlp vive no mesmo venv do app (pip install yt-dlp -> venv/bin/yt-dlp). Chamamos
# por subprocess (não importamos o módulo) pra isolar falhas e aplicar timeout matando o
# processo travado, igual aos ffmpeg do pipeline. Só VOD (live encerrada/normal); live em
# andamento é recusada na rota (sem duração definida).
_YTDLP = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
_YT_PROBE_TIMEOUT = 45       # ler metadados (sem baixar)
_YT_DOWNLOAD_TIMEOUT = 3600  # baixar o vídeo (até 2h de vídeo cabe com folga)
_YT_SOCKET_TIMEOUT = 30      # aborta socket morto em vez de pendurar até o teto global
_YT_MAX_FILESIZE = "12G"     # teto de bytes (o teto de duração não limita bitrate): trava
                             # vídeo curto porém pesado de estourar o disco/banda
_YT_HEARTBEAT_SECONDS = 60   # de quanto em quanto tempo marca "sinal de vida" no download
# O YouTube bloqueia download de IP de datacenter ("confirme que não é um robô"). A saída é
# autenticar com cookies de uma conta Google. Se este arquivo existir (Netscape cookies.txt,
# fora do repo, 600), passamos --cookies; senão, segue sem (funciona só fora do bloqueio).
# Use uma conta DEDICADA: cookies de download server-side podem fazer o Google flaggar a conta.
_YT_COOKIES = os.environ.get(
    "YOUTUBE_COOKIES_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".youtube_cookies.txt"),
)


def _cookie_copy_args():
    """O yt-dlp REESCREVE o arquivo passado em --cookies a cada execução; quando o YouTube
    invalida a sessão (comum em IP de datacenter), ele grava de volta um jar degradado e
    DESTRÓI o master exportado (foi o que matou os cookies em produção). Damos a ele uma CÓPIA
    descartável, preservando o master. Retorna (args, tmp_path|None); o chamador remove o tmp."""
    if not (_YT_COOKIES and os.path.exists(_YT_COOKIES)):
        return [], None
    fd, tmp = tempfile.mkstemp(prefix="ytck_", suffix=".txt")
    os.close(fd)
    try:
        shutil.copyfile(_YT_COOKIES, tmp)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return [], None
    return ["--cookies", tmp], tmp


def _rm_quiet(path) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


# Retry no bloqueio anti-robô do YouTube: o IP de datacenter leva "confirme que não é um robô"
# de forma INTERMITENTE mesmo com cookies+Deno+PO token; a MESMA URL costuma passar numa tentativa
# seguinte. Só repete nesse caso (erro permanente como vídeo indisponível não adianta repetir).
_YT_RETRIES = 3


def _is_botblock(stderr: str) -> bool:
    s = (stderr or "").lower()
    return "sign in to confirm" in s or "not a bot" in s


def _run_ytdlp(build_cmd, timeout: int, timeout_msg: str):
    """Roda o yt-dlp com cópia descartável dos cookies e RETRY no bloqueio anti-robô intermitente.
    build_cmd(cookie_args) monta a lista do comando. Levanta RuntimeError em timeout/yt-dlp ausente;
    senão devolve o CompletedProcess da última tentativa (o chamador checa returncode)."""
    last = None
    for attempt in range(_YT_RETRIES):
        ck_args, ck_tmp = _cookie_copy_args()
        try:
            last = subprocess.run(build_cmd(ck_args), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(timeout_msg)
        except FileNotFoundError:
            raise RuntimeError("yt-dlp não está instalado no servidor.")
        finally:
            _rm_quiet(ck_tmp)
        if last.returncode == 0 or not _is_botblock(last.stderr):
            return last
        if attempt < _YT_RETRIES - 1:
            log.info("[youtube] bloqueio anti-robô intermitente (tentativa %d/%d); repetindo",
                     attempt + 1, _YT_RETRIES)
            time.sleep(3)
    return last


# Prefere h264+aac em mp4 (merge limpo, sem recode), até 1080p (reframe é 1080x1920, mais
# que isso é desperdício). Fallbacks progressivos até o melhor disponível.
_YT_FORMAT = (
    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "best[height<=1080][ext=mp4]/"
    "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
)


def _yt_error_hint(stderr: str) -> str:
    """Traduz o erro do yt-dlp pra uma mensagem amigável (o stderr cru é técnico)."""
    s = (stderr or "").lower()
    if "sign in to confirm" in s or "not a bot" in s or "confirm your age" in s:
        return ("O YouTube bloqueou o download a partir deste servidor (proteção anti-robô). "
                "Precisamos configurar cookies do navegador no servidor.")
    if "private video" in s or "video unavailable" in s or "been removed" in s or "members-only" in s:
        return "Vídeo indisponível, privado, removido ou só para membros."
    if "unsupported url" in s or "is not a valid url" in s or "no video" in s:
        return "Link não suportado. Cole a URL de um vídeo do YouTube."
    return "Não consegui baixar este vídeo do YouTube."


def youtube_probe(url: str) -> dict:
    """Lê metadados do vídeo SEM baixar (título, duração, se é live). Levanta RuntimeError
    com mensagem amigável se o yt-dlp falhar, for bloqueado, ou o link for inválido."""
    r = _run_ytdlp(
        lambda ck: [_YTDLP, *ck, "--socket-timeout", str(_YT_SOCKET_TIMEOUT),
                    "--dump-single-json", "--no-playlist", "--skip-download", "--no-warnings", url],
        _YT_PROBE_TIMEOUT, "Tempo esgotado ao ler o link do YouTube.",
    )
    if r.returncode != 0:
        raise RuntimeError(_yt_error_hint(r.stderr))
    try:
        info = json.loads(r.stdout or "{}")
    except (TypeError, ValueError):
        raise RuntimeError("Não consegui ler os dados do vídeo.")
    return {
        "title": (info.get("title") or "").strip() or "Vídeo do YouTube",
        "duration": info.get("duration"),
        "is_live": bool(info.get("is_live")),
        "live_status": info.get("live_status"),
    }


def _download_youtube(url: str, out_path: str, job_id: str) -> None:
    """Baixa o vídeo do YouTube pra out_path (mp4). Levanta RuntimeError em falha/bloqueio/
    timeout (o subprocess é morto no estouro) ou se o arquivo sair vazio."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    r = _run_ytdlp(
        lambda ck: [_YTDLP, *ck, "-f", _YT_FORMAT, "--merge-output-format", "mp4",
                    "--no-playlist", "--no-part", "--no-warnings", "--retries", "3",
                    "--socket-timeout", str(_YT_SOCKET_TIMEOUT), "--max-filesize", _YT_MAX_FILESIZE,
                    "-o", out_path, url],
        _YT_DOWNLOAD_TIMEOUT, "Tempo esgotado ao baixar o vídeo do YouTube.",
    )
    if r.returncode != 0:
        raise RuntimeError(_yt_error_hint(r.stderr))
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 10240:
        raise RuntimeError("O download do YouTube saiu vazio.")


def run_youtube_thread1(job_id: str, url: str) -> None:
    """Thread 1 do fluxo YouTube: baixa o vídeo (estado 'downloading') e então cai no MESMO
    pipeline do upload (run_thread1: extrai áudio → S3 → dispara N8N). Mantém a máquina de
    estados; só adiciona 'downloading' antes de 'extracting_audio'."""
    job = db.get_job(job_id)
    try:
        _check_cancel(job_id)
        db.update_job(job_id, status="downloading", progress=8)
        # O download é um subprocess bloqueante de até 1h sem progresso intermediário; o heartbeat
        # mantém updated_at fresco pra o watchdog (JOB_STALE_MINUTES, 30min) NÃO marcar como stale um
        # download legítimo em andamento (o que liberaria a vaga e deixaria um 2º job pesado rodar em
        # paralelo). O heartbeat usa WHERE status='downloading', então um cancelamento não é ressuscitado.
        with _heartbeat(job_id, "downloading"):
            _download_youtube(url, job["local_path"], job_id)
        _check_cancel(job_id)
    except _Cancelled:
        log.info("[cancel] job %s cancelado no download; limpando disco", job_id)
        shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)
        return
    except Exception as e:
        _fail_job(job_id, str(e))
        return
    run_thread1(job_id)


@contextmanager
def _heartbeat(job_id: str, status: str, interval: float = _YT_HEARTBEAT_SECONDS):
    """Marca sinal de vida (updated_at) periodicamente durante uma etapa longa SEM progresso
    intermediário (download do YouTube, corte, reframe), até sair do bloco `with`. Sem isto, o
    watchdog (reap_stale_jobs, JOB_STALE_MINUTES=30min) pode ceifar um job VIVO quando um upload
    novo chega e dispara o reap. O `status` esperado no heartbeat_job garante que um cancelamento
    concorrente (status virou 'error') NÃO ressuscita o job."""
    stop = threading.Event()

    def _beat() -> None:
        while not stop.wait(interval):
            try:
                db.heartbeat_job(job_id, status)
            except Exception as e:
                log.warning("[heartbeat] job %s: %s", job_id, e)

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


def run_thread2(job_id: str) -> None:
    """Thread 2: cut clips → reframe (base SEM legenda) → sobe base + grava
    segmentos editáveis → mark done. A queima da legenda é sob demanda (na rota
    de vídeo), não aqui."""
    job = db.get_job(job_id)
    try:
        clips_data = _parse_clips_json(job.get("clips_json"))
        if not clips_data:
            raise RuntimeError("clips_json vazio ou inválido (nenhum clipe pra cortar).")
        job_clips_dir = os.path.join(config.JOB_DIR, job_id, "clips")
        os.makedirs(job_clips_dir, exist_ok=True)

        # Legenda como DADO editável: gravamos os segmentos sempre que houver
        # transcript (SRT), independente do toggle. O clipe sai SEM legenda (base
        # limpa); a queima acontece sob demanda a partir da base + dos segmentos.
        transcript = job.get("transcript")

        # Tempo por PALAVRA (output.word_timestamps do Whisper): o N8N descarta, então buscamos o
        # output cru do RunPod pelo runpod_job_id (já PATCHado pra cá) AQUI, no início da thread2
        # (segundos após a transcrição ficar pronta, dentro da janela curta de retenção do RunPod).
        # Serve pra ancorar a legenda na PALAVRA real (anti "adiantada") e guardar as palavras por
        # clipe pro editor. Best-effort: words=None cai no método por frase (segments_for_clip).
        words = None
        if transcript and job.get("runpod_job_id"):
            try:
                out = runpod_client.fetch_whisper_output(job["runpod_job_id"])
                wt = out.get("word_timestamps") if isinstance(out, dict) else None
                if isinstance(wt, list) and wt:
                    words = wt
                    log.info("[caption] %d palavras com timestamp obtidas do RunPod", len(wt))
                else:
                    log.info("[caption] sem word_timestamps no output do RunPod; legenda por frase")
            except Exception as e:
                log.warning("[caption] falha ao obter word_timestamps (%s); legenda por frase", e)

        # Auto-ajuste do corte pra não terminar/começar no meio da fala: usa o SRT pra
        # estender o fim até o fim do trecho falado (e puxar o início pro começo dele),
        # com teto. Roda ANTES do corte, então o ffmpeg, a legenda (que herda o `meta`)
        # e a duração usam os limites ajustados. Sem transcript, mantém tudo igual.
        if transcript:
            for clip in clips_data:
                try:
                    clip["start"], clip["end"] = captions.snap_clip_bounds(
                        transcript, clip["start"], clip["end"])
                except (KeyError, TypeError, ValueError):
                    continue

        _check_cancel(job_id)
        db.update_job(job_id, status="cutting", progress=70)
        _t_cut = time.monotonic()
        # Corte e reframe são etapas longas sem update intermediário: o heartbeat segura o watchdog
        # (JOB_STALE_MINUTES) pra não ceifar o job se um upload novo disparar o reap no meio.
        with _heartbeat(job_id, "cutting"):
            produced = _cut_clips(job["local_path"], clips_data, job_clips_dir, config.CLIP_HANDLE_SECONDS)
        log.info("[timing] corte: %d clipe(s) em %.1fs", len(produced), time.monotonic() - _t_cut)

        _check_cancel(job_id)
        db.update_job(job_id, status="reframing", progress=85)
        _t_reframe = time.monotonic()
        with _heartbeat(job_id, "reframing"):
            _reframe_clips(job_id, produced)   # GPU (fan-out) se ligado, senão CPU; fallback CPU por clipe
        log.info("[timing] reframe total: %d clipe(s) em %.1fs", len(produced), time.monotonic() - _t_reframe)

        _check_cancel(job_id)
        db.update_job(job_id, status="uploading_clips", progress=95)
        results = []
        H = config.CLIP_HANDLE_SECONDS
        for i, item in enumerate(produced):
            key = f"jobs/{job_id}/clips/clip_{i+1:02d}_base.mp4"
            # Sobe a base ANTES de qualquer cleanup: depois do rmtree é a única cópia que sobra e é
            # de onde a queima sob demanda parte. No caminho GPU o worker já subiu a base nessa MESMA
            # key (presigned PUT), então não re-sobe; só o caminho CPU (ou fallback) tem `final` local.
            if item.get("gpu_base_key"):
                pass
            else:
                _s3_upload_with_retry(item["final"], key)
            # Retem o corte 16:9 (fonte do crop por keyframe, BACKEND §33) se ainda nao foi retido pela
            # GPU. item["path"] (o corte local) existe ate o rmtree no fim; sobe pra key dedicada (TTL
            # via lifecycle). Best-effort: falha aqui nao derruba o job (o crop fica indisponivel).
            if not item.get("src169_retained"):
                try:
                    _s3_upload_with_retry(item["path"],
                                          f"{config.SOURCE16X9_PREFIX}{job_id}/clip_{i+1:02d}.mp4")
                except Exception as e:
                    log.warning("[src169] clip %d: falha ao reter o 16:9 (%s)", i + 1, e)
            clip_id = f"{job_id}:{i}"
            meta = item["meta"]
            win_start = float(item.get("win_start") or 0.0)
            # Com handles (H > 0), o base.mp4 é a JANELA: window_dur (duração real do base, via
            # ffprobe) cobre o clamp quando a janela passa do fim do vídeo, e os segmentos passam
            # a ser relativos ao início da janela (win_start). Sem handles, tudo é relativo ao
            # próprio corte (comportamento antigo). Se o ffprobe falhar, usa o fim nominal e não
            # persiste a janela (degrada pra "sem handles", sem quebrar a legenda).
            if H > 0:
                # GPU: a duração da janela vem do retorno do worker (não há arquivo local pra ffprobe).
                # CPU: probe do base local. Em ambos, window_dur None degrada pra "sem handles".
                window_dur = item.get("window_dur")
                if window_dur is None and item.get("final"):
                    window_dur = _probe_duration(item["final"])
                seg_start = win_start
                seg_end = (win_start + window_dur) if window_dur else (float(meta["end"]) + H)
            else:
                window_dur = None
                seg_start, seg_end = float(meta["start"]), float(meta["end"])
            # Grava os segmentos editáveis (clip_id "<job_id>:<index>", batendo com o id de
            # favoritos/_clip_objects). Falha aqui não derruba o job: dá pra re-derivar depois.
            if transcript:
                try:
                    # Legenda ancorada na PALAVRA real quando há word_timestamps (anti "adiantada");
                    # senão, método por frase (char-proporcional). Modo dual, sem regressão.
                    segs = captions.words_for_clip(words, transcript, seg_start, seg_end) if words else None
                    if not segs:
                        segs = captions.segments_for_clip(transcript, seg_start, seg_end)
                    if segs:
                        db.set_clip_captions(clip_id, json.dumps(segs, ensure_ascii=False))
                        # Guarda as palavras CRUAS do clipe (relativas à janela) pro editor word-level.
                        if words:
                            cw = captions.clip_words_window(words, seg_start, seg_end)
                            if cw:
                                db.set_clip_words(clip_id, json.dumps(cw, ensure_ascii=False))
                except Exception as e:
                    log.warning("[caption] clip %d: falha ao gravar segmentos (%s)", i + 1, e)
            # Persiste a janela/corte só quando há handles e o ffprobe deu a duração real. O
            # in/out inicial é o corte que o Claude/snap decidiu, situado dentro da janela.
            if H > 0 and window_dur:
                try:
                    start, end = float(meta["start"]), float(meta["end"])
                    in_pt = max(0.0, round(start - win_start, 3))
                    out_pt = round(min(end - win_start, window_dur), 3)
                    if out_pt > in_pt:
                        db.set_clip_window(clip_id, round(win_start, 3),
                                           round(window_dur, 3), in_pt, out_pt)
                except Exception as e:
                    log.warning("[window] clip %d: falha ao gravar a janela (%s)", i + 1, e)
            results.append(_clip_result(key, meta))

        # Thumbnail do card: frame do melhor clipe, ANTES do rmtree (arquivos ainda em
        # disco). Best-effort: se falhar, thumbnail_key fica None e o card usa o fallback.
        thumbnail_key = _generate_thumbnail(job_id, produced)

        # result_urls guarda objetos {key, title, score, reason, duration}. A key
        # agora aponta pra base SEM legenda (clip_NN_base.mp4). O /api/status monta
        # o Clip a partir disso. (Jobs antigos têm a chave do clipe já queimado.)
        _check_cancel(job_id)
        db.update_job(job_id, status="done", progress=100, result_urls=json.dumps(results),
                      error=None, thumbnail_key=thumbnail_key)

        # Cleanup SÓ AQUI: base no S3 + segmentos no banco + done. Nunca antes (a
        # queima sob demanda depende da base no S3 e dos segmentos, não do disco).
        shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)

    except _Cancelled:
        log.info("[cancel] job %s cancelado; encerrando thread2 e limpando disco", job_id)
        shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)
    except Exception as e:
        _fail_job(job_id, str(e))


# ─── Private helpers ─────────────────────────────────────────────────────────

def _parse_clips_json(raw):
    """Parse tolerante do clips_json vindo do Claude (via N8N). Aceita None, lista já
    pronta, ou string com/sem cerca ```json. Nunca levanta: devolve [] no que não der
    pra interpretar (o chamador trata lista vazia como erro amigável)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return []
    text = raw.strip()
    if text.startswith("```"):
        # Pega o miolo da primeira cerca; se vier malformada, cai no parse direto.
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _best_clip(produced: list):
    """Clipe de maior score (best-effort: score ausente/inválido conta como -inf).
    Função pura pra ser testável sem ffmpeg."""
    if not produced:
        return None

    def _score(it):
        try:
            return float((it.get("meta") or {}).get("score"))
        except (TypeError, ValueError):
            return float("-inf")

    return max(produced, key=_score)


def _probe_duration(path: str):
    """Duração (segundos, float) de um arquivo de vídeo via ffprobe, ou None se falhar.
    Best-effort: usado pra saber a duração real da janela cortada (que pode ser menor que o
    nominal quando o fim passa do vídeo). Arquivo curto (a janela): ffprobe é barato."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    try:
        d = float((r.stdout or "").strip())
    except (TypeError, ValueError):
        return None
    return d if math.isfinite(d) and d > 0 else None


def _reframe_clips(job_id: str, produced: list) -> None:
    """Reenquadra cada clipe pra 9:16. Anota em cada item: `final` (path local da base, ou None se a
    GPU já subiu a base pro S3), `gpu_base_key` (key S3 da base feita na GPU, ou None) e `window_dur`
    (duração da janela vinda do worker GPU, ou None -> ffprobe depois). Com REFRAME_GPU_ENABLED, faz
    o reframe na GPU (RunPod serverless, em paralelo) e cai pra CPU por clipe que falhar; senão roda
    tudo na CPU (comportamento antigo)."""
    from reframer import reframe_clip
    for item in produced:
        item["final"] = None
        item["gpu_base_key"] = None
        item["window_dur"] = None

    gpu_ok = _reframe_clips_gpu(job_id, produced) if config.REFRAME_GPU_ENABLED else [False] * len(produced)

    for i, item in enumerate(produced):
        if gpu_ok[i]:
            continue                                    # base já no S3 (GPU); nada a fazer na CPU
        out_path = item["path"].replace(".mp4", "_base.mp4")
        _t = time.monotonic()
        try:
            reframe_clip(item["path"], out_path)        # base 9x16 SEM legenda
        except Exception as e:
            log.warning("[reframe] clip %d falhou (%s), usando center crop", i + 1, e)
            _center_crop_fallback(item["path"], out_path)
        log.info("[timing] reframe clip %d (cpu): %.1fs", i + 1, time.monotonic() - _t)
        item["final"] = out_path


def _reframe_clips_gpu(job_id: str, produced: list) -> list:
    """Reframe na GPU (RunPod serverless) com FAN-OUT: sobe os cortes pro S3, dispara todos os jobs
    e faz poll de cada um. O worker baixa o corte, reenquadra (NVENC) e sobe a base direto na key
    final via presigned PUT, devolvendo a duração. Devolve uma lista de bool (True = a GPU fez aquele
    clipe; False = cai no fallback de CPU). Nunca levanta: qualquer falha vira fallback."""
    n = len(produced)
    ok = [False] * n
    params = {
        "deadzone_frac": config.REFRAME_DEADZONE_FRAC,
        "max_pan_frac": config.REFRAME_MAX_PAN_FRAC,
        "min_conf": config.REFRAME_MIN_CONF,
        "detect_cuts": config.REFRAME_CUT_DETECT,
    }
    ct = config.REFRAME_PUT_CONTENT_TYPE
    url_expiry = config.REFRAME_GPU_TIMEOUT + 600       # URLs vivem mais que fila + cold start + job
    t0 = time.monotonic()
    submitted = []     # (i, rp_job_id, base_key)
    # 1) sobe os cortes e submete todos (fan-out). O corte 16:9 e RETIDO (fonte do crop por keyframe,
    # BACKEND §33): em vez de subir pra uma key temporaria e deletar, sobe direto pra key de retencao
    # (lifecycle/TTL) e o worker le DELA. Zero upload extra; src169_retained evita o re-upload no loop
    # do run_thread2.
    for i, item in enumerate(produced):
        base_key = f"jobs/{job_id}/clips/clip_{i+1:02d}_base.mp4"
        src_key = f"{config.SOURCE16X9_PREFIX}{job_id}/clip_{i+1:02d}.mp4"
        try:
            s3_client.upload_file(item["path"], src_key)
            item["src169_retained"] = True
            get_url = s3_client.get_presigned_url(src_key, expiry_seconds=url_expiry)
            put_url = s3_client.get_presigned_put_url(base_key, ct, expiry_seconds=url_expiry)
            rp_id = runpod_client.submit_reframe(get_url, put_url, ct, params, config.REFRAME_GPU_TIMEOUT)
            submitted.append((i, rp_id, base_key))
        except Exception as e:
            log.warning("[reframe-gpu] clip %d: falha ao submeter (%s); fallback CPU", i + 1, e)
    # 2) poll de cada job (eles rodam em paralelo na RunPod; o 1o poll espera o mais lento)
    for i, rp_id, base_key in submitted:
        try:
            out = runpod_client.poll(rp_id, config.REFRAME_GPU_TIMEOUT)
            dur = out.get("duration")
            produced[i]["window_dur"] = float(dur) if dur else None
            produced[i]["gpu_base_key"] = base_key      # base já está no S3 nessa key
            ok[i] = True
        except Exception as e:
            log.warning("[reframe-gpu] clip %d: %s; fallback CPU", i + 1, e)
    # 3) o corte 16:9 NAO e mais deletado: fica retido como fonte do crop por keyframe (TTL via
    # lifecycle do bucket; limpo no delete do projeto). Nao ha mais temporario pra apagar aqui.
    log.info("[timing] reframe GPU: %d/%d clipe(s) na GPU em %.1fs", sum(ok), n, time.monotonic() - t0)
    return ok


def _generate_thumbnail(job_id: str, produced: list):
    """Best-effort: extrai 1 frame do clipe de maior score (no aspecto ORIGINAL do
    corte, item['path'], pra casar com o card 16/10, não o _base.mp4 9:16), sobe em
    jobs/{job_id}/thumb.jpg e devolve a key (ou None). NUNCA levanta: falhar aqui não
    pode derrubar o job (o card cai no fallback de onda). Roda ANTES do rmtree, com os
    arquivos ainda em disco."""
    best = _best_clip(produced)
    src = best.get("path") if best else None
    if not src or not os.path.exists(src):
        return None
    # Com handles, src (item['path']) é a JANELA: o corte oficial começa ~handle segundos
    # adiante, então -ss 1 cairia na sobra-cabeça. Pega 1s DENTRO do corte (start - win_start).
    ss = 1.0
    try:
        win_start = float(best.get("win_start") or 0.0)
        ss = max(0.0, float(best["meta"]["start"]) - win_start) + 1.0
    except (KeyError, TypeError, ValueError):
        ss = 1.0
    thumb_path = os.path.join(config.JOB_DIR, job_id, "thumb.jpg")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{ss:.3f}", "-i", src,
             "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", thumb_path],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0 or not os.path.exists(thumb_path) or os.path.getsize(thumb_path) < 256:
            log.warning("[thumb] job %s: ffmpeg não gerou thumbnail (%s)", job_id, r.stderr[-200:])
            return None
        key = f"jobs/{job_id}/thumb.jpg"
        _s3_upload_with_retry(thumb_path, key)
        return key
    except Exception as e:
        log.warning("[thumb] job %s: falha ao gerar/subir thumbnail (%s)", job_id, e)
        return None


def _clip_result(key: str, meta: dict) -> dict:
    """Monta a entrada salva em result_urls: a chave S3 (base) + o metadata do Claude."""
    try:
        duration = int(round(float(meta.get("end")) - float(meta.get("start"))))
    except (TypeError, ValueError):
        duration = None
    return {
        "key": key,
        "title": meta.get("title"),
        "score": meta.get("score"),
        "reason": meta.get("reason"),
        "duration": duration,
    }


def _extract_audio(video_path: str, job_id: str) -> str:
    audio_path = os.path.join(config.JOB_DIR, job_id, "audio.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-acodec", "libmp3lame", "-q:a", "4", audio_path],
        capture_output=True, text=True, timeout=_FFMPEG_AUDIO_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr[-400:]}")
    return audio_path


def _s3_upload_with_retry(local_path: str, s3_key: str, retries: int = 3) -> None:
    for attempt in range(retries):
        try:
            s3_client.upload_file(local_path, s3_key)
            return
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"S3 upload failed after {retries} attempts: {e}")
            time.sleep(2 ** attempt)


def _trigger_n8n_with_retry(
    job_id: str, audio_s3_url: str, min_duration: int, max_duration: int, retries: int = 3
) -> None:
    payload = {
        "job_id": job_id,
        "audio_s3_url": audio_s3_url,
        "min_duration": min_duration,
        "max_duration": max_duration,
    }
    for attempt in range(retries):
        try:
            # (connect, read): N8N morto/inacessível falha em 10s no connect em vez de
            # pendurar 600s; o read longo cobre o tempo do workflow (RunPod + Claude).
            resp = requests.post(config.N8N_WEBHOOK_URL, json=payload, timeout=(10, 600))
            resp.raise_for_status()
            return
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"N8N trigger failed after {retries} attempts: {e}")
            time.sleep(2 ** attempt)


def _cut_clips(video_path: str, clips_data: list, output_dir: str, handle: float = 0.0) -> list:
    """Corta cada clipe e devolve, por clipe efetivamente produzido, {path, meta, win_start}.
    Carrega o metadata original (title/score/reason) junto, pra não depender do índice (clipes
    < 5s ou que falham são pulados e quebrariam o casamento).

    Com handle > 0, corta a JANELA [start-handle, end+handle] (clamped a >= 0; o ffmpeg corta
    no que existe se o fim passar do vídeo) em vez do corte exato: o base.mp4 vira a janela e o
    editor abre/fecha o corte dentro da sobra. meta mantém o start/end ORIGINAIS (título/score/
    duração do card não mudam); win_start situa o corte dentro da janela. handle=0 = corte exato
    (comportamento antigo, sem sobra)."""
    produced = []
    idx = 0
    for n, clip in enumerate(clips_data):
        # Valida start/end antes de chamar o ffmpeg: um item malformado do scoring
        # (faltando campo, NaN, end<=start, negativo) é pulado, não derruba o job.
        try:
            start = float(clip["start"])
            end = float(clip["end"])
        except (KeyError, TypeError, ValueError):
            log.warning("[cut] clip %d ignorado: start/end ausente ou inválido", n + 1)
            continue
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
            log.warning("[cut] clip %d ignorado: intervalo inválido (%s -> %s)", n + 1, start, end)
            continue
        if end - start < 5:
            log.info("[cut] clip %d ignorado: duração %.1fs muito curta", n + 1, end - start)
            continue
        idx += 1
        win_start = max(0.0, start - handle) if handle > 0 else start
        win_end = (end + handle) if handle > 0 else end
        out_path = os.path.join(output_dir, f"clip_{idx:02d}.mp4")
        try:
            result = subprocess.run(
                # INPUT seek: -ss ANTES do -i pula direto pro ponto do clipe via índice, em vez de
                # decodificar desde o segundo zero (era o gargalo de ~40min em fonte longa: o clipe
                # no min 90 decodificava 90 min só pra começar). Com re-encode, o corte continua
                # frame-accurate e win_start = o pedido (NÃO mexe na matemática dos handles, §18).
                # -t (duração) em vez de -to (que com input-seek seria ambíguo). nice + -threads:
                # cede CPU pro site durante o corte (não trava o web app).
                ["ffmpeg", "-y", "-ss", f"{win_start:.3f}", "-i", video_path,
                 "-t", f"{win_end - win_start:.3f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                 "-avoid_negative_ts", "make_zero",
                 "-threads", str(config.BURN_FFMPEG_THREADS),
                 "-movflags", "+faststart", out_path],
                capture_output=True, text=True, timeout=_FFMPEG_CUT_TIMEOUT,
                preexec_fn=lambda: os.nice(config.BURN_NICE),
            )
        except subprocess.TimeoutExpired:
            log.error("[cut] clip %d estourou o timeout (%ss); pulando", n + 1, _FFMPEG_CUT_TIMEOUT)
            continue
        if result.returncode == 0 and os.path.exists(out_path):
            produced.append({"path": out_path, "meta": clip, "win_start": win_start})
        else:
            log.warning("[cut] clip %d falhou: %s", n + 1, result.stderr[:200])
    return produced


def _center_crop_fallback(input_path: str, output_path: str, subtitles: str | None = None) -> None:
    vf = "crop=iw*9/16:ih:(iw-iw*9/16)/2:0,scale=1080:1920"
    if subtitles:
        from reframer import _filter_escape
        vf += f",subtitles={_filter_escape(subtitles)}"
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
         "-movflags", "+faststart", output_path],
        capture_output=True, check=True, timeout=_FFMPEG_CUT_TIMEOUT,
    )


def _fail_job(job_id: str, error_msg: str) -> None:
    db.update_job(job_id, status="error", error=error_msg)
    shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)
