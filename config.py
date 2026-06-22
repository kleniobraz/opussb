"""
config.py — Single source of configuration for OpusSB.
Reads from .env via python-dotenv. Variables without defaults
raise KeyError if missing (os.environ["VAR"] semantics).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# S3 / Hetzner Object Storage
S3_ENDPOINT: str = os.environ["S3_ENDPOINT"]
S3_BUCKET: str = os.environ["S3_BUCKET"]
S3_ACCESS_KEY: str = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY: str = os.environ["S3_SECRET_KEY"]

# RunPod
RUNPOD_API_KEY: str = os.environ["RUNPOD_API_KEY"]
RUNPOD_ENDPOINT_ID: str = os.environ["RUNPOD_ENDPOINT_ID"]

# Claude / Anthropic
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# N8N
N8N_WEBHOOK_URL: str = os.environ["N8N_WEBHOOK_URL"]

# Sessão / login. SECRET_KEY é obrigatória de propósito: sem ela o app não sobe,
# evita rodar com chave default insegura e invalidar sessões a cada restart.
FLASK_SECRET_KEY: str = os.environ["FLASK_SECRET_KEY"]
SESSION_DAYS: int = int(os.environ.get("SESSION_DAYS", "7"))

# Flask
FLASK_PORT: int = int(os.environ.get("FLASK_PORT", "5001"))
MAX_CONCURRENT_JOBS: int = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
JOB_DIR: str = os.environ.get("JOB_DIR", "/opt/opussb/jobs")
# Acima da janela do Whisper (5-15min) com folga. Job preso passa disso vira error.
JOB_STALE_MINUTES: int = int(os.environ.get("JOB_STALE_MINUTES", "30"))

# YouTube (yt-dlp): teto de duração do vídeo de entrada, em segundos. Controla custo de
# transcrição (RunPod escala com a duração) e quanto tempo um job segura o único slot da
# fila (MAX_CONCURRENT_JOBS=1). Default 2h. Ajustável por env sem mexer no código.
YOUTUBE_MAX_DURATION: int = int(os.environ.get("YOUTUBE_MAX_DURATION", str(2 * 60 * 60)))

# ─── Auth: Zoho SSO + cadastro com aprovação ─────────────────────────────────
# Tudo OPCIONAL de propósito: sem ZOHO_CLIENT_ID/SECRET o login Zoho fica desligado
# e só o login por senha (incl. break-glass) funciona — o app sobe normal.
ZOHO_CLIENT_ID: str = os.environ.get("ZOHO_CLIENT_ID", "")
ZOHO_CLIENT_SECRET: str = os.environ.get("ZOHO_CLIENT_SECRET", "")
ZOHO_REDIRECT_URI: str = os.environ.get(
    "ZOHO_REDIRECT_URI", "https://sbclips.space/api/auth/zoho/callback"
)
# Data center do Zoho. .com no nosso caso (MX em mx.zoho.com); .eu/.in/.com.au noutras regiões.
ZOHO_ACCOUNTS_BASE: str = os.environ.get("ZOHO_ACCOUNTS_BASE", "https://accounts.zoho.com").rstrip("/")
ZOHO_ENABLED: bool = bool(ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET)

# Domínios que entram via Zoho (login direto, auto-aprovado). Fora daqui, recusa.
_ALLOWED_DOMAINS_RAW = os.environ.get("ALLOWED_EMAIL_DOMAINS", "seubone.com,grupoquatro5.com")
ALLOWED_EMAIL_DOMAINS: list = [d.strip().lower() for d in _ALLOWED_DOMAINS_RAW.split(",") if d.strip()]

# Admins iniciais, semeados no boot como is_admin + status active. Promova outros pelo painel.
_ADMIN_EMAILS_RAW = os.environ.get("ADMIN_EMAILS", "")
ADMIN_EMAILS: list = [e.strip().lower() for e in _ADMIN_EMAILS_RAW.split(",") if e.strip()]
