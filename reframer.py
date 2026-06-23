import os
import subprocess
import tempfile
import cv2
import numpy as np
from ultralytics import YOLO
import config

# Rede de proteção contra ffmpeg travado no encode final (clipe curto; folga larga).
_FFMPEG_TIMEOUT = 1200

# Estabilidade do enquadramento (ver config.py). Lidos uma vez; env-tunáveis.
_DEADZONE_FRAC = getattr(config, "REFRAME_DEADZONE_FRAC", 0.35)
_MAX_PAN_FRAC = getattr(config, "REFRAME_MAX_PAN_FRAC", 0.18)
_MIN_CONF = getattr(config, "REFRAME_MIN_CONF", 0.30)

_model = None

def _get_model() -> YOLO:
    global _model
    if _model is None:
        _model = YOLO("yolov8n.pt")
    return _model


def _filter_escape(path: str) -> str:
    """Escapa um caminho para uso dentro de um filtergraph do ffmpeg (subtitles=...)."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def reframe_clip(input_path: str, output_path: str, subtitles: str | None = None) -> None:
    """Reenquadra para 9:16 (1080x1920). Se subtitles (caminho .ass) for passado,
    a legenda é queimada DENTRO do mesmo encode final (sem segundo encode).

    O corte só se move na HORIZONTAL (a vertical é altura cheia). O enquadramento é TRAVADO no
    sujeito: uma câmera virtual com zona morta + pan lento (ver _build_locked_trajectory), pra
    não ficar balançando atrás de cada gesto (feedback dos cortes)."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Se já é 9:16 (ou mais vertical): sem reframe. Sem legenda, copia direto;
    # com legenda, padroniza para 1080x1920 e queima.
    if width > 0 and height / width >= 16 / 9 - 0.05:
        cap.release()
        if subtitles:
            _encode_with_subtitles(input_path, output_path, subtitles)
        else:
            import shutil
            shutil.copy2(input_path, output_path)
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Target: 9:16 crop width from the source height
    crop_w = int(height * 9 / 16)
    if crop_w > width:
        crop_w = width
    # Center fallback: crop center if nothing is ever detected
    center_x = (width - crop_w) // 2

    # Pass 1: amostra o CENTRO do sujeito a cada N frames. Usa grab() nos pulados (só demuxa,
    # sem converter pra numpy) e retrieve() só nos amostrados. Segue o MESMO sujeito (o box mais
    # próximo do anterior, não a maior confiança do frame -> não salta entre pessoas) e, quando
    # NÃO detecta, registra None pra SEGURAR a posição (não pular pro centro).
    sample_every = 15
    samples = []          # (frame_idx, subject_cx | None)
    prev_cx = None
    frame_idx = 0
    model = _get_model()
    while True:
        if not cap.grab():
            break
        if frame_idx % sample_every == 0:
            ok, frame = cap.retrieve()
            if ok:
                scx = _detect_subject_cx(frame, model, width, prev_cx, _MIN_CONF)
                if scx is not None:
                    prev_cx = scx
                samples.append((frame_idx, scx))
        frame_idx += 1
    cap.release()

    per_frame_cx = _build_locked_trajectory(samples, total_frames, fps, width, crop_w, center_x)

    # Pass 2: recorta cada frame e ENVIA direto pro ffmpeg (encode único + áudio).
    _pipe_reframe_encode(input_path, output_path, per_frame_cx, fps,
                         width, crop_w, center_x, subtitles)


def _detect_subject_cx(frame: np.ndarray, model: YOLO, width: int,
                       prev_cx: float | None, min_conf: float) -> float | None:
    """Detecta o CENTRO horizontal do sujeito (em coords da resolução original) ou None se não
    houver pessoa confiável. Pra estabilidade: ignora detecções fracas (< min_conf) e, quando há
    mais de uma pessoa, escolhe a mais PRÓXIMA do sujeito anterior (continuidade), não a de maior
    confiança do frame -> evita o corte saltar entre pessoas. Sem sujeito anterior, pega a mais
    confiável."""
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
        return max(candidates, key=lambda c: c[0])[1]          # primeira trava: mais confiante
    return min(candidates, key=lambda c: abs(c[1] - prev_cx))[1]  # continuidade: mais perto do anterior


def _moving_avg(arr, window: int):
    """Média móvel com bordas replicadas (não puxa pro centro). Polimento final da trajetória."""
    a = np.asarray(arr, dtype=float)
    if window <= 1 or a.size == 0:
        return a
    window = min(window, a.size)
    pad = window // 2
    padded = np.pad(a, pad, mode="edge")
    sm = np.convolve(padded, np.ones(window) / window, mode="valid")
    return sm[:a.size]


def _build_locked_trajectory(samples: list, total_frames: int, fps: float,
                             width: int, crop_w: int, center_x: int) -> list:
    """Câmera virtual TRAVADA. A partir das amostras do centro do sujeito (com None = sem
    detecção -> segura a última), produz o crop_x por frame com:
      - zona morta: enquanto o sujeito fica dentro de uma faixa central do corte, NÃO mexe;
      - pan limitado: quando precisa andar, vai DEVAGAR (teto de px/frame), sem saltos.
    Resultado: parece fixo no rosto na fala normal, e só acompanha (suave) se a pessoa se
    deslocar de verdade. Sem nada detectado o clipe inteiro -> corte central fixo."""
    if total_frames <= 0:
        return []
    valid = [(f, cx) for f, cx in samples if cx is not None]
    if not valid:
        return [center_x] * total_frames                      # nada detectado -> centro fixo

    # Centro desejado do sujeito por frame: segura o último valor válido (sem pular pro centro).
    desired = np.empty(total_frames, dtype=float)
    vi, cur = 0, valid[0][1]
    for f in range(total_frames):
        while vi < len(valid) and valid[vi][0] <= f:
            cur = valid[vi][1]
            vi += 1
        desired[f] = cur

    # Câmera virtual em termos do CENTRO do corte (cc). half_dz = meia zona morta; max_step =
    # pan máximo por frame. lo/hi mantêm o corte dentro do quadro.
    half_dz = _DEADZONE_FRAC * crop_w / 2.0
    max_step = max(1.0, _MAX_PAN_FRAC * crop_w / max(fps, 1.0))
    half_w = crop_w / 2.0
    lo, hi = half_w, max(half_w, width - half_w)
    cc = min(max(desired[0], lo), hi)
    out = np.empty(total_frames, dtype=float)
    for f in range(total_frames):
        s = desired[f]
        if s - cc > half_dz:                                  # sujeito saiu pela direita da zona
            target = s - half_dz
        elif cc - s > half_dz:                                # saiu pela esquerda
            target = s + half_dz
        else:
            target = cc                                       # dentro da zona morta: não mexe
        delta = target - cc
        if delta > max_step:
            delta = max_step
        elif delta < -max_step:
            delta = -max_step
        cc = min(max(cc + delta, lo), hi)
        out[f] = cc - half_w                                  # crop_x = borda esquerda

    out = _moving_avg(out, max(3, int(round(fps / 3))))       # polimento dos micro-degraus
    return [int(round(v)) for v in out]


def _pipe_reframe_encode(input_path: str, output_path: str, per_frame_cx: list, fps: float,
                         width: int, crop_w: int, center_x: int, subtitles: str | None = None) -> None:
    """Pass 2 em UMA passada: lê os frames de origem, recorta/redimensiona pra 1080x1920 e
    envia direto pro ffmpeg (rawvideo via stdin), que encoda libx264 veryfast e junta o áudio
    original num único encode. Substitui o esquema antigo (cv2 grava um mp4v intermediário +
    ffmpeg re-encoda), que fazia DOIS encodes completos e um temp grande em disco. Ganho:
    ~metade do trabalho de encode, zero temp, e sem a perda de geração do mp4v.

    stderr do ffmpeg vai pra um arquivo temporário (não um PIPE) de propósito: um PIPE de
    stderr não drenado encheria e travaria o ffmpeg enquanto a gente escreve no stdin
    (deadlock). stdout vai pra DEVNULL. Se o ffmpeg morrer no meio (BrokenPipe) ou sair com
    erro/saída vazia, levanta -> o chamador (run_thread2) cai no _center_crop_fallback."""
    out_w, out_h = 1080, 1920
    last_cx = per_frame_cx[-1] if per_frame_cx else center_x   # fora do alcance: segura a última (não pula pro centro)
    vf = "setsar=1"
    if subtitles:
        vf += f",subtitles={_filter_escape(subtitles)}"
    cmd = [
        "ffmpeg", "-y",
        # input 0: vídeo cru vindo do nosso pipe (frames já recortados/redimensionados em BGR)
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_w}x{out_h}", "-r", f"{fps}", "-i", "-",
        # input 1: o arquivo original, só pelo áudio
        "-i", input_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
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
            cropped = frame[:, cx:cx + crop_w]
            resized = cv2.resize(cropped, (out_w, out_h))
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


def _encode_with_subtitles(input_path: str, output_path: str, subtitles: str) -> None:
    """Caso raro: clipe já é 9:16. Padroniza para 1080x1920 e queima a legenda."""
    vf = f"scale=1080:1920,setsar=1,subtitles={_filter_escape(subtitles)}"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac",
         "-movflags", "+faststart",
         output_path],
        capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg subtitle burn failed: {result.stderr[-400:]}")
