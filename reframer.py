import os
import subprocess
import cv2
import numpy as np
from ultralytics import YOLO

_model = None

def _get_model() -> YOLO:
    global _model
    if _model is None:
        _model = YOLO("yolov8n.pt")
    return _model


def reframe_clip(input_path: str, output_path: str) -> None:
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Se já é 9:16 (ou mais vertical), copia direto sem reframe
    if width > 0 and height / width >= 16 / 9 - 0.05:
        cap.release()
        import shutil
        shutil.copy2(input_path, output_path)
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Target: 9:16 crop width from the source height
    crop_w = int(height * 9 / 16)
    if crop_w > width:
        crop_w = width
    # Center fallback: crop starts at this x if no person detected
    center_x = (width - crop_w) // 2

    # Pass 1: detect crop_x per sampled frame
    sample_every = 15
    raw_cx = []  # (frame_idx, crop_x)
    frame_idx = 0
    model = _get_model()
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_idx % sample_every == 0:
            cx = _detect_crop_x(frame, model, width, crop_w, center_x)
            raw_cx.append((frame_idx, cx))
        frame_idx += 1
    cap.release()

    # Build per-frame crop_x array with temporal smoothing (window=45 frames)
    if not raw_cx:
        per_frame_cx = [center_x] * total_frames
    else:
        per_frame_cx = _interpolate_and_smooth(raw_cx, total_frames, window=15)

    # Pass 2: write cropped frames
    tmp_path = output_path + ".tmp.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(tmp_path, fourcc, fps, (1080, 1920))
    assert out.isOpened(), f"VideoWriter failed to open: {tmp_path}"

    cap2 = cv2.VideoCapture(input_path)
    frame_idx = 0
    while True:
        ret, frame = cap2.read()
        if not ret:
            break
        cx = per_frame_cx[frame_idx] if frame_idx < len(per_frame_cx) else center_x
        cx = max(0, min(cx, width - crop_w))
        cropped = frame[:, cx:cx + crop_w]
        resized = cv2.resize(cropped, (1080, 1920))
        out.write(resized)
        frame_idx += 1
    cap2.release()
    out.release()

    # Mux audio back with FFmpeg
    _mux_audio(input_path, tmp_path, output_path)
    if os.path.exists(tmp_path):
        os.remove(tmp_path)


def _detect_crop_x(frame: np.ndarray, model: YOLO, width: int, crop_w: int, fallback_x: int) -> int:
    small = cv2.resize(frame, (640, 360))
    scale_x = width / 640
    results = model(small, classes=[0], verbose=False)
    best_conf = 0.0
    best_cx = fallback_x
    for box in results[0].boxes:
        conf = float(box.conf[0])
        if conf > best_conf:
            best_conf = conf
            x1 = float(box.xyxy[0][0]) * scale_x
            x2 = float(box.xyxy[0][2]) * scale_x
            person_cx = (x1 + x2) / 2
            # Center crop window on person, clamped to video bounds
            cx = int(person_cx - crop_w / 2)
            cx = max(0, min(cx, width - crop_w))
            best_cx = cx
    return best_cx


def _interpolate_and_smooth(raw_cx: list, total_frames: int, window: int = 45) -> list:
    """Linear interpolation between samples, then moving-average smoothing."""
    # Build dense array
    dense = np.full(total_frames, raw_cx[0][1], dtype=float)
    for i in range(len(raw_cx) - 1):
        f0, cx0 = raw_cx[i]
        f1, cx1 = raw_cx[i + 1]
        for f in range(f0, min(f1, total_frames)):
            t = (f - f0) / max(f1 - f0, 1)
            dense[f] = cx0 + t * (cx1 - cx0)
    # Fill tail
    if raw_cx:
        last_f, last_cx = raw_cx[-1]
        for f in range(last_f, total_frames):
            dense[f] = last_cx

    # Moving average
    kernel = np.ones(window) / window
    # Pad edges with border values to avoid pulling toward center
    pad = window // 2
    padded = np.pad(dense, pad, mode="edge")
    smoothed = np.convolve(padded, kernel, mode="valid")
    smoothed = smoothed[:total_frames]
    return [int(round(v)) for v in smoothed]


def _mux_audio(original: str, video_only: str, output: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_only,
         "-i", original,
         "-c:v", "libx264",
         "-vf", "setsar=1",
         "-c:a", "aac",
         "-map", "0:v:0",
         "-map", "1:a:0",
         "-shortest",
         output],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio mux failed: {result.stderr}")
