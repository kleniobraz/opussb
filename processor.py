import os
import json
import math
import logging
import shutil
import subprocess
import time
import requests
import config
import db
import s3_client
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

        _check_cancel(job_id)
        db.update_job(job_id, status="cutting", progress=70)
        produced = _cut_clips(job["local_path"], clips_data, job_clips_dir)

        _check_cancel(job_id)
        db.update_job(job_id, status="reframing", progress=85)
        from reframer import reframe_clip
        for i, item in enumerate(produced):
            out_path = item["path"].replace(".mp4", "_base.mp4")
            try:
                reframe_clip(item["path"], out_path)            # base 9x16 SEM legenda
            except Exception as e:
                log.warning("[reframe] clip %d falhou (%s), usando center crop", i + 1, e)
                _center_crop_fallback(item["path"], out_path)
            item["final"] = out_path

        _check_cancel(job_id)
        db.update_job(job_id, status="uploading_clips", progress=95)
        results = []
        for i, item in enumerate(produced):
            key = f"jobs/{job_id}/clips/clip_{i+1:02d}_base.mp4"
            # Sobe a base ANTES de qualquer cleanup: depois do rmtree é a única
            # cópia que sobra e é de onde a queima sob demanda parte.
            _s3_upload_with_retry(item["final"], key)
            # Grava os segmentos editáveis (clip_id "<job_id>:<index>", batendo com
            # o id de favoritos/_clip_objects). Falha aqui não derruba o job: dá pra
            # re-derivar do transcript depois.
            if transcript:
                try:
                    segs = captions.segments_for_clip(
                        transcript, float(item["meta"]["start"]), float(item["meta"]["end"]))
                    if segs:
                        db.set_clip_captions(f"{job_id}:{i}", json.dumps(segs, ensure_ascii=False))
                except Exception as e:
                    log.warning("[caption] clip %d: falha ao gravar segmentos (%s)", i + 1, e)
            results.append(_clip_result(key, item["meta"]))

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
    thumb_path = os.path.join(config.JOB_DIR, job_id, "thumb.jpg")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", "1", "-i", src,
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


def _cut_clips(video_path: str, clips_data: list, output_dir: str) -> list:
    """Corta cada clipe e devolve, por clipe efetivamente produzido, {path, meta}.
    Carrega o metadata original (title/score/reason) junto, pra não depender do
    índice (clipes < 5s ou que falham são pulados e quebrariam o casamento)."""
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
        out_path = os.path.join(output_dir, f"clip_{idx:02d}.mp4")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", video_path,
                 "-ss", str(start), "-to", str(end),
                 "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                 "-avoid_negative_ts", "1", "-movflags", "+faststart", out_path],
                capture_output=True, text=True, timeout=_FFMPEG_CUT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            log.error("[cut] clip %d estourou o timeout (%ss); pulando", n + 1, _FFMPEG_CUT_TIMEOUT)
            continue
        if result.returncode == 0 and os.path.exists(out_path):
            produced.append({"path": out_path, "meta": clip})
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
