import os
import json
import shutil
import subprocess
import time
import requests
import config
import db
import s3_client


def run_thread1(job_id: str) -> None:
    """Thread 1: extract audio → upload to S3 → trigger N8N."""
    job = db.get_job(job_id)
    try:
        db.update_job(job_id, status="extracting_audio", progress=15)
        audio_path = _extract_audio(job["local_path"], job_id)

        db.update_job(job_id, status="uploading_audio", progress=25)
        audio_s3_key = f"jobs/{job_id}/audio/audio.mp3"
        _s3_upload_with_retry(audio_path, audio_s3_key)
        audio_s3_url = s3_client.get_presigned_url(audio_s3_key, expiry_seconds=86400)
        db.update_job(job_id, audio_s3_url=audio_s3_url, status="transcribing", progress=35)

        _trigger_n8n_with_retry(
            job_id, audio_s3_url, job["min_duration"], job["max_duration"]
        )
    except Exception as e:
        _fail_job(job_id, str(e))


def run_thread2(job_id: str) -> None:
    """Thread 2: cut clips → reframe → upload to S3 → mark done."""
    job = db.get_job(job_id)
    try:
        raw = job["clips_json"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        clips_data = json.loads(raw.strip())
        job_clips_dir = os.path.join(config.JOB_DIR, job_id, "clips")
        os.makedirs(job_clips_dir, exist_ok=True)

        db.update_job(job_id, status="cutting", progress=70)
        cut_paths = _cut_clips(job["local_path"], clips_data, job_clips_dir)

        db.update_job(job_id, status="reframing", progress=85)
        from reframer import reframe_clip
        final_paths = []
        for i, cut_path in enumerate(cut_paths):
            out_path = cut_path.replace(".mp4", "_9x16.mp4")
            try:
                reframe_clip(cut_path, out_path)
            except Exception as e:
                print(f"[reframe] clip {i+1} falhou ({e}), usando center crop")
                _center_crop_fallback(cut_path, out_path)
            final_paths.append(out_path)

        db.update_job(job_id, status="uploading_clips", progress=95)
        s3_keys = []
        for i, path in enumerate(final_paths):
            key = f"jobs/{job_id}/clips/clip_{i+1:02d}_9x16.mp4"
            _s3_upload_with_retry(path, key)
            s3_keys.append(key)

        db.update_job(job_id, status="done", progress=100, result_urls=json.dumps(s3_keys), error=None)

        # Cleanup só aqui, depois dos clipes confirmados no S3 (regra: nunca antes).
        shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)

    except Exception as e:
        _fail_job(job_id, str(e))


# ─── Private helpers ─────────────────────────────────────────────────────────

def _extract_audio(video_path: str, job_id: str) -> str:
    audio_path = os.path.join(config.JOB_DIR, job_id, "audio.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-acodec", "libmp3lame", "-q:a", "4", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")
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
            resp = requests.post(config.N8N_WEBHOOK_URL, json=payload, timeout=600)
            resp.raise_for_status()
            return
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"N8N trigger failed after {retries} attempts: {e}")
            time.sleep(2 ** attempt)


def _cut_clips(video_path: str, clips_data: list, output_dir: str) -> list:
    cut_paths = []
    for i, clip in enumerate(clips_data):
        start = float(clip["start"])
        end = float(clip["end"])
        if end - start < 5:
            print(f"[cut] clip {i+1} ignorado: duração {end-start:.1f}s muito curta")
            continue
        out_path = os.path.join(output_dir, f"clip_{i+1:02d}.mp4")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-ss", str(start), "-to", str(end),
             "-c:v", "libx264", "-c:a", "aac",
             "-avoid_negative_ts", "1", out_path],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and os.path.exists(out_path):
            cut_paths.append(out_path)
        else:
            print(f"[cut] clip {i+1} falhou: {result.stderr[:200]}")
    return cut_paths


def _center_crop_fallback(input_path: str, output_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-vf", "crop=iw*9/16:ih:(iw-iw*9/16)/2:0,scale=1080:1920",
         "-c:v", "libx264", "-crf", "23", "-c:a", "aac", output_path],
        capture_output=True, check=True,
    )


def _fail_job(job_id: str, error_msg: str) -> None:
    db.update_job(job_id, status="error", error=error_msg)
    shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)
