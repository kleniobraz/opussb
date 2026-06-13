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
CLAUDE_MODEL: str = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# N8N
N8N_WEBHOOK_URL: str = os.environ["N8N_WEBHOOK_URL"]

# Flask
FLASK_PORT: int = int(os.environ.get("FLASK_PORT", "5001"))
MAX_CONCURRENT_JOBS: int = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
JOB_DIR: str = os.environ.get("JOB_DIR", "/opt/opussb/jobs")
