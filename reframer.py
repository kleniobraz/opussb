"""Reframe 9:16 na VPS (CPU, libx264). Fino de propósito: a matemática de câmera + o pipe de encode
vivem em `reframe_core` (compartilhados com o worker GPU em gpu_reframe/handler.py, pra o
enquadramento ser idêntico nos dois). Aqui só ficam o carregamento do YOLO e os params do config."""
import reframe_core
import config
from ultralytics import YOLO

# Estabilidade do enquadramento (ver config.py). Env-tunáveis; passados pro core.
_DEADZONE_FRAC = getattr(config, "REFRAME_DEADZONE_FRAC", 0.35)
_MAX_PAN_FRAC = getattr(config, "REFRAME_MAX_PAN_FRAC", 0.18)
_MIN_CONF = getattr(config, "REFRAME_MIN_CONF", 0.30)
_CUT_DETECT = getattr(config, "REFRAME_CUT_DETECT", True)
# Codec da VPS (CPU). Na GPU o worker usa h264_nvenc; a lógica de corte é a mesma (reframe_core).
_VPS_CODEC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]

# Reexport: o processor.py importa `from reframer import _filter_escape` (escape de caminho no
# filtergraph do ffmpeg, usado no _center_crop_fallback).
_filter_escape = reframe_core.filter_escape

_model = None


def _get_model() -> YOLO:
    global _model
    if _model is None:
        _model = YOLO("yolov8n.pt")
    return _model


def reframe_clip(input_path: str, output_path: str, subtitles: str | None = None) -> None:
    """Reenquadra pra 9:16 (1080x1920) com câmera travada, encode libx264 na CPU. Delega o trabalho
    pro reframe_core (mesma matemática do worker GPU)."""
    reframe_core.reframe_to_file(
        input_path, output_path, _get_model(),
        deadzone_frac=_DEADZONE_FRAC, max_pan_frac=_MAX_PAN_FRAC, min_conf=_MIN_CONF,
        video_codec_args=_VPS_CODEC, subtitles=subtitles, detect_cuts=_CUT_DETECT,
    )
