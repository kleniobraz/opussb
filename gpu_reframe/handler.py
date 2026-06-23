"""Worker RunPod SERVERLESS de reframe 9:16 na GPU.

Recebe um clipe (presigned GET), reenquadra com a MESMA lógica da VPS (`reframe_core`, câmera
travada) só que com encode **NVENC** (GPU), e sobe o resultado via presigned PUT. Devolve a duração.
Roda só dentro da imagem do worker (gpu_reframe/Dockerfile); não é importado na VPS.

Contrato de input (job["input"]):
  input_url           presigned GET do corte (janela) a reenquadrar
  output_put_url      presigned PUT onde subir a base 9:16 resultante
  output_content_type Content-Type a usar no PUT (TEM que casar com o assinado, senão 403)
  deadzone_frac, max_pan_frac, min_conf   params da câmera travada (mesmos da VPS)
Saída: {"duration": <s>, "ok": true} ou {"error": "<msg>"}.
"""
import os
import subprocess
import tempfile

import requests
import runpod
from ultralytics import YOLO

import reframe_core

# NVENC: VBR de qualidade constante. p4 = preset médio do NVENC. Tunável na Fase B se precisar.
_NVENC_CODEC = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
_HTTP_TIMEOUT = 300

# Modelo carregado UMA vez por worker (no import, não por job) -> sem recarregar/OOM por requisição.
_model = YOLO(os.environ.get("YOLO_WEIGHTS", "yolov8n.pt"))


def _download(url: str, dst: str) -> None:
    with requests.get(url, stream=True, timeout=_HTTP_TIMEOUT) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def _probe_duration(path: str):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float((r.stdout or "").strip())
    except Exception:
        return None


def reframe_job(job_input: dict) -> dict:
    src = job_input["input_url"]
    put_url = job_input["output_put_url"]
    ct = job_input.get("output_content_type", "video/mp4")
    params = dict(
        deadzone_frac=float(job_input.get("deadzone_frac", 0.35)),
        max_pan_frac=float(job_input.get("max_pan_frac", 0.18)),
        min_conf=float(job_input.get("min_conf", 0.30)),
    )
    with tempfile.TemporaryDirectory() as tmp:
        cut = os.path.join(tmp, "cut.mp4")
        out = os.path.join(tmp, "base.mp4")
        _download(src, cut)
        reframe_core.reframe_to_file(cut, out, _model, video_codec_args=_NVENC_CODEC, **params)
        dur = _probe_duration(out)
        with open(out, "rb") as f:
            # Content-Type IGUAL ao assinado no presigned PUT (mismatch = 403).
            put = requests.put(put_url, data=f, headers={"Content-Type": ct}, timeout=_HTTP_TIMEOUT)
        put.raise_for_status()
    return {"duration": dur, "ok": True}


def handler(job):
    try:
        return reframe_job(job["input"])
    except Exception as e:
        # Retorno de erro gracioso: o backend cai no fallback de CPU pra esse clipe.
        return {"error": str(e)}


runpod.serverless.start({"handler": handler})
