#!/usr/bin/env bash
# setup_vps.sh — Run as root on the target VPS (Ubuntu 22.04, root@187.127.43.75).
# Make executable before transferring: chmod +x setup_vps.sh
# Usage: bash setup_vps.sh

set -e

echo "==> Creating directory structure..."
mkdir -p /opt/opussb/{static,jobs,tests}

echo "==> Installing system dependencies..."
apt update && apt install -y \
    python3.10 \
    python3.10-venv \
    python3-pip \
    ffmpeg \
    nginx \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6

echo "==> Creating Python virtual environment..."
if [ ! -d /opt/opussb/venv ]; then
    python3.10 -m venv /opt/opussb/venv
    echo "Virtual environment created."
else
    echo "Virtual environment already exists, skipping creation."
fi

# Assumes project files have already been rsynced to /opt/opussb/
if [ ! -f /opt/opussb/requirements.txt ]; then
    echo "ERROR: /opt/opussb/requirements.txt not found. Rsync the project files first." >&2
    exit 1
fi
echo "==> Installing pip dependencies from /opt/opussb/requirements.txt..."
/opt/opussb/venv/bin/pip install --upgrade pip
/opt/opussb/venv/bin/pip install -r /opt/opussb/requirements.txt

echo "==> Verifying installed packages..."
/opt/opussb/venv/bin/python -c "import flask, boto3, cv2, ultralytics; print('VPS setup OK')"

echo "==> Installing Inter Medium font (legenda queimada, captions.py FONT_NAME)..."
# Rastreada no repo (fonts/inter/) desde 2026-07-14: ficar fora do git foi o que causou ela
# sumir na migracao Hetzner->Hostinger (asset manual, ninguem lembrou de copiar). Idempotente.
if [ -f /opt/opussb/fonts/inter/Inter-Medium.ttf ]; then
    install -d -m 755 /usr/local/share/fonts/inter
    cp /opt/opussb/fonts/inter/Inter-Medium.ttf /usr/local/share/fonts/inter/
    fc-cache -f >/dev/null
    echo "Fonte Inter Medium instalada em /usr/local/share/fonts/inter/."
else
    echo "AVISO: /opt/opussb/fonts/inter/Inter-Medium.ttf nao encontrado; legenda queimada vai" >&2
    echo "cair no fallback de fonte generica do sistema ate isso ser resolvido." >&2
fi

echo "==> Initialising git repository in /opt/opussb/ (if not already)..."
if [ ! -d /opt/opussb/.git ]; then
    git -C /opt/opussb init
    echo "Git repo initialised."
else
    echo "Git repo already exists, skipping init."
fi

echo "==> Setup complete."
