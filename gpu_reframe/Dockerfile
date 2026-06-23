# Worker GPU de reframe (RunPod serverless). Base PyTorch+CUDA runtime (dá CUDA + torch pro YOLO);
# o ffmpeg do apt do Ubuntu 22.04 já traz h264_nvenc (carregado contra o driver do host em runtime,
# que a RunPod fornece). Build sempre linux/amd64 (ver o workflow de CI).
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video

# ffmpeg (com h264_nvenc) + libs que o opencv da ultralytics precisa (senão crash de libGL.so.1).
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# Sanity gate: falha o build LOUD se o ffmpeg não tiver o h264_nvenc compilado (o listing não
# precisa de GPU; só o encode real precisa do driver, que a RunPod fornece).
RUN ffmpeg -hide_banner -encoders | grep -q h264_nvenc

WORKDIR /app

COPY gpu_reframe/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Baixa os pesos do YOLOv8n no build (embutidos na imagem -> não baixa no cold start). yolov8n.pt
# não está no git, então a ultralytics busca da release oficial pra dentro do WORKDIR (/app).
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# Lógica compartilhada com a VPS (mesma câmera travada) + o handler.
COPY reframe_core.py /app/reframe_core.py
COPY gpu_reframe/handler.py /app/handler.py

CMD ["python", "-u", "handler.py"]
