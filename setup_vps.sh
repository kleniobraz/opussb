#!/usr/bin/env bash
# setup_vps.sh — Run as root on the target VPS (Ubuntu 22.04, root@87.99.159.235).
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

echo "==> Initialising git repository in /opt/opussb/ (if not already)..."
if [ ! -d /opt/opussb/.git ]; then
    git -C /opt/opussb init
    echo "Git repo initialised."
else
    echo "Git repo already exists, skipping init."
fi

echo "==> Setup complete."
