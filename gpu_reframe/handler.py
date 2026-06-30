"""Worker RunPod SERVERLESS de reframe 9:16 na GPU.

Recebe um clipe (presigned GET), reenquadra com a MESMA lógica da VPS (`reframe_core`, câmera
travada) só que com encode **NVENC** (GPU), e sobe o resultado via presigned PUT. Devolve a duração.
Roda só dentro da imagem do worker (gpu_reframe/Dockerfile); não é importado na VPS.

Dois modos (BACKEND §33, crop por keyframe):
  AUTO (default): reenquadra com YOLO (câmera travada) e, de brinde, devolve os keyframes ESPARSOS
                  da trajetória ({"keyframes": {mode,kf}}) pro app semear o editor de crop. Custo zero
                  (pega carona no mesmo job).
  RE-RENDER: se o input traz `keyframes` (lista [{t,cx}]), NÃO roda YOLO: re-renderiza o 16:9 com a
             trajetória do usuário (reframe_with_keyframes). É o "Aplicar" do crop manual na GPU.

Contrato de input (job["input"]):
  input_url           presigned GET do corte (janela)/16:9 a reenquadrar
  output_put_url      presigned PUT onde subir a base 9:16 resultante
  output_content_type Content-Type a usar no PUT (TEM que casar com o assinado, senão 403)
  deadzone_frac, max_pan_frac, min_conf   params da câmera travada (modo AUTO)
  keyframe_interval_s intervalo (s) de amostragem do auto-seed (modo AUTO; default 0.75)
  keyframes           lista [{t,cx}] -> liga o modo RE-RENDER (sem YOLO)
Saída AUTO:     {"duration": <s>, "ok": true, "keyframes": {mode,kf}|null}
Saída RE-RENDER:{"duration": <s>, "ok": true, "rerender": true}
Erro:           {"error": "<msg>"}.
"""
import os
import subprocess
import tempfile
import time

import requests
import runpod
from ultralytics import YOLO

import reframe_core

# NVENC: VBR de qualidade constante. p4 = preset médio do NVENC. Tunável na Fase B se precisar.
_NVENC_CODEC = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
_HTTP_TIMEOUT = 300
# Retry com backoff no I/O S3 (download/upload). O Hetzner Object Storage devolve 502/504 transitorios
# (~10% nos smoke tests); sem retry, cada um derruba o reframe da GPU pro fallback de CPU. 5xx/429/rede
# = retentavel; 4xx (assinatura/permissao) = falha imediata (repetir nao adianta). BACKEND §33/Fase 2b.
_HTTP_RETRIES = 4
_RETRY_BACKOFF = 2.0

# Modelo carregado UMA vez por worker (no import, não por job) -> sem recarregar/OOM por requisição.
_model = YOLO(os.environ.get("YOLO_WEIGHTS", "yolov8n.pt"))

# Nome da GPU do worker (diagnostico): reportado no output pra correlacionar falha de NVENC com o tipo
# de placa (ja houve incidente de GPU incompativel, ver BACKEND_CONTEXT). Best-effort, nunca quebra.
try:
    import torch
    _GPU = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no-cuda"
except Exception:
    _GPU = "unknown"


def _with_retries(fn, what: str):
    """Roda fn() com retry+backoff. Repete em 5xx/429/erro de rede (transitorios do Hetzner); 4xx
    (exceto 429) levanta na hora (assinatura/permissao nao melhora repetindo)."""
    last = None
    for attempt in range(_HTTP_RETRIES):
        try:
            return fn()
        except requests.RequestException as e:
            last = e
            resp = getattr(e, "response", None)
            if resp is not None and 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise
            if attempt < _HTTP_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise last


def _download(url: str, dst: str) -> None:
    # Re-abre o arquivo a cada tentativa (trunca): um download parcial de uma tentativa anterior nao
    # vaza pra proxima. Cada tentativa e um GET inteiro.
    def _do():
        with requests.get(url, stream=True, timeout=_HTTP_TIMEOUT) as r:
            r.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
    _with_retries(_do, "download")


def _probe_duration(path: str):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
        return float((r.stdout or "").strip())
    except Exception:
        return None


def _upload(out_path: str, put_url: str, ct: str) -> None:
    # Re-abre o arquivo a cada tentativa (o handle precisa voltar ao inicio num retry).
    def _do():
        with open(out_path, "rb") as f:
            # Content-Type IGUAL ao assinado no presigned PUT (mismatch = 403).
            put = requests.put(put_url, data=f, headers={"Content-Type": ct}, timeout=_HTTP_TIMEOUT)
        put.raise_for_status()
    _with_retries(_do, "upload")


def reframe_job(job_input: dict) -> dict:
    src = job_input["input_url"]
    put_url = job_input["output_put_url"]
    ct = job_input.get("output_content_type", "video/mp4")
    kf_list = job_input.get("keyframes")        # presença -> modo RE-RENDER (crop manual, sem YOLO)
    with tempfile.TemporaryDirectory() as tmp:
        cut = os.path.join(tmp, "cut.mp4")
        out = os.path.join(tmp, "base.mp4")
        _download(src, cut)
        if isinstance(kf_list, list) and kf_list:
            # RE-RENDER: aplica a trajetória do usuário no 16:9, sem detecção (mesma matemática da CPU).
            reframe_core.reframe_with_keyframes(cut, out, kf_list, video_codec_args=_NVENC_CODEC)
            dur = _probe_duration(out)
            _upload(out, put_url, ct)
            return {"duration": dur, "ok": True, "rerender": True, "gpu": _GPU}
        # AUTO: reframe com YOLO + auto-seed de keyframes (BACKEND §33).
        params = dict(
            deadzone_frac=float(job_input.get("deadzone_frac", 0.35)),
            max_pan_frac=float(job_input.get("max_pan_frac", 0.18)),
            min_conf=float(job_input.get("min_conf", 0.30)),
            detect_cuts=bool(job_input.get("detect_cuts", False)),
        )
        traj = reframe_core.reframe_to_file(cut, out, _model, video_codec_args=_NVENC_CODEC, **params)
        dur = _probe_duration(out)
        _upload(out, put_url, ct)
    # Reduz a trajetória a keyframes esparsos pro app semear o editor (None se já-vertical/sem trajetória).
    kf = None
    if traj:
        try:
            kf = reframe_core.keyframes_from_trajectory(
                traj["per_frame_cx"], traj["fps"], traj["width"], traj["crop_w"],
                traj.get("cuts"), float(job_input.get("keyframe_interval_s", 0.75)))
        except Exception:
            kf = None
    return {"duration": dur, "ok": True, "keyframes": kf, "gpu": _GPU}


def handler(job):
    try:
        return reframe_job(job["input"])
    except Exception as e:
        # Retorno de erro gracioso: o backend cai no fallback de CPU pra esse clipe.
        return {"error": str(e), "gpu": _GPU}


runpod.serverless.start({"handler": handler})
