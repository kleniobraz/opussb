"""Lógica PURA de reframe 9:16, compartilhada entre a VPS (reframer.py, codec libx264) e o worker
GPU (gpu_reframe/handler.py, codec h264_nvenc). Sem import de `config`: tudo entra por argumento,
pra a mesma matemática de câmera rodar IDÊNTICA nos dois lugares (evita o enquadramento divergir).

Depende só de cv2/numpy/ffmpeg (NÃO importa ultralytics: o modelo YOLO entra como argumento, cada
ambiente carrega o seu)."""
import os
import shutil
import subprocess
import tempfile
import cv2
import numpy as np

# Rede de proteção contra ffmpeg travado no encode final (clipe curto; folga larga).
_FFMPEG_TIMEOUT = 1200


def filter_escape(path: str) -> str:
    """Escapa um caminho para uso dentro de um filtergraph do ffmpeg (subtitles=...)."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def detect_subject_cx(frame: np.ndarray, model, width: int,
                      prev_cx, min_conf: float):
    """Centro horizontal do sujeito (coords da resolução original) ou None se não houver pessoa
    confiável (>= min_conf). Com sujeito anterior, escolhe o box mais PRÓXIMO do anterior
    (continuidade), não o mais confiante do frame -> evita o corte saltar entre pessoas."""
    small = cv2.resize(frame, (640, 360))
    scale_x = width / 640
    candidates = []   # (conf, cx)
    for box in model(small, classes=[0], verbose=False)[0].boxes:
        conf = float(box.conf[0])
        if conf < min_conf:
            continue
        x1 = float(box.xyxy[0][0]) * scale_x
        x2 = float(box.xyxy[0][2]) * scale_x
        candidates.append((conf, (x1 + x2) / 2))
    if not candidates:
        return None
    if prev_cx is None:
        return max(candidates, key=lambda c: c[0])[1]            # primeira trava: mais confiante
    return min(candidates, key=lambda c: abs(c[1] - prev_cx))[1]  # continuidade: mais perto do anterior


def moving_avg(arr, window: int):
    """Média móvel com bordas replicadas (não puxa pro centro). Polimento da trajetória."""
    a = np.asarray(arr, dtype=float)
    if window <= 1 or a.size == 0:
        return a
    window = min(window, a.size)
    pad = window // 2
    padded = np.pad(a, pad, mode="edge")
    sm = np.convolve(padded, np.ones(window) / window, mode="valid")
    return sm[:a.size]


def build_locked_trajectory(samples: list, total_frames: int, fps: float,
                            width: int, crop_w: int, center_x: int,
                            deadzone_frac: float, max_pan_frac: float) -> list:
    """Câmera virtual TRAVADA. A partir das amostras do centro do sujeito (None = sem detecção ->
    segura a última), produz o crop_x por frame com ZONA MORTA (deadzone_frac: dentro dela não mexe)
    + PAN LIMITADO (max_pan_frac por segundo: quando precisa andar, vai devagar, sem salto). Sem
    nada detectado o clipe inteiro -> corte central fixo."""
    if total_frames <= 0:
        return []
    valid = [(f, cx) for f, cx in samples if cx is not None]
    if not valid:
        return [center_x] * total_frames

    # Centro desejado do sujeito por frame: segura o último válido (sem pular pro centro).
    desired = np.empty(total_frames, dtype=float)
    vi, cur = 0, valid[0][1]
    for f in range(total_frames):
        while vi < len(valid) and valid[vi][0] <= f:
            cur = valid[vi][1]
            vi += 1
        desired[f] = cur

    half_dz = deadzone_frac * crop_w / 2.0
    max_step = max(1.0, max_pan_frac * crop_w / max(fps, 1.0))
    half_w = crop_w / 2.0
    lo, hi = half_w, max(half_w, width - half_w)
    cc = min(max(desired[0], lo), hi)
    out = np.empty(total_frames, dtype=float)
    for f in range(total_frames):
        s = desired[f]
        if s - cc > half_dz:
            target = s - half_dz
        elif cc - s > half_dz:
            target = s + half_dz
        else:
            target = cc
        delta = target - cc
        if delta > max_step:
            delta = max_step
        elif delta < -max_step:
            delta = -max_step
        cc = min(max(cc + delta, lo), hi)
        out[f] = cc - half_w

    out = moving_avg(out, max(3, int(round(fps / 3))))
    return [int(round(v)) for v in out]


def reframe_to_file(input_path: str, output_path: str, model, *,
                    deadzone_frac: float, max_pan_frac: float, min_conf: float,
                    video_codec_args: list, subtitles: str | None = None) -> None:
    """Reenquadra pra 9:16 (1080x1920) com câmera travada. `model` é um YOLO já carregado;
    `video_codec_args` é a lista de args de vídeo do ffmpeg (ex.: libx264 veryfast na VPS, h264_nvenc
    na GPU). Se já-9:16, padroniza/copia. Levanta em falha (o chamador decide o fallback)."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Já 9:16 (ou mais vertical): sem reframe. Sem legenda, copia; com legenda, padroniza e queima.
    if width > 0 and height / width >= 16 / 9 - 0.05:
        cap.release()
        if subtitles:
            _encode_vertical_with_subs(input_path, output_path, subtitles, video_codec_args)
        else:
            shutil.copy2(input_path, output_path)
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    crop_w = int(height * 9 / 16)
    if crop_w > width:
        crop_w = width
    center_x = (width - crop_w) // 2

    # Pass 1: amostra o centro do sujeito a cada N frames. grab() nos pulados (sem converter BGR),
    # retrieve() só nos amostrados. Segue o mesmo sujeito; None = segura a posição.
    sample_every = 15
    samples = []
    prev_cx = None
    frame_idx = 0
    while True:
        if not cap.grab():
            break
        if frame_idx % sample_every == 0:
            ok, frame = cap.retrieve()
            if ok:
                scx = detect_subject_cx(frame, model, width, prev_cx, min_conf)
                if scx is not None:
                    prev_cx = scx
                samples.append((frame_idx, scx))
        frame_idx += 1
    cap.release()

    per_frame_cx = build_locked_trajectory(samples, total_frames, fps, width, crop_w, center_x,
                                           deadzone_frac, max_pan_frac)
    _pipe_encode(input_path, output_path, per_frame_cx, fps, width, crop_w, center_x,
                 video_codec_args, subtitles)


def _pipe_encode(input_path: str, output_path: str, per_frame_cx: list, fps: float,
                 width: int, crop_w: int, center_x: int,
                 video_codec_args: list, subtitles: str | None = None) -> None:
    """Pass 2 em UMA passada: recorta cada frame e envia direto pro ffmpeg (rawvideo via stdin),
    que encoda (codec parametrizado) e junta o áudio original num único encode. stderr do ffmpeg vai
    pra ARQUIVO (não PIPE; PIPE não drenado travaria o ffmpeg em deadlock). BrokenPipe/timeout ->
    levanta (o chamador decide o fallback)."""
    out_w, out_h = 1080, 1920
    last_cx = per_frame_cx[-1] if per_frame_cx else center_x   # fora do alcance: segura a última
    vf = "setsar=1"
    if subtitles:
        vf += f",subtitles={filter_escape(subtitles)}"
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_w}x{out_h}", "-r", f"{fps}", "-i", "-",
        "-i", input_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-vf", vf,
        *video_codec_args, "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest", "-movflags", "+faststart",
        output_path,
    ]
    errf = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errf)
    cap = cv2.VideoCapture(input_path)
    try:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            cx = per_frame_cx[frame_idx] if frame_idx < len(per_frame_cx) else last_cx
            cx = max(0, min(cx, width - crop_w))
            resized = cv2.resize(frame[:, cx:cx + crop_w], (out_w, out_h))
            proc.stdin.write(resized.tobytes())
            frame_idx += 1
        proc.stdin.close()
        try:
            proc.wait(timeout=_FFMPEG_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("reframe encode estourou o timeout")
    except (BrokenPipeError, OSError) as e:
        proc.kill()
        raise RuntimeError(f"reframe pipe quebrou (ffmpeg morreu cedo): {e}")
    finally:
        cap.release()
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass

    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
        errf.seek(0)
        tail = errf.read()[-500:].decode("utf-8", "replace")
        errf.close()
        raise RuntimeError(f"reframe encode falhou (rc={proc.returncode}): {tail}")
    errf.close()


def _encode_vertical_with_subs(input_path: str, output_path: str, subtitles: str,
                               video_codec_args: list) -> None:
    """Caso raro: clipe já é 9:16. Padroniza pra 1080x1920 e queima a legenda."""
    vf = f"scale=1080:1920,setsar=1,subtitles={filter_escape(subtitles)}"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vf", vf,
         *video_codec_args, "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", output_path],
        capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg subtitle burn failed: {result.stderr[-400:]}")
