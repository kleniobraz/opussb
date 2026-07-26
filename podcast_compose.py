"""Montagem do podcast (§39 Parte 5): GANCHO (recorte do próprio episódio) + VINHETA
(biblioteca) + PODCAST completo, export 16:9 no fps e na resolução NATIVOS do episódio.

Técnica validada no teste de VFR de 2026-07-06: re-encoda SÓ os pedaços pequenos (gancho
e vinheta) casando a assinatura A/V COMPLETA do corpo (fps, timescale, áudio, profile/
level/SAR, lida POR ARQUIVO: os exports do estúdio variam entre si), e concat demuxer com
-c copy pro corpo de 2h, que NUNCA é re-encodado. Vinheta sem trilha de áudio (caso real
da Carbone) ganha silêncio via anullsrc, senão o concat quebra por contagem de streams.

Módulo deliberadamente leve (sem processor/reframer/YOLO), no padrão do hooks.py: roda
numa thread disparada pelo POST /api/podcast/<id>/compose. A tela final 9:16 dos clipes
usa os mesmos fundamentos, então `probe_av_signature` e `concat_copy` moraram aqui e o
app.py importa (a probe saiu de lá por alias; comportamento preservado)."""
import json
import logging
import os
import re
import shutil
import subprocess
import threading

import config
import db
import podcast_ingest
import podcast_audio
import s3_client

log = logging.getLogger("opussb.compose")

_PROBE_TIMEOUT = 30

# Teto pra pegar o slot de extração de áudio no fim da montagem. Não é env-tunável de
# propósito (mesmo espírito do _HEARTBEAT_INTERVAL do podcast_audio): o que importa é não
# ser infinito, porque enquanto a montagem espera o slot ela segura o vídeo fonte no disco.
# Estourar aqui não perde nada: o áudio vira 'error' e o usuário tem "Tentar de novo".
_AUDIO_SLOT_TIMEOUT = 60


def probe_av_signature(path: str, require_audio: bool = True):
    """Assinatura A/V pra casar pedaços re-encodados com um corpo em -c copy. Devolve os
    4 campos históricos da tela final ({fps_s, timescale, ar, ac}) + os estendidos da
    montagem (profile, level, sar, width, height, pix_fmt, has_audio, nb_v, nb_a), ou
    None sem vídeo legível. Com require_audio (default, o contrato da tela final), sem
    áudio devolve None; com False, ar/ac caem em 48000/2 e has_audio=False (o chamador
    decide injetar anullsrc ou sair sem trilha)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,r_frame_rate,time_base,profile,level,"
             "sample_aspect_ratio,width,height,pix_fmt,sample_rate,channels",
             "-of", "json", path],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
        streams = (json.loads(r.stdout) if r.returncode == 0 and r.stdout else {}).get("streams") or []
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None
    vids = [s for s in streams if s.get("codec_type") == "video"]
    auds = [s for s in streams if s.get("codec_type") == "audio"]
    if not vids or (require_audio and not auds):
        return None
    v, a = vids[0], (auds[0] if auds else {})
    fps_s = v.get("r_frame_rate") or ""
    if "/" not in fps_s or fps_s.startswith("0/"):
        return None
    tb = v.get("time_base") or ""
    timescale = tb.split("/")[-1] if "/" in tb else "15360"
    try:
        return {
            "fps_s": fps_s,
            "timescale": int(timescale),
            "ar": int(a.get("sample_rate") or 48000),
            "ac": int(a.get("channels") or 2),
            "has_audio": bool(auds),
            "profile": (v.get("profile") or "").strip(),
            "level": v.get("level"),
            "sar": v.get("sample_aspect_ratio") or "1:1",
            "width": int(v["width"]),
            "height": int(v["height"]),
            "pix_fmt": v.get("pix_fmt") or "yuv420p",
            "nb_v": len(vids),
            "nb_a": len(auds),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _x264_profile(profile) -> str | None:
    """Profile do ffprobe -> flag do x264, só pros perfis 8-bit que a casa usa; fora
    disso devolve None e o encode segue sem a flag (melhor que falhar o x264)."""
    p = (profile or "").strip().lower()
    if p == "constrained baseline":
        p = "baseline"
    return p if p in ("baseline", "main", "high") else None


def _level_str(level) -> str | None:
    """Level do ffprobe (int, ex.: 41) -> "4.1" do x264; fora da faixa conhecida, None."""
    if isinstance(level, int) and 10 <= level <= 62:
        return f"{level // 10}.{level % 10}"
    return None


def build_matched_cmd(src: str, out: str, sig: dict, *, dur: float = None,
                      seek: tuple = None, silence: bool = False, threads: int = 2) -> list:
    """Comando (puro, testável) que re-encoda um pedaço CASANDO a assinatura do corpo, a
    premissa do concat-copy. seek=(start, dur) recorta do próprio src (gancho; -ss antes
    do -i + re-encode = corte exato). silence injeta anullsrc quando o src não tem áudio
    e o corpo tem (vinheta da Carbone). Corpo sem áudio: pedaço sai só com vídeo."""
    cmd = ["ffmpeg", "-y"]
    if seek:
        cmd += ["-ss", f"{seek[0]:.3f}"]
    cmd += ["-i", src]
    if silence:
        layout = "stereo" if sig["ac"] >= 2 else "mono"
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r={sig['ar']}:cl={layout}"]
    if seek:
        cmd += ["-t", f"{seek[1]:.3f}"]
    elif dur and dur > 0:
        cmd += ["-t", f"{float(dur):.3f}"]
    cmd += ["-map", "0:v:0"]
    if sig["has_audio"]:
        cmd += ["-map", "1:a:0" if silence else "0:a:0"]
    sar = re.sub(r"[^0-9:]", "", sig.get("sar") or "") or "1:1"
    cmd += ["-r", sig["fps_s"],
            "-vf", f"scale={sig['width']}:{sig['height']}:force_original_aspect_ratio=decrease,"
                   f"pad={sig['width']}:{sig['height']}:(ow-iw)/2:(oh-ih)/2,"
                   f"setsar={sar.replace(':', '/')}",
            "-video_track_timescale", str(sig["timescale"]),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", sig.get("pix_fmt") or "yuv420p"]
    prof = _x264_profile(sig.get("profile"))
    if prof:
        cmd += ["-profile:v", prof]
    lvl = _level_str(sig.get("level"))
    if lvl:
        cmd += ["-level:v", lvl]
    if sig["has_audio"]:
        cmd += ["-c:a", "aac", "-ar", str(sig["ar"]), "-ac", str(sig["ac"])]
    if silence:
        cmd += ["-shortest"]
    cmd += ["-threads", str(threads), "-movflags", "+faststart", out]
    return cmd


def build_concat_cmd(list_path: str, out: str) -> list:
    """Concat demuxer com -c copy (puro, testável): é o que garante que o corpo grande
    NUNCA é re-encodado. Só funciona com pedaços casados (build_matched_cmd)."""
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", "-movflags", "+faststart", out]


def concat_copy(paths: list, out_path: str, timeout: int, nice: int = 0) -> bool:
    """Concatena N arquivos JÁ CASADOS por concat-copy + faststart. Retorna True/False
    (o chamador decide degradar ou falhar). Usado pela tela final 9:16 e pela montagem."""
    list_path = out_path + ".list.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '" + p.replace("'", r"'\''") + "'\n")
    try:
        r = subprocess.run(build_concat_cmd(list_path, out_path), capture_output=True,
                           text=True, timeout=timeout,
                           preexec_fn=(lambda: os.nice(nice)) if nice else None)
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        log.warning("[concat] falhou: %s", (r.stderr or "")[-300:])
        return False
    return True


def duration(path: str):
    """Duração (segundos) via ffprobe, ou None sem conseguir ler. Pública: usada pela
    montagem e pelo corte de início (podcast_cut.py), não duplica a lógica em cada um."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None


def run_ffmpeg(cmd: list, nice: int, etapa: str, timeout: int | None = None) -> None:
    """Roda um ffmpeg e valida o resultado (saída existe e não é lixo pequeno). Pública:
    usada pela montagem (timeout default = COMPOSE_TIMEOUT) e pelo corte de início, que
    passa seu próprio timeout dedicado (etapas com pesos de I/O bem diferentes)."""
    to = timeout if timeout is not None else config.COMPOSE_TIMEOUT
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=to,
                       preexec_fn=lambda: os.nice(nice))
    out = cmd[-1]
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1024:
        raise RuntimeError(f"{etapa}: ffmpeg falhou ({(r.stderr or '')[-300:]})")


def _sig_tag(sig: dict) -> str:
    """Assinatura -> sufixo de cache S3 do matched da vinheta. Inclui TODOS os params que
    entram no encode (diferente da tela final, que casa menos campos)."""
    bruto = (f"{sig['fps_s']}_{sig['timescale']}_{sig['ar']}_{sig['ac']}_"
             f"{sig.get('profile')}_{sig.get('level')}_{sig['width']}x{sig['height']}_"
             f"{sig.get('pix_fmt')}_{'a' if sig['has_audio'] else 'mudo'}")
    return re.sub(r"[^A-Za-z0-9x_.-]", "-", bruto)


def _ensure_matched_vinheta(vinheta: dict, sig: dict, workdir: str, threads: int, nice: int) -> str:
    """Vinheta re-encodada pros params do corpo (pillarbox se a proporção for outra),
    cacheada no S3 por assinatura completa: episódios do mesmo export reusam de graça."""
    matched_key = config.VINHETA_PREFIX + f"_matched/{vinheta['id']}_{_sig_tag(sig)}.mp4"
    matched = os.path.join(workdir, "vinheta_matched.mp4")
    if s3_client.exists(matched_key):
        s3_client.download_file(matched_key, matched)
        return matched
    src = os.path.join(workdir, "vinheta_src.mp4")
    s3_client.download_file(vinheta["s3_key"], src)
    vsig = probe_av_signature(src, require_audio=False)
    silence = bool(sig["has_audio"]) and not (vsig and vsig["has_audio"])
    run_ffmpeg(build_matched_cmd(src, matched, sig, silence=silence, threads=threads),
               nice, "casamento da vinheta")
    try:
        s3_client.upload_file(matched, matched_key)
    except Exception as e:
        log.warning("[compose] cache do matched da vinheta falhou: %s", e)
    return matched


def _upload_com_heartbeat(path: str, key: str, job_id: str) -> None:
    """Upload do final com sinal de vida: 8-16GB podem levar minutos e o watchdog (30min)
    não pode ceifar montagem legítima. O heartbeat só renova se o status seguir
    'montando' (um cancelamento concorrente não é ressuscitado)."""
    pare = threading.Event()

    def _bate():
        while not pare.wait(60):
            db.heartbeat_job(job_id, "montando")

    t = threading.Thread(target=_bate, daemon=True)
    t.start()
    try:
        s3_client.upload_file(path, key)
    finally:
        pare.set()


class _Cancelled(Exception):
    """Sinaliza que o job foi cancelado externamente (status virou 'error'). Cópia local
    do padrão de processor.py: os dois módulos são deliberadamente independentes (ver
    docstring do topo do arquivo), então a checagem não é importada, é replicada."""


def _check_cancel(job_id: str) -> None:
    """Aborta cooperativamente se o job sumiu ou foi marcado como error (cancelado).
    Chamado entre etapas pra a thread parar e não sobrescrever o cancelamento."""
    j = db.get_job(job_id)
    if j is None or j["status"] == "error":
        raise _Cancelled()


def _compose(job_id: str, job: dict, workdir: str) -> None:
    # Materializa o fonte se ele ainda estiver só no S3 (upload direto). Idempotente e
    # DENTRO da thread: baixar 10GB num request prenderia uma das 4 threads do gunicorn.
    src = podcast_ingest.garantir_fonte_local(job)
    try:
        choice = json.loads(job.get("hook_choice") or "null")
    except ValueError:
        choice = None
    if not isinstance(choice, dict):
        raise RuntimeError("job sem gancho aprovado (hook_choice vazio)")
    hook_start, hook_end = float(choice["start"]), float(choice["end"])
    vinheta = db.get_vinheta(job.get("vinheta_id") or "")
    if not vinheta or not vinheta.get("s3_key"):
        raise RuntimeError("a vinheta do projeto não existe mais na biblioteca")

    src_bytes = os.path.getsize(src)
    livre = shutil.disk_usage(os.path.dirname(src)).free
    preciso = int(src_bytes * config.COMPOSE_DISK_FACTOR)
    if livre < preciso:
        raise RuntimeError(f"disco insuficiente: livre {livre / 2**30:.1f}GB, "
                           f"necessário ~{preciso / 2**30:.1f}GB")

    sig = probe_av_signature(src, require_audio=False)
    if not sig:
        raise RuntimeError("não consegui ler a assinatura A/V do fonte")
    os.makedirs(workdir, exist_ok=True)
    nice, threads = config.BURN_NICE, config.BURN_FFMPEG_THREADS

    _check_cancel(job_id)
    db.update_job(job_id, progress=20)
    gancho = os.path.join(workdir, "gancho.mp4")
    run_ffmpeg(build_matched_cmd(src, gancho, sig, seek=(hook_start, hook_end - hook_start),
                                 threads=threads), nice, "recorte do gancho")

    _check_cancel(job_id)
    db.update_job(job_id, progress=45)
    vin_matched = _ensure_matched_vinheta(vinheta, sig, workdir, threads, nice)

    corpo = src
    if sig["nb_v"] > 1 or sig["nb_a"] > 1:
        # Trilha extra (cover art, áudio secundário) quebraria o concat por contagem de
        # streams: remux -c copy só com v:0 (+a:0), segundos de I/O, sem re-encode.
        _check_cancel(job_id)
        db.update_job(job_id, progress=55)
        corpo = os.path.join(workdir, "corpo_remux.mp4")
        cmd = ["ffmpeg", "-y", "-i", src, "-map", "0:v:0"]
        if sig["has_audio"]:
            cmd += ["-map", "0:a:0"]
        run_ffmpeg(cmd + ["-c", "copy", corpo], nice, "remux do corpo (trilhas extras)")

    _check_cancel(job_id)
    db.update_job(job_id, progress=65)
    final_local = os.path.join(workdir, "final.mp4")
    if not concat_copy([gancho, vin_matched, corpo], final_local,
                       timeout=config.COMPOSE_TIMEOUT, nice=nice):
        raise RuntimeError("concat final falhou")

    # Sanidade da emenda: uma junção corrompida some ou duplica tempo; duração total tem
    # que bater com a soma das partes.
    esperado = ((hook_end - hook_start) + (duration(vin_matched) or 0)
                + (duration(corpo) or 0))
    total = duration(final_local) or 0
    if abs(total - esperado) > 2.0:
        raise RuntimeError(f"duração do final ({total:.1f}s) não bate com a soma "
                           f"das partes ({esperado:.1f}s)")

    _check_cancel(job_id)
    db.update_job(job_id, progress=85)
    final_key = f"jobs/{job_id}/podcast/final.mp4"
    _upload_com_heartbeat(final_local, final_key, job_id)

    _check_cancel(job_id)
    # 'extraindo' entra no MESMO update do 'done', nunca depois: o front faz polling de 3s e
    # mostra o botão "Baixar áudio (MP3)" assim que vê done com áudio vazio. Num update
    # separado existiria a janela pro usuário clicar e disparar o FALLBACK (download do
    # episódio inteiro de volta do S3) em paralelo com a extração local que já está rodando,
    # que é exatamente o que esta mudança existe pra eliminar. Atômico, sem janela.
    extrair = config.PODCAST_AUDIO_ENABLED
    db.update_job(job_id, podcast_final_key=final_key, status="done", progress=100,
                  **({"podcast_audio_status": "extraindo"} if extrair else {}))
    log.info("[compose] job %s: final de %.1fs montado e no S3 (%s)", job_id, total, final_key)

    # A FONTE morre aqui, e não só no rmtree do fim: ela é o maior arquivo do job (dezenas
    # de GB) e a extração de MP3 abaixo usa o final_local, nunca ela. Apagar já encolhe a
    # janela de exposição: se um restart matar a thread no meio da extração, fica órfão o
    # workdir (tamanho do final.mp4) em vez de fonte + final. Regra 7 respeitada: o final
    # já está confirmado no S3 e no banco nas duas linhas acima.
    try:
        os.remove(src)
    except OSError as e:
        log.warning("[compose] job %s: não consegui apagar a fonte agora (%s); "
                    "o rmtree do fim cobre", job_id, e)

    # E a cópia no S3 (upload direto) morre junto, pelo mesmo motivo: sem isto cada episódio
    # deixaria dezenas de GB no bucket PAGO pra sempre, e nada os ceifaria (o lifecycle de 7
    # dias só coleta multipart incompleto, não objeto montado). A coluna é limpa na mesma
    # tacada porque _podcast_tem_fonte confia nela: deixá-la apontando pra key apagada faria
    # o /source responder 302 pra um 404.
    if job.get("podcast_source_key"):
        # Coluna limpa ANTES do delete, de propósito: na ordem inversa, um delete que passa
        # seguido de um update que falha (SQLite locked) deixaria a coluna apontando pra key
        # apagada, e aí _podcast_tem_fonte mente e o /source responde 302 pra um 404.
        db.update_job(job_id, podcast_source_key=None)
        try:
            s3_client.delete_object(job["podcast_source_key"])
        except Exception as e:
            log.warning("[compose] job %s: fonte segue no S3 (%s); o delete do projeto cobre",
                        job_id, e)

    # MP3 aqui, com o final.mp4 AINDA local: o caminho antigo (rota sob demanda) baixava os
    # mesmos GB de volta do S3 só pra extrair uns 100MB de áudio, e esse download é o maior
    # consumidor de rede do sistema. Best-effort de propósito: o episódio é o entregável, o
    # MP3 é acessório, então NADA aqui pode derrubar a montagem. Sem _check_cancel: o job já
    # está 'done' e um _Cancelled cairia no handler que só limpa o workdir, deixando o vídeo
    # fonte (dezenas de GB) órfão no disco pra sempre.
    # O try/finally é o que garante a regra 7 mesmo se ALGO aqui explodir. Sem ele, uma
    # exceção no próprio db.update_job de baixo (SQLite 'database is locked' depois do
    # busy_timeout, erro de I/O) subiria pro handler de run_podcast_compose, que gravaria
    # status='error' num episódio JÁ 'done' e com o final.mp4 confirmado no S3: o usuário
    # perderia acesso a um entregável que existe, veria "montagem falhou" com mensagem de
    # banco, e o rmtree nunca rodaria (fonte de dezenas de GB órfão travando a próxima
    # montagem no guard-rail de disco). O finally fecha os dois buracos de uma vez.
    try:
        if not extrair:
            log.info("[compose] job %s: extração de MP3 pulada (PODCAST_AUDIO_ENABLED=false); "
                     "o áudio segue disponível sob demanda quando a flag voltar", job_id)
        else:
            try:
                audio_key = podcast_audio.extrair_e_publicar(
                    job_id, final_local, workdir, slot_timeout=_AUDIO_SLOT_TIMEOUT)
                db.update_job(job_id, podcast_audio_status="done", podcast_audio_key=audio_key)
                log.info("[compose] job %s: MP3 extraído na montagem, sem round-trip (%s)",
                         job_id, audio_key)
            except Exception as e:
                # 'error' (e não voltar pra vazio) porque o front já sabe renderizar isso como
                # "Falha ao extrair o áudio" + botão "Tentar de novo", que dispara o fallback
                # SÓ por ação do usuário. Zero retry automático de propósito.
                log.warning("[compose] job %s: MP3 falhou na montagem (%s); episódio entregue "
                            "normalmente, áudio fica sob demanda", job_id, e)
                db.update_job(job_id, podcast_audio_status="error", podcast_audio_error=str(e))
    except Exception as e:
        # Rede de segurança do próprio tratamento de erro: nada depois do 'done' pode
        # rebaixar o episódio. Só loga; o áudio fica no estado em que deu pra gravar.
        log.warning("[compose] job %s: falha ao registrar o estado do MP3 (%s); episódio "
                    "segue entregue", job_id, e)
    finally:
        # Regra 7 da casa: a fonte só morre DEPOIS do final confirmado no S3 (upload + DB
        # acima), e morre SEMPRE, qualquer que tenha sido o desfecho do áudio.
        shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)


def run_podcast_compose(job_id: str) -> None:
    """Thread da montagem (POST /api/podcast/<id>/compose). Falha vira status='error' e
    LIBERA o disco do workdir, mas PRESERVA a fonte: retry = re-POST do compose."""
    job = db.get_job(job_id)
    if not job or job.get("job_type") != "podcast":
        return
    workdir = os.path.join(config.JOB_DIR, job_id, "compose")
    try:
        _compose(job_id, job, workdir)
    except _Cancelled:
        # Quem cancelou (POST /api/jobs/<id>/cancel) já gravou status="error" com a
        # mensagem "Cancelado pelo usuário."; aqui só limpa o workdir da montagem e
        # NÃO toca status/error (senão sobrescreveria a mensagem original). A fonte
        # (job["local_path"]) fica intacta, igual o caminho de erro genérico abaixo.
        log.info("[cancel] job %s cancelado durante a montagem; encerrando e limpando workdir", job_id)
        shutil.rmtree(workdir, ignore_errors=True)
    except Exception as e:
        log.warning("[compose] job %s falhou: %s", job_id, e)
        db.update_job(job_id, status="error", error=f"montagem falhou: {e}")
        shutil.rmtree(workdir, ignore_errors=True)
