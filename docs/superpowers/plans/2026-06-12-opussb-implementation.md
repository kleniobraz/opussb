# OpusSB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir o pipeline completo OpusSB — upload de vídeo → transcrição → scoring → corte → reframe 9:16 → entrega de clipes via S3.

**Architecture:** Flask job server na VPS gerencia estado (SQLite) e threads de processamento; N8N orquestra APIs externas (RunPod + Claude); processor.py executa FFmpeg e delega reframe ao reframer.py com YOLOv8.

**Tech Stack:** Python 3.10, Flask 3.x, SQLite, boto3, FFmpeg, ultralytics (YOLOv8n), OpenCV headless, scipy, requests, pytest, Nginx, systemd.

---

## Mapa de Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `config.py` | Única fonte de configuração — lê env vars |
| `.env` | Secrets da VPS (nunca commitado) |
| `db.py` | CRUD SQLite para jobs |
| `s3_client.py` | Wrapper boto3 (upload + presigned URLs) |
| `app.py` | Flask: /upload, /status, /callback, /internal |
| `processor.py` | Thread 1 (áudio→S3→N8N) + Thread 2 (corte→reframe→S3) |
| `reframer.py` | YOLOv8 + OpenCV → vídeo 9:16 |
| `static/index.html` | Formulário de upload + polling UI |
| `static/app.js` | Lógica de polling e exibição de status |
| `static/style.css` | CSS básico |
| `tests/conftest.py` | Fixtures de env e DB para todos os testes |
| `tests/test_db.py` | Testes unitários do SQLite |
| `tests/test_s3_client.py` | Testes do wrapper S3 (boto3 mockado) |
| `tests/test_app.py` | Testes dos endpoints Flask |
| `tests/test_processor.py` | Testes das funções de processamento (subprocess mockado) |
| `/etc/nginx/sites-available/opussb` | Config Nginx |
| `/etc/systemd/system/opussb.service` | Serviço systemd do Flask |

---

## Task 1: VPS Setup — Dependências de Sistema

**Files:**
- Create: `/opt/opussb/requirements.txt`

- [ ] **Step 1: SSH na VPS e criar estrutura de diretórios**

```bash
ssh root@87.99.159.235
mkdir -p /opt/opussb/{static,jobs,tests}
cd /opt/opussb
```

- [ ] **Step 2: Instalar dependências de sistema**

```bash
apt update && apt install -y \
  python3.10 python3.10-venv python3-pip \
  ffmpeg \
  nginx \
  libglib2.0-0 libsm6 libxrender1 libxext6
```

Verificar: `ffmpeg -version` deve exibir `ffmpeg version 4.x` ou superior.

- [ ] **Step 3: Criar virtualenv e instalar dependências Python**

```bash
cd /opt/opussb
python3.10 -m venv venv
source venv/bin/activate
```

Criar `/opt/opussb/requirements.txt`:

```
flask>=3.0
python-dotenv>=1.0
boto3>=1.34
requests>=2.32
ultralytics>=8.2
opencv-python-headless>=4.10
scipy>=1.13
numpy>=1.26
pytest>=8.2
pytest-mock>=3.14
```

```bash
pip install -r requirements.txt
```

Verificar: `python -c "import flask, boto3, cv2, ultralytics; print('OK')"` deve imprimir `OK`.

- [ ] **Step 4: Commit do requirements.txt**

```bash
cd /opt/opussb
git init
git add requirements.txt
git commit -m "chore: add requirements.txt"
```

---

## Task 2: config.py + .env + conftest.py

**Files:**
- Create: `/opt/opussb/config.py`
- Create: `/opt/opussb/.env` (não commitado)
- Create: `/opt/opussb/.gitignore`
- Create: `/opt/opussb/tests/conftest.py`
- Create: `/opt/opussb/tests/test_config.py`

- [ ] **Step 1: Criar .gitignore**

```
.env
__pycache__/
*.pyc
venv/
jobs/
opussb.db
```

- [ ] **Step 2: Escrever o teste que falha**

Criar `/opt/opussb/tests/test_config.py`:

```python
import os
import importlib
import pytest


def test_config_expoe_variaveis_obrigatorias():
    import config
    assert hasattr(config, "S3_ENDPOINT")
    assert hasattr(config, "S3_BUCKET")
    assert hasattr(config, "S3_ACCESS_KEY")
    assert hasattr(config, "S3_SECRET_KEY")
    assert hasattr(config, "RUNPOD_API_KEY")
    assert hasattr(config, "RUNPOD_ENDPOINT_ID")
    assert hasattr(config, "ANTHROPIC_API_KEY")
    assert hasattr(config, "N8N_WEBHOOK_URL")
    assert hasattr(config, "FLASK_PORT")
    assert hasattr(config, "MAX_CONCURRENT_JOBS")
    assert hasattr(config, "JOB_DIR")


def test_flask_port_e_inteiro():
    import config
    assert isinstance(config.FLASK_PORT, int)


def test_max_concurrent_jobs_e_inteiro():
    import config
    assert isinstance(config.MAX_CONCURRENT_JOBS, int)
    assert config.MAX_CONCURRENT_JOBS >= 1
```

- [ ] **Step 3: Rodar para confirmar que falha**

```bash
cd /opt/opussb && source venv/bin/activate
pytest tests/test_config.py -v
```

Esperado: `ERROR` — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 4: Criar `/opt/opussb/tests/conftest.py`**

```python
import os

# Define antes de qualquer import de módulos do projeto
os.environ.setdefault("S3_ENDPOINT", "https://test.s3.example.com")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("S3_ACCESS_KEY", "AKIATEST")
os.environ.setdefault("S3_SECRET_KEY", "secrettest")
os.environ.setdefault("RUNPOD_API_KEY", "rp_test")
os.environ.setdefault("RUNPOD_ENDPOINT_ID", "testendpoint")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/opussb")
os.environ.setdefault("DB_PATH", "/tmp/test_opussb.db")
os.environ.setdefault("JOB_DIR", "/tmp/test_opussb_jobs")
os.environ.setdefault("MAX_CONCURRENT_JOBS", "1")
os.environ.setdefault("FLASK_PORT", "5001")

import pytest

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Garante banco limpo para cada teste."""
    db_path = str(tmp_path / "test.db")
    jobs_dir = str(tmp_path / "jobs")
    os.environ["DB_PATH"] = db_path
    os.environ["JOB_DIR"] = jobs_dir
    os.makedirs(jobs_dir, exist_ok=True)

    import importlib, db
    importlib.reload(db)
    db.init_db()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)
```

- [ ] **Step 5: Criar `/opt/opussb/config.py`**

```python
import os
from dotenv import load_dotenv

load_dotenv()

S3_ENDPOINT: str       = os.environ["S3_ENDPOINT"]
S3_BUCKET: str         = os.environ["S3_BUCKET"]
S3_ACCESS_KEY: str     = os.environ["S3_ACCESS_KEY"]
S3_SECRET_KEY: str     = os.environ["S3_SECRET_KEY"]

RUNPOD_API_KEY: str    = os.environ["RUNPOD_API_KEY"]
RUNPOD_ENDPOINT_ID: str = os.environ["RUNPOD_ENDPOINT_ID"]

ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL: str      = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

N8N_WEBHOOK_URL: str   = os.environ["N8N_WEBHOOK_URL"]

FLASK_PORT: int        = int(os.environ.get("FLASK_PORT", "5001"))
MAX_CONCURRENT_JOBS: int = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
JOB_DIR: str           = os.environ.get("JOB_DIR", "/opt/opussb/jobs")
```

- [ ] **Step 6: Criar `/opt/opussb/.env` com as credenciais reais**

```env
S3_ENDPOINT=https://opussb-storage.fsn1.your-objectstorage.com
S3_BUCKET=opussb-storage
S3_ACCESS_KEY=SEU_ACCESS_KEY_HETZNER
S3_SECRET_KEY=SEU_SECRET_KEY_HETZNER

RUNPOD_API_KEY=SUA_RUNPOD_API_KEY
RUNPOD_ENDPOINT_ID=fczh1t4sx02j1s

ANTHROPIC_API_KEY=SUA_ANTHROPIC_API_KEY
CLAUDE_MODEL=claude-sonnet-4-6

N8N_WEBHOOK_URL=http://localhost:5678/webhook/opussb

FLASK_PORT=5001
MAX_CONCURRENT_JOBS=1
JOB_DIR=/opt/opussb/jobs
```

- [ ] **Step 7: Rodar testes e confirmar que passam**

```bash
cd /opt/opussb && source venv/bin/activate
pytest tests/test_config.py -v
```

Esperado: `3 passed`

- [ ] **Step 8: Commit**

```bash
git add config.py .gitignore tests/conftest.py tests/test_config.py
git commit -m "feat: add config.py with env var loading and test conftest"
```

---

## Task 3: db.py — SQLite Helpers

**Files:**
- Create: `/opt/opussb/db.py`
- Create: `/opt/opussb/tests/test_db.py`

- [ ] **Step 1: Escrever os testes que falham**

Criar `/opt/opussb/tests/test_db.py`:

```python
import importlib
import pytest
import db


def test_create_e_get_job():
    db.create_job("job-001", "video.mp4", 30, 90, "/opt/opussb/jobs/job-001/original.mp4")
    job = db.get_job("job-001")
    assert job is not None
    assert job["job_id"] == "job-001"
    assert job["filename"] == "video.mp4"
    assert job["status"] == "queued"
    assert job["progress"] == 5
    assert job["min_duration"] == 30
    assert job["max_duration"] == 90


def test_get_job_inexistente_retorna_none():
    result = db.get_job("nao-existe")
    assert result is None


def test_update_job_status_e_progress():
    db.create_job("job-002", "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job("job-002", status="extracting_audio", progress=15)
    job = db.get_job("job-002")
    assert job["status"] == "extracting_audio"
    assert job["progress"] == 15


def test_update_job_campos_multiplos():
    db.create_job("job-003", "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job("job-003", runpod_job_id="rpjob-abc", status="transcribing", progress=40)
    job = db.get_job("job-003")
    assert job["runpod_job_id"] == "rpjob-abc"
    assert job["status"] == "transcribing"
    assert job["progress"] == 40


def test_count_active_jobs_exclui_done_e_error():
    db.create_job("job-a", "v.mp4", 30, 90, "/tmp/v.mp4")   # queued — ativo
    db.create_job("job-b", "v.mp4", 30, 90, "/tmp/v.mp4")
    db.update_job("job-b", status="done")                     # done — não conta
    db.create_job("job-c", "v.mp4", 30, 90, "/tmp/v.mp4")
    db.update_job("job-c", status="error")                    # error — não conta
    db.create_job("job-d", "v.mp4", 30, 90, "/tmp/v.mp4")
    db.update_job("job-d", status="transcribing")             # ativo

    assert db.count_active_jobs() == 2


def test_updated_at_muda_ao_atualizar():
    db.create_job("job-ts", "v.mp4", 30, 90, "/tmp/v.mp4")
    before = db.get_job("job-ts")["updated_at"]
    import time; time.sleep(0.01)
    db.update_job("job-ts", status="cutting")
    after = db.get_job("job-ts")["updated_at"]
    assert after > before
```

- [ ] **Step 2: Rodar para confirmar que falha**

```bash
pytest tests/test_db.py -v
```

Esperado: `ERROR` — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Criar `/opt/opussb/db.py`**

```python
import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager


def _db_path() -> str:
    return os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "opussb.db"))


@contextmanager
def _conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id        TEXT PRIMARY KEY,
                filename      TEXT NOT NULL,
                status        TEXT NOT NULL,
                progress      INTEGER DEFAULT 0,
                min_duration  INTEGER,
                max_duration  INTEGER,
                local_path    TEXT,
                audio_s3_url  TEXT,
                runpod_job_id TEXT,
                transcript    TEXT,
                clips_json    TEXT,
                result_urls   TEXT,
                error         TEXT,
                created_at    TEXT,
                updated_at    TEXT
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(job_id: str, filename: str, min_duration: int, max_duration: int, local_path: str) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, filename, status, progress, min_duration, max_duration, local_path, created_at, updated_at)
               VALUES (?, ?, 'queued', 5, ?, ?, ?, ?, ?)""",
            (job_id, filename, min_duration, max_duration, local_path, _now(), _now()),
        )


def get_job(job_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def update_job(job_id: str, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    with _conn() as conn:
        conn.execute(f"UPDATE jobs SET {fields} WHERE job_id = ?", values)


def count_active_jobs() -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status NOT IN ('done', 'error')"
        ).fetchone()
        return row[0]
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_db.py -v
```

Esperado: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py tests/conftest.py
git commit -m "feat: add db.py with SQLite job management"
```

---

## Task 4: s3_client.py — Wrapper Hetzner S3

**Files:**
- Create: `/opt/opussb/s3_client.py`
- Create: `/opt/opussb/tests/test_s3_client.py`

- [ ] **Step 1: Escrever os testes que falham**

Criar `/opt/opussb/tests/test_s3_client.py`:

```python
import pytest
import s3_client


def test_upload_file_chama_boto3(mocker, tmp_path):
    mock_s3 = mocker.MagicMock()
    mocker.patch("s3_client._client", return_value=mock_s3)

    test_file = tmp_path / "audio.mp3"
    test_file.write_bytes(b"audio data")

    s3_client.upload_file(str(test_file), "jobs/abc/audio/audio.mp3")

    mock_s3.upload_file.assert_called_once_with(
        str(test_file), "test-bucket", "jobs/abc/audio/audio.mp3"
    )


def test_get_presigned_url_retorna_string(mocker):
    mock_s3 = mocker.MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://presigned.example.com/clip?sig=xyz"
    mocker.patch("s3_client._client", return_value=mock_s3)

    url = s3_client.get_presigned_url("jobs/abc/clips/clip_01_9x16.mp4", expiry_seconds=604800)

    assert url == "https://presigned.example.com/clip?sig=xyz"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": "jobs/abc/clips/clip_01_9x16.mp4"},
        ExpiresIn=604800,
    )


def test_get_presigned_url_default_expiry_24h(mocker):
    mock_s3 = mocker.MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://presigned.example.com/audio?sig=abc"
    mocker.patch("s3_client._client", return_value=mock_s3)

    s3_client.get_presigned_url("jobs/abc/audio/audio.mp3")

    # ExpiresIn é passado como keyword arg em generate_presigned_url
    assert mock_s3.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 86400
```

- [ ] **Step 2: Rodar para confirmar que falha**

```bash
pytest tests/test_s3_client.py -v
```

Esperado: `ERROR` — `ModuleNotFoundError: No module named 's3_client'`

- [ ] **Step 3: Criar `/opt/opussb/s3_client.py`**

```python
import boto3
from botocore.config import Config
import config


def _client():
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.S3_ACCESS_KEY,
        aws_secret_access_key=config.S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


def upload_file(local_path: str, s3_key: str) -> None:
    _client().upload_file(local_path, config.S3_BUCKET, s3_key)


def get_presigned_url(s3_key: str, expiry_seconds: int = 86400) -> str:
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": config.S3_BUCKET, "Key": s3_key},
        ExpiresIn=expiry_seconds,
    )
```

- [ ] **Step 4: Rodar e confirmar que passa**

```bash
pytest tests/test_s3_client.py -v
```

Esperado: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add s3_client.py tests/test_s3_client.py
git commit -m "feat: add s3_client.py with upload and presigned URL support"
```

---

## Task 5: app.py — Skeleton + /health + /api/status

**Files:**
- Create: `/opt/opussb/app.py`
- Create: `/opt/opussb/processor.py` (stub — será completado nas Tasks 8–9)
- Create: `/opt/opussb/tests/test_app.py`

- [ ] **Step 1: Criar processor.py stub para evitar ImportError**

Criar `/opt/opussb/processor.py`:

```python
# Stub — implementação completa nas Tasks 8 e 9
def run_thread1(job_id: str) -> None:
    pass

def run_thread2(job_id: str) -> None:
    pass
```

- [ ] **Step 2: Escrever os testes que falham**

Criar `/opt/opussb/tests/test_app.py`:

```python
import json
import pytest
import importlib


@pytest.fixture
def client(tmp_path):
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_health_retorna_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_status_job_inexistente_retorna_404(client):
    resp = client.get("/api/status/nao-existe")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_status_job_existente_retorna_progress(client):
    import db
    db.create_job("job-status-test", "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job("job-status-test", status="transcribing", progress=40)

    resp = client.get("/api/status/job-status-test")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "transcribing"
    assert data["progress"] == 40
    assert data["filename"] == "video.mp4"


def test_status_done_retorna_clips_como_presigned_urls(client, mocker):
    import db, json
    db.create_job("job-done", "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job(
        "job-done",
        status="done",
        progress=100,
        result_urls=json.dumps(["jobs/job-done/clips/clip_01_9x16.mp4"]),
    )
    mocker.patch("s3_client.get_presigned_url", return_value="https://presigned.example.com/clip_01.mp4")

    resp = client.get("/api/status/job-done")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "done"
    assert len(data["clips"]) == 1
    assert data["clips"][0] == "https://presigned.example.com/clip_01.mp4"


def test_status_error_retorna_mensagem(client):
    import db
    db.create_job("job-err", "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job("job-err", status="error", error="FFmpeg falhou")

    resp = client.get("/api/status/job-err")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "error"
    assert data["error"] == "FFmpeg falhou"
```

- [ ] **Step 3: Rodar para confirmar que falha**

```bash
pytest tests/test_app.py -v
```

Esperado: `ERROR` — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 4: Criar `/opt/opussb/app.py`**

```python
import os
import uuid
import json
import threading
from flask import Flask, request, jsonify, abort
import config
import db
import s3_client
import processor

app = Flask(__name__, static_folder="static", static_url_path="")
db.init_db()

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _allowed(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404

    resp = {
        "status": job["status"],
        "progress": job["progress"],
        "filename": job["filename"],
    }

    if job["status"] == "done" and job["result_urls"]:
        keys = json.loads(job["result_urls"])
        resp["clips"] = [s3_client.get_presigned_url(k, expiry_seconds=604800) for k in keys]

    if job["status"] == "error":
        resp["error"] = job["error"]
        if job["result_urls"]:
            keys = json.loads(job["result_urls"])
            resp["partial_clips"] = [s3_client.get_presigned_url(k, expiry_seconds=604800) for k in keys]

    return jsonify(resp)


@app.route("/api/upload", methods=["POST"])
def upload():
    if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
        return jsonify({
            "error": "Um vídeo já está em processamento! Vai tomar uma água, respirar e aguardar a conclusão antes de enviar novo vídeo :)"
        }), 429

    file = request.files.get("video")
    if not file or not file.filename or not _allowed(file.filename):
        return jsonify({"error": "Arquivo inválido. Use mp4, mov, avi, mkv ou webm."}), 400

    min_duration = request.form.get("min_duration", type=int)
    max_duration = request.form.get("max_duration", type=int)
    if not min_duration or not max_duration or min_duration >= max_duration:
        return jsonify({
            "error": "min_duration e max_duration são obrigatórios e min deve ser menor que max."
        }), 400

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(config.JOB_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower()
    local_path = os.path.join(job_dir, f"original{ext}")
    file.save(local_path)

    db.create_job(job_id, file.filename, min_duration, max_duration, local_path)
    threading.Thread(target=processor.run_thread1, args=(job_id,), daemon=True).start()

    return jsonify({"job_id": job_id}), 202


@app.route("/api/callback", methods=["POST"])
def callback():
    if request.remote_addr != "127.0.0.1":
        abort(403)
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    if not job_id or not db.get_job(job_id):
        return jsonify({"error": "job_id inválido"}), 400
    db.update_job(
        job_id,
        transcript=data.get("transcript"),
        clips_json=data.get("clips_json"),
        status="cutting",
        progress=70,
    )
    threading.Thread(target=processor.run_thread2, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/internal/jobs/<job_id>", methods=["PATCH"])
def internal_update_job(job_id):
    if request.remote_addr != "127.0.0.1":
        abort(403)
    data = request.get_json(force=True)
    allowed = {"runpod_job_id", "status", "progress", "error"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nenhum campo válido."}), 400
    db.update_job(job_id, **updates)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.FLASK_PORT)
```

- [ ] **Step 5: Rodar testes**

```bash
pytest tests/test_app.py -v
```

Esperado: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add app.py processor.py tests/test_app.py
git commit -m "feat: add Flask app with /health, /api/status, /api/upload, /api/callback"
```

---

## Task 6: app.py — Testes do /api/upload (concorrência + validação)

**Files:**
- Modify: `/opt/opussb/tests/test_app.py` (adiciona testes de upload)

- [ ] **Step 1: Adicionar testes de upload ao final de test_app.py**

```python
def test_upload_sem_arquivo_retorna_400(client):
    resp = client.post("/api/upload", data={"min_duration": "30", "max_duration": "90"})
    assert resp.status_code == 400
    assert "Arquivo inválido" in resp.get_json()["error"]


def test_upload_formato_invalido_retorna_400(client, tmp_path):
    test_file = tmp_path / "video.txt"
    test_file.write_bytes(b"nao e video")
    resp = client.post(
        "/api/upload",
        data={
            "video": (open(str(test_file), "rb"), "video.txt"),
            "min_duration": "30",
            "max_duration": "90",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_sem_duracao_retorna_400(client, tmp_path):
    test_file = tmp_path / "video.mp4"
    test_file.write_bytes(b"fake mp4 data")
    resp = client.post(
        "/api/upload",
        data={"video": (open(str(test_file), "rb"), "video.mp4")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "min_duration" in resp.get_json()["error"]


def test_upload_min_maior_que_max_retorna_400(client, tmp_path):
    test_file = tmp_path / "video.mp4"
    test_file.write_bytes(b"fake mp4 data")
    resp = client.post(
        "/api/upload",
        data={
            "video": (open(str(test_file), "rb"), "video.mp4"),
            "min_duration": "90",
            "max_duration": "30",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_segundo_job_retorna_429(client, mocker, tmp_path):
    import db
    # Simula um job já em andamento
    db.create_job("job-ativo", "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job("job-ativo", status="transcribing")

    test_file = tmp_path / "video.mp4"
    test_file.write_bytes(b"fake mp4 data")
    mocker.patch("processor.run_thread1")

    resp = client.post(
        "/api/upload",
        data={
            "video": (open(str(test_file), "rb"), "video.mp4"),
            "min_duration": "30",
            "max_duration": "90",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 429
    assert "água" in resp.get_json()["error"]


def test_upload_valido_cria_job_e_retorna_job_id(client, mocker, tmp_path):
    mocker.patch("processor.run_thread1")
    test_file = tmp_path / "video.mp4"
    test_file.write_bytes(b"fake mp4 data")

    resp = client.post(
        "/api/upload",
        data={
            "video": (open(str(test_file), "rb"), "video.mp4"),
            "min_duration": "30",
            "max_duration": "90",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert "job_id" in data
    assert len(data["job_id"]) == 36  # UUID4

    import db
    job = db.get_job(data["job_id"])
    assert job is not None
    assert job["status"] == "queued"
```

- [ ] **Step 2: Rodar testes**

```bash
pytest tests/test_app.py -v
```

Esperado: `11 passed`

- [ ] **Step 3: Commit**

```bash
git add tests/test_app.py
git commit -m "test: add upload endpoint validation and concurrency tests"
```

---

## Task 7: processor.py — Thread 1 (Extração de Áudio + S3 + N8N)

**Files:**
- Modify: `/opt/opussb/processor.py` (implementação completa da Thread 1)
- Create: `/opt/opussb/tests/test_processor.py`

- [ ] **Step 1: Escrever os testes que falham**

Criar `/opt/opussb/tests/test_processor.py`:

```python
import json
import os
import pytest
import importlib


@pytest.fixture(autouse=True)
def reload_processor():
    import processor
    importlib.reload(processor)


def test_extract_audio_chama_ffmpeg(mocker, tmp_path):
    import processor
    mock_run = mocker.patch("processor.subprocess.run")
    mock_run.return_value = mocker.MagicMock(returncode=0, stderr="")

    # cria arquivo dummy
    video = tmp_path / "original.mp4"
    video.write_bytes(b"fake")
    os.environ["JOB_DIR"] = str(tmp_path)

    result = processor._extract_audio(str(video), "job-extract")

    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "-acodec" in cmd
    assert "libmp3lame" in cmd


def test_extract_audio_levanta_se_ffmpeg_falha(mocker, tmp_path):
    import processor
    mock_run = mocker.patch("processor.subprocess.run")
    mock_run.return_value = mocker.MagicMock(returncode=1, stderr="codec error")
    video = tmp_path / "original.mp4"
    video.write_bytes(b"fake")

    with pytest.raises(RuntimeError, match="FFmpeg audio extraction failed"):
        processor._extract_audio(str(video), "job-fail")


def test_s3_upload_retry_levanta_apos_3_tentativas(mocker):
    import processor
    mocker.patch("processor.s3_client.upload_file", side_effect=Exception("network error"))
    mocker.patch("processor.time.sleep")

    with pytest.raises(RuntimeError, match="S3 upload failed after 3 attempts"):
        processor._s3_upload_with_retry("/tmp/audio.mp3", "jobs/abc/audio/audio.mp3")


def test_s3_upload_retry_sucede_na_segunda_tentativa(mocker):
    import processor
    call_count = {"n": 0}

    def flaky_upload(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise Exception("temporary error")

    mocker.patch("processor.s3_client.upload_file", side_effect=flaky_upload)
    mocker.patch("processor.time.sleep")

    processor._s3_upload_with_retry("/tmp/audio.mp3", "jobs/abc/audio/audio.mp3")
    assert call_count["n"] == 2


def test_trigger_n8n_retry_levanta_apos_3_falhas(mocker):
    import processor
    mocker.patch("processor.requests.post", side_effect=Exception("connection refused"))
    mocker.patch("processor.time.sleep")

    with pytest.raises(RuntimeError, match="N8N trigger failed after 3 attempts"):
        processor._trigger_n8n_with_retry("job-abc", "https://s3.example.com/audio.mp3", 30, 90)


def test_cut_clips_chama_ffmpeg_por_clip(mocker, tmp_path):
    import processor
    mock_run = mocker.patch("processor.subprocess.run")
    mock_run.return_value = mocker.MagicMock(returncode=0)

    clips_data = [
        {"start": 10.0, "end": 70.0, "score": 9, "reason": "ótimo insight"},
        {"start": 120.0, "end": 180.0, "score": 8, "reason": "momento emocional"},
    ]
    video = tmp_path / "original.mp4"
    video.write_bytes(b"fake")
    output_dir = str(tmp_path / "clips")
    os.makedirs(output_dir)

    # Cria os arquivos de saída simulados para que cut_clips os registre
    def fake_run(cmd, **kwargs):
        # Simula criação do arquivo de saída
        out_path = cmd[cmd.index("-avoid_negative_ts") + 2] if "-avoid_negative_ts" in cmd else cmd[-1]
        open(out_path, "w").close()
        return mocker.MagicMock(returncode=0)
    mock_run.side_effect = fake_run

    paths = processor._cut_clips(str(video), clips_data, output_dir)
    assert len(paths) == 2
    assert mock_run.call_count == 2


def test_cut_clips_pula_clips_muito_curtos(mocker, tmp_path):
    import processor
    mock_run = mocker.patch("processor.subprocess.run")
    clips_data = [
        {"start": 10.0, "end": 13.0, "score": 5, "reason": "curto demais"},  # < 5s
        {"start": 20.0, "end": 80.0, "score": 9, "reason": "ok"},
    ]
    output_dir = str(tmp_path / "clips")
    os.makedirs(output_dir)

    def fake_run(cmd, **kwargs):
        out_path = cmd[-1]
        open(out_path, "w").close()
        return mocker.MagicMock(returncode=0)
    mock_run.side_effect = fake_run

    paths = processor._cut_clips("/tmp/video.mp4", clips_data, output_dir)
    assert len(paths) == 1
    assert mock_run.call_count == 1
```

- [ ] **Step 2: Rodar para confirmar que falha**

```bash
pytest tests/test_processor.py -v
```

Esperado: falhas por `AttributeError` — funções privadas não existem no stub.

- [ ] **Step 3: Implementar `/opt/opussb/processor.py` completo**

```python
import os
import json
import shutil
import subprocess
import time
import requests
import config
import db
import s3_client


def run_thread1(job_id: str) -> None:
    job = db.get_job(job_id)
    try:
        db.update_job(job_id, status="extracting_audio", progress=15)
        audio_path = _extract_audio(job["local_path"], job_id)

        db.update_job(job_id, status="uploading_audio", progress=25)
        audio_s3_key = f"jobs/{job_id}/audio/audio.mp3"
        _s3_upload_with_retry(audio_path, audio_s3_key)
        audio_s3_url = s3_client.get_presigned_url(audio_s3_key, expiry_seconds=86400)
        db.update_job(job_id, audio_s3_url=audio_s3_url, status="transcribing", progress=35)

        _trigger_n8n_with_retry(
            job_id, audio_s3_url, job["min_duration"], job["max_duration"]
        )
    except Exception as e:
        _fail_job(job_id, str(e))


def run_thread2(job_id: str) -> None:
    job = db.get_job(job_id)
    try:
        clips_data = json.loads(job["clips_json"])
        job_clips_dir = os.path.join(config.JOB_DIR, job_id, "clips")
        os.makedirs(job_clips_dir, exist_ok=True)

        db.update_job(job_id, status="cutting", progress=70)
        cut_paths = _cut_clips(job["local_path"], clips_data, job_clips_dir)

        db.update_job(job_id, status="reframing", progress=85)
        from reframer import reframe_clip
        final_paths = []
        for i, cut_path in enumerate(cut_paths):
            out_path = cut_path.replace(".mp4", "_9x16.mp4")
            try:
                reframe_clip(cut_path, out_path)
            except Exception as e:
                print(f"[reframe] clip {i+1} falhou ({e}), usando center crop")
                _center_crop_fallback(cut_path, out_path)
            final_paths.append(out_path)

        db.update_job(job_id, status="uploading_clips", progress=95)
        s3_keys = []
        for i, path in enumerate(final_paths):
            key = f"jobs/{job_id}/clips/clip_{i+1:02d}_9x16.mp4"
            _s3_upload_with_retry(path, key)
            s3_keys.append(key)

        db.update_job(job_id, status="done", progress=100, result_urls=json.dumps(s3_keys))

    except Exception as e:
        _fail_job(job_id, str(e))
    finally:
        shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)


# ─── Funções privadas ────────────────────────────────────────────────────────

def _extract_audio(video_path: str, job_id: str) -> str:
    audio_path = os.path.join(config.JOB_DIR, job_id, "audio.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-acodec", "libmp3lame", "-q:a", "4", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed: {result.stderr}")
    return audio_path


def _s3_upload_with_retry(local_path: str, s3_key: str, retries: int = 3) -> None:
    for attempt in range(retries):
        try:
            s3_client.upload_file(local_path, s3_key)
            return
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"S3 upload failed after {retries} attempts: {e}")
            time.sleep(2 ** attempt)


def _trigger_n8n_with_retry(
    job_id: str, audio_s3_url: str, min_duration: int, max_duration: int, retries: int = 3
) -> None:
    payload = {
        "job_id": job_id,
        "audio_s3_url": audio_s3_url,
        "min_duration": min_duration,
        "max_duration": max_duration,
    }
    for attempt in range(retries):
        try:
            resp = requests.post(config.N8N_WEBHOOK_URL, json=payload, timeout=10)
            resp.raise_for_status()
            return
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"N8N trigger failed after {retries} attempts: {e}")
            time.sleep(2 ** attempt)


def _cut_clips(video_path: str, clips_data: list, output_dir: str) -> list:
    cut_paths = []
    for i, clip in enumerate(clips_data):
        start = float(clip["start"])
        end = float(clip["end"])
        if end - start < 5:
            print(f"[cut] clip {i+1} ignorado: duração {end-start:.1f}s muito curta")
            continue
        out_path = os.path.join(output_dir, f"clip_{i+1:02d}.mp4")
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-ss", str(start), "-to", str(end),
             "-c:v", "libx264", "-c:a", "aac",
             "-avoid_negative_ts", "1", out_path],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and os.path.exists(out_path):
            cut_paths.append(out_path)
        else:
            print(f"[cut] clip {i+1} falhou: {result.stderr[:200]}")
    return cut_paths


def _center_crop_fallback(input_path: str, output_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path,
         "-vf", "crop=iw*9/16:ih:(iw-iw*9/16)/2:0,scale=1080:1920",
         "-c:v", "libx264", "-crf", "23", "-c:a", "aac", output_path],
        capture_output=True, check=True,
    )


def _fail_job(job_id: str, error_msg: str) -> None:
    db.update_job(job_id, status="error", error=error_msg)
    shutil.rmtree(os.path.join(config.JOB_DIR, job_id), ignore_errors=True)
```

- [ ] **Step 4: Rodar testes**

```bash
pytest tests/test_processor.py -v
```

Esperado: `7 passed`

- [ ] **Step 5: Rodar toda a suite**

```bash
pytest tests/ -v
```

Esperado: todos os testes passam.

- [ ] **Step 6: Commit**

```bash
git add processor.py tests/test_processor.py
git commit -m "feat: implement processor.py Thread 1 and Thread 2 with FFmpeg and S3"
```

---

## Task 8: reframer.py — YOLOv8 + OpenCV + FFmpeg → 9:16

**Files:**
- Create: `/opt/opussb/reframer.py`

> Este módulo não tem testes unitários automatizados — depende de arquivo de vídeo real e modelo YOLOv8. A validação é manual com um vídeo de teste.

- [ ] **Step 1: Criar `/opt/opussb/reframer.py`**

```python
import os
import subprocess
import numpy as np
import cv2
from scipy.ndimage import uniform_filter1d
from ultralytics import YOLO

_model = None


def _get_model() -> YOLO:
    global _model
    if _model is None:
        _model = YOLO("yolov8n.pt")  # download automático na primeira execução
    return _model


def reframe_clip(input_path: str, output_path: str) -> None:
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Não foi possível abrir o vídeo: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Janela de corte 9:16 mantendo altura total
    out_h = orig_h
    out_w = int(orig_h * 9 / 16)
    out_w = min(out_w, orig_w)
    out_w = out_w - (out_w % 2)  # codec exige dimensões pares

    # Passo 1: detectar posições em frames amostrados
    raw_crop_x = _detect_positions(cap, total_frames, orig_w, out_w)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    # Passo 2: trajetória suavizada
    crop_x_arr = _build_smooth_trajectory(raw_crop_x, total_frames, orig_w, out_w)

    # Passo 3: escrever vídeo sem áudio
    mute_path = output_path.replace(".mp4", "_mute.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(mute_path, fourcc, fps, (out_w, out_h))

    for i in range(total_frames):
        ret, frame = cap.read()
        if not ret:
            break
        cx = int(crop_x_arr[i]) if i < len(crop_x_arr) else (orig_w - out_w) // 2
        cropped = frame[:, cx : cx + out_w]
        if cropped.shape[1] != out_w or cropped.shape[0] != out_h:
            cropped = cv2.resize(cropped, (out_w, out_h))
        writer.write(cropped)

    writer.release()
    cap.release()

    assert os.path.exists(mute_path), "OpenCV não gerou o arquivo de vídeo"
    assert os.path.getsize(mute_path) > 1000, "Arquivo de vídeo corrompido ou vazio"

    # Passo 4: extrair áudio e fazer mux
    audio_tmp = output_path.replace(".mp4", "_audio.aac")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "copy", audio_tmp],
        capture_output=True,
    )
    # Alguns clipes podem não ter áudio — trata como stream copy normal
    if r.returncode != 0:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-vn", "-acodec", "aac", audio_tmp],
            capture_output=True, check=True,
        )

    subprocess.run(
        ["ffmpeg", "-y", "-i", mute_path, "-i", audio_tmp,
         "-c:v", "copy", "-c:a", "copy", "-shortest", output_path],
        capture_output=True, check=True,
    )

    os.remove(mute_path)
    if os.path.exists(audio_tmp):
        os.remove(audio_tmp)


def _detect_positions(cap: cv2.VideoCapture, total_frames: int, orig_w: int, out_w: int) -> dict:
    model = _get_model()
    sample_every = 15
    raw_crop_x = {}
    default_cx = (orig_w - out_w) // 2

    for idx in range(0, total_frames, sample_every):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue
        small = cv2.resize(frame, (640, 360))
        scale_x = orig_w / 640.0
        results = model(small, classes=[0], verbose=False)
        if results and len(results[0].boxes) > 0:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            best = boxes[np.argmax(areas)]
            center_x = (best[0] + best[2]) / 2 * scale_x
            cx = int(center_x - out_w / 2)
            raw_crop_x[idx] = max(0, min(cx, orig_w - out_w))

    return raw_crop_x


def _build_smooth_trajectory(
    raw_crop_x: dict, total_frames: int, orig_w: int, out_w: int
) -> np.ndarray:
    default_cx = (orig_w - out_w) // 2

    if not raw_crop_x:
        return np.full(total_frames, default_cx, dtype=int)

    indices = sorted(raw_crop_x.keys())
    values = [raw_crop_x[i] for i in indices]
    all_frames = np.arange(total_frames)
    interpolated = np.interp(all_frames, indices, values)
    smoothed = uniform_filter1d(interpolated, size=45)
    return np.clip(smoothed, 0, orig_w - out_w).astype(int)
```

- [ ] **Step 2: Validação manual — baixar vídeo de teste e rodar**

```bash
cd /opt/opussb && source venv/bin/activate

# Baixar vídeo de teste curto (30s)
wget -O /tmp/test_input.mp4 \
  "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"

python3 -c "
from reframer import reframe_clip
reframe_clip('/tmp/test_input.mp4', '/tmp/test_output_9x16.mp4')
print('OK — arquivo gerado em /tmp/test_output_9x16.mp4')
"
```

Esperado: arquivo `/tmp/test_output_9x16.mp4` criado sem erros. Verificar com:

```bash
ffprobe -v quiet -print_format json -show_streams /tmp/test_output_9x16.mp4 \
  | python3 -c "import json,sys; s=[x for x in json.load(sys.stdin)['streams'] if x['codec_type']=='video'][0]; print(s['width'], 'x', s['height'])"
```

Esperado: `1080 x 1920` (ou proporção 9:16 equivalente).

- [ ] **Step 3: Commit**

```bash
git add reframer.py
git commit -m "feat: implement reframer.py with YOLOv8n person tracking and 9:16 crop"
```

---

## Task 9: Nginx + systemd

**Files:**
- Create: `/etc/nginx/sites-available/opussb`
- Create: `/etc/systemd/system/opussb.service`

- [ ] **Step 1: Criar config Nginx**

```bash
cat > /etc/nginx/sites-available/opussb << 'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 4G;
    proxy_read_timeout   300s;
    proxy_connect_timeout 60s;
    proxy_send_timeout   300s;

    location /api/ {
        proxy_pass         http://127.0.0.1:5001;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /health {
        proxy_pass         http://127.0.0.1:5001;
        proxy_set_header   Host $host;
    }

    location / {
        root   /opt/opussb/static;
        index  index.html;
        try_files $uri $uri/ /index.html;
    }
}
EOF

ln -sf /etc/nginx/sites-available/opussb /etc/nginx/sites-enabled/opussb
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
```

Verificar: `curl http://localhost/health` deve retornar `{"status": "ok"}` (após Flask estar rodando).

- [ ] **Step 2: Criar serviço systemd**

```bash
cat > /etc/systemd/system/opussb.service << 'EOF'
[Unit]
Description=OpusSB Flask Job Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/opussb
EnvironmentFile=/opt/opussb/.env
ExecStart=/opt/opussb/venv/bin/python app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable opussb
systemctl start opussb
```

- [ ] **Step 3: Verificar que Flask sobe corretamente**

```bash
systemctl status opussb
journalctl -u opussb -n 20
curl http://localhost/health
```

Esperado: status `active (running)` e resposta `{"status": "ok"}`.

- [ ] **Step 4: Commit dos arquivos de configuração**

Copie os arquivos de config para o repo para documentação:

```bash
cp /etc/nginx/sites-available/opussb /opt/opussb/nginx.conf
cp /etc/systemd/system/opussb.service /opt/opussb/opussb.service
git add nginx.conf opussb.service
git commit -m "infra: add Nginx and systemd service configuration"
```

---

## Task 10: Frontend — index.html + app.js + style.css

**Files:**
- Create: `/opt/opussb/static/index.html`
- Create: `/opt/opussb/static/app.js`
- Create: `/opt/opussb/static/style.css`

- [ ] **Step 1: Criar `/opt/opussb/static/style.css`**

```css
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #0f0f0f;
  color: #e0e0e0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}

.container {
  width: 100%;
  max-width: 600px;
}

h1 { font-size: 2rem; margin-bottom: 0.25rem; }
.subtitle { color: #888; margin-bottom: 2rem; }

.card {
  background: #1a1a1a;
  border: 1px solid #333;
  border-radius: 12px;
  padding: 2rem;
  margin-bottom: 1.5rem;
}

label { display: block; margin-bottom: 0.4rem; font-size: 0.9rem; color: #aaa; }

input[type="file"],
input[type="number"] {
  width: 100%;
  background: #0f0f0f;
  border: 1px solid #444;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  color: #e0e0e0;
  font-size: 1rem;
  margin-bottom: 1.25rem;
}

.row { display: flex; gap: 1rem; }
.row > div { flex: 1; }

button {
  width: 100%;
  background: #6c63ff;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.9rem;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.2s;
}
button:hover:not(:disabled) { background: #5a52d5; }
button:disabled { background: #444; cursor: default; }

.progress-bar-bg {
  background: #333;
  border-radius: 999px;
  height: 8px;
  margin: 1rem 0 0.5rem;
}
.progress-bar-fill {
  height: 100%;
  border-radius: 999px;
  background: #6c63ff;
  transition: width 0.5s;
}

.stage-label { font-size: 0.85rem; color: #888; text-transform: capitalize; }

.clips-grid {
  display: grid;
  gap: 0.75rem;
  margin-top: 1rem;
}

.clip-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: #111;
  border: 1px solid #333;
  border-radius: 8px;
  padding: 0.75rem 1rem;
}

.clip-item a {
  color: #6c63ff;
  text-decoration: none;
  font-weight: 600;
}
.clip-item a:hover { text-decoration: underline; }

.error-box {
  background: #2a1a1a;
  border: 1px solid #c0392b;
  border-radius: 8px;
  padding: 1rem;
  color: #e74c3c;
  margin-top: 1rem;
}

.hidden { display: none; }
```

- [ ] **Step 2: Criar `/opt/opussb/static/app.js`**

```javascript
const POLL_INTERVAL_MS = 30000;

const STAGE_LABELS = {
  queued:           "Na fila...",
  extracting_audio: "Extraindo áudio...",
  uploading_audio:  "Enviando áudio...",
  transcribing:     "Transcrevendo com Whisper...",
  scoring:          "Claude analisando melhores momentos...",
  cutting:          "Cortando clipes...",
  reframing:        "Reframando para 9:16...",
  uploading_clips:  "Enviando clipes...",
  done:             "Pronto!",
  error:            "Erro no processamento",
};

let pollTimer = null;
let currentJobId = null;

async function submitUpload(e) {
  e.preventDefault();

  const form = document.getElementById("upload-form");
  const btn = document.getElementById("submit-btn");
  const formData = new FormData(form);

  btn.disabled = true;
  btn.textContent = "Enviando...";
  showSection("status-section");
  updateStatus({ status: "queued", progress: 5 });

  try {
    const resp = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await resp.json();

    if (!resp.ok) {
      showError(data.error || "Erro ao enviar o vídeo.");
      btn.disabled = false;
      btn.textContent = "Processar Vídeo";
      return;
    }

    currentJobId = data.job_id;
    startPolling(currentJobId);
  } catch (err) {
    showError("Falha na conexão com o servidor.");
    btn.disabled = false;
    btn.textContent = "Processar Vídeo";
  }
}

function startPolling(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollOnce(jobId);
  pollTimer = setInterval(() => pollOnce(jobId), POLL_INTERVAL_MS);
}

async function pollOnce(jobId) {
  try {
    const resp = await fetch(`/api/status/${jobId}`);
    const data = await resp.json();
    updateStatus(data);

    if (data.status === "done" || data.status === "error") {
      clearInterval(pollTimer);
      pollTimer = null;
      document.getElementById("submit-btn").disabled = false;
      document.getElementById("submit-btn").textContent = "Processar Novo Vídeo";
    }
  } catch (err) {
    console.warn("Polling falhou, tentando novamente...", err);
  }
}

function updateStatus(data) {
  const progress = data.progress || 0;
  document.getElementById("progress-fill").style.width = `${progress}%`;
  document.getElementById("stage-label").textContent =
    STAGE_LABELS[data.status] || data.status;

  if (data.status === "done" && data.clips && data.clips.length > 0) {
    showClips(data.clips);
  }

  if (data.status === "error") {
    showError(data.error || "Erro desconhecido.");
    if (data.partial_clips && data.partial_clips.length > 0) {
      showClips(data.partial_clips, true);
    }
  }
}

function showClips(urls, partial = false) {
  const grid = document.getElementById("clips-grid");
  grid.innerHTML = "";
  urls.forEach((url, i) => {
    const item = document.createElement("div");
    item.className = "clip-item";
    item.innerHTML = `
      <span>${partial ? "⚠️" : "✅"} Clipe ${i + 1}</span>
      <a href="${url}" target="_blank" download>Baixar</a>
    `;
    grid.appendChild(item);
  });
  showSection("clips-section");
}

function showError(msg) {
  const box = document.getElementById("error-box");
  box.textContent = msg;
  box.classList.remove("hidden");
}

function showSection(id) {
  document.getElementById(id).classList.remove("hidden");
}

document.getElementById("upload-form").addEventListener("submit", submitUpload);
document.getElementById("retry-btn").addEventListener("click", () => {
  document.getElementById("status-section").classList.add("hidden");
  document.getElementById("clips-section").classList.add("hidden");
  document.getElementById("error-box").classList.add("hidden");
  document.getElementById("clips-grid").innerHTML = "";
  document.getElementById("submit-btn").disabled = false;
  document.getElementById("submit-btn").textContent = "Processar Vídeo";
});
```

- [ ] **Step 3: Criar `/opt/opussb/static/index.html`**

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>OpusSB — Clipes Virais</title>
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <div class="container">
    <h1>OpusSB</h1>
    <p class="subtitle">Transforme seu vídeo em clipes virais 9:16</p>

    <div class="card">
      <form id="upload-form">
        <label for="video">Vídeo (mp4, mov, avi, mkv, webm — até 4GB)</label>
        <input type="file" id="video" name="video" accept=".mp4,.mov,.avi,.mkv,.webm" required />

        <div class="row">
          <div>
            <label for="min_duration">Duração mínima dos clipes (segundos)</label>
            <input type="number" id="min_duration" name="min_duration"
                   min="10" max="300" value="30" required />
          </div>
          <div>
            <label for="max_duration">Duração máxima dos clipes (segundos)</label>
            <input type="number" id="max_duration" name="max_duration"
                   min="10" max="300" value="90" required />
          </div>
        </div>

        <button type="submit" id="submit-btn">Processar Vídeo</button>
      </form>
    </div>

    <div id="status-section" class="card hidden">
      <strong>Processando...</strong>
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" id="progress-fill" style="width: 0%"></div>
      </div>
      <p class="stage-label" id="stage-label">Na fila...</p>
      <div id="error-box" class="error-box hidden"></div>
      <button id="retry-btn" style="margin-top:1rem; background:#333;">
        Tentar Novamente
      </button>
    </div>

    <div id="clips-section" class="card hidden">
      <strong>Clipes prontos para download:</strong>
      <div id="clips-grid" class="clips-grid"></div>
    </div>
  </div>

  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 4: Validar no browser**

```bash
systemctl restart opussb
```

Abrir `http://87.99.159.235` no browser. Verificar:
- Formulário carrega sem erros de console
- Campo de arquivo aceita apenas extensões de vídeo
- Seções de status e clips estão ocultas inicialmente

- [ ] **Step 5: Commit**

```bash
git add static/
git commit -m "feat: add frontend HTML/JS/CSS with upload form and polling UI"
```

---

## Task 11: N8N Workflow — Configuração Passo a Passo

> N8N está em `http://87.99.159.235:5678`. Acesse e crie um novo workflow chamado **"OpusSB Pipeline"**.

- [ ] **Step 0: Configurar credenciais no N8N**

O N8N no Docker não acessa o `.env` do sistema. As API keys devem ser cadastradas como credenciais N8N:

1. Ir em **Settings → Credentials → New Credential**
2. Criar **"RunPod API"**: tipo `Header Auth` → Header Name: `Authorization`, Value: `Bearer SUA_RUNPOD_API_KEY`
3. Criar **"Anthropic API"**: tipo `Header Auth` → Header Name: `x-api-key`, Value: `SUA_ANTHROPIC_API_KEY`

Nos nós HTTP Request, referenciar essas credenciais em vez de `$env.RUNPOD_API_KEY`.

- [ ] **Step 1: Criar Webhook Trigger**

1. Adicionar nó **Webhook**
2. HTTP Method: `POST`
3. Path: `opussb`
4. Authentication: `None`
5. Respond: `When Last Node Finishes`
6. Salvar — copiar a URL de produção gerada (ex: `http://localhost:5678/webhook/opussb`)
7. Confirmar que bate com `N8N_WEBHOOK_URL` no `.env`

- [ ] **Step 2: Nó "RunPod Submit"**

1. Adicionar nó **HTTP Request** após Webhook
2. Name: `RunPod Submit`
3. Method: `POST`
4. URL: `https://api.runpod.ai/v2/fczh1t4sx02j1s/run`
5. Authentication: `Predefined Credential Type` → selecionar **"RunPod API"** (criada no Step 0)
6. Body: JSON

```json
{
  "input": {
    "audio": "={{ $json.audio_s3_url }}"
  }
}
```

- [ ] **Step 3: Nó "Save RunPod Job ID"**

1. Adicionar nó **HTTP Request**
2. Name: `Save RunPod Job ID`
3. Method: `PATCH`
4. URL: `http://127.0.0.1:5001/api/internal/jobs/{{ $('Webhook').item.json.job_id }}`
5. Body: JSON

```json
{
  "runpod_job_id": "={{ $json.id }}",
  "status": "transcribing"
}
```

- [ ] **Step 4: Nó "Wait 30s"**

1. Adicionar nó **Wait**
2. Name: `Wait 30s`
3. Resume: `After Time Interval`
4. Wait Amount: `30`
5. Wait Unit: `Seconds`

- [ ] **Step 5: Nó "Check Attempts" (Code)**

1. Adicionar nó **Code** após Wait
2. Name: `Check Attempts`
3. Language: JavaScript
4. Código:

```javascript
const attempts = $node["Wait 30s"].context.attempts || 0;
if (attempts > 40) {
  throw new Error("RunPod timeout após 20 minutos — job_id: " + $('Webhook').item.json.job_id);
}
$node["Wait 30s"].context.attempts = attempts + 1;
return $input.all();
```

- [ ] **Step 6: Nó "RunPod Status"**

1. Adicionar nó **HTTP Request**
2. Name: `RunPod Status`
3. Method: `GET`
4. URL: `https://api.runpod.ai/v2/fczh1t4sx02j1s/status/{{ $('Save RunPod Job ID').item.json.id }}`
5. Authentication: `Predefined Credential Type` → selecionar **"RunPod API"** (criada no Step 0)

- [ ] **Step 7: Nó "Is Completed?" (IF)**

1. Adicionar nó **IF**
2. Name: `Is Completed?`
3. Condition: `String` → `{{ $json.status }}` `equals` `COMPLETED`
4. Conectar **false** de volta ao nó **Wait 30s** (loop)
5. Conectar **true** ao próximo nó

- [ ] **Step 8: Nó "Update Status Scoring"**

1. Adicionar nó **HTTP Request** na branch `true` do IF
2. Name: `Update Status Scoring`
3. Method: `PATCH`
4. URL: `http://127.0.0.1:5001/api/internal/jobs/{{ $('Webhook').item.json.job_id }}`
5. Body: JSON

```json
{
  "status": "scoring",
  "progress": 60
}
```

- [ ] **Step 9: Nó "Claude Score Clips"**

1. Adicionar nó **HTTP Request**
2. Name: `Claude Score Clips`
3. Method: `POST`
4. URL: `https://api.anthropic.com/v1/messages`
5. Authentication: `Predefined Credential Type` → selecionar **"Anthropic API"** (criada no Step 0)
6. Headers adicionais:
   - `anthropic-version`: `2023-06-01`
   - `content-type`: `application/json`
6. Body: JSON

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 4096,
  "messages": [
    {
      "role": "user",
      "content": "Você receberá a transcrição de um vídeo com timestamps por segmento.\nIdentifique os 5 a 10 melhores trechos para clipes virais.\nCada trecho deve ter duração entre {{ $('Webhook').item.json.min_duration }}s e {{ $('Webhook').item.json.max_duration }}s.\n\nCritérios: insight forte, momento emocional, pergunta-resposta completa, história curta, afirmação surpreendente. O trecho DEVE começar e terminar em frase completa. Nunca corte no meio de uma ideia.\n\nRetorne APENAS um JSON válido no formato:\n[\n  { \"start\": 123.4, \"end\": 187.2, \"score\": 9, \"reason\": \"...\" }\n]\n\nTranscrição:\n{{ JSON.stringify($json.output.segments) }}"
    }
  ]
}
```

- [ ] **Step 10: Nó "Extract Clips JSON" (Code)**

1. Adicionar nó **Code**
2. Name: `Extract Clips JSON`
3. Código:

```javascript
const content = $json.content[0].text;
// Extrai o JSON mesmo que Claude adicione texto antes/depois
const match = content.match(/\[[\s\S]*\]/);
if (!match) throw new Error("Claude não retornou JSON válido: " + content.substring(0, 200));
const clips = JSON.parse(match[0]);
if (!Array.isArray(clips) || clips.length === 0) {
  throw new Error("Claude retornou array vazio");
}
return [{ json: { clips_json: JSON.stringify(clips) } }];
```

- [ ] **Step 11: Nó "Flask Callback"**

1. Adicionar nó **HTTP Request**
2. Name: `Flask Callback`
3. Method: `POST`
4. URL: `http://127.0.0.1:5001/api/callback`
5. Body: JSON

```json
{
  "job_id": "={{ $('Webhook').item.json.job_id }}",
  "transcript": "={{ JSON.stringify($('RunPod Status').item.json.output.segments) }}",
  "clips_json": "={{ $json.clips_json }}"
}
```

- [ ] **Step 12: Ativar workflow e testar manualmente**

1. Clicar **Save** e depois **Activate**
2. Testar com curl da VPS:

```bash
curl -X POST http://localhost:5678/webhook/opussb \
  -H "Content-Type: application/json" \
  -d '{"job_id":"test-n8n","audio_s3_url":"https://example.com/audio.mp3","min_duration":30,"max_duration":90}'
```

Esperado: workflow executa, nó "RunPod Submit" aciona (pode falhar com audio URL fake — isso é esperado no teste).

- [ ] **Step 13: Commit do README do workflow**

```bash
cat > /opt/opussb/N8N_WORKFLOW.md << 'EOF'
# N8N Workflow — OpusSB Pipeline

Workflow ativo em N8N (http://localhost:5678).
Nome: "OpusSB Pipeline"
Webhook path: /webhook/opussb

Nós (em ordem):
1. Webhook Trigger
2. RunPod Submit
3. Save RunPod Job ID → PATCH /api/internal/jobs/{job_id}
4. Wait 30s → loop de polling
5. Check Attempts (Code) → timeout após 40 tentativas
6. RunPod Status (GET)
7. Is Completed? (IF)
8. Update Status Scoring → PATCH /api/internal/jobs/{job_id}
9. Claude Score Clips (POST /v1/messages)
10. Extract Clips JSON (Code)
11. Flask Callback → POST /api/callback
EOF

git add N8N_WORKFLOW.md
git commit -m "docs: add N8N workflow documentation"
```

---

## Task 12: Teste End-to-End com Vídeo Real

- [ ] **Step 1: Verificar que todos os serviços estão ativos**

```bash
systemctl status opussb nginx
curl http://localhost/health
```

Esperado: `{"status": "ok"}`

- [ ] **Step 2: Fazer upload via curl de um vídeo de teste**

```bash
# Usar o vídeo de teste baixado na Task 8
curl -X POST http://localhost/api/upload \
  -F "video=@/tmp/test_input.mp4" \
  -F "min_duration=15" \
  -F "max_duration=60"
```

Esperado: `{"job_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}`

- [ ] **Step 3: Monitorar status do job**

```bash
JOB_ID="<job_id_retornado_acima>"
watch -n 10 "curl -s http://localhost/api/status/$JOB_ID | python3 -m json.tool"
```

Esperado: status evolui de `queued` → `extracting_audio` → `uploading_audio` → `transcribing` → `scoring` → `cutting` → `reframing` → `uploading_clips` → `done`

- [ ] **Step 4: Verificar clipes no S3**

```bash
python3 -c "
import config, s3_client
# lista os clipes do job
import boto3
from botocore.config import Config
s3 = boto3.client('s3',
  endpoint_url=config.S3_ENDPOINT,
  aws_access_key_id=config.S3_ACCESS_KEY,
  aws_secret_access_key=config.S3_SECRET_KEY,
  config=Config(signature_version='s3v4'))
resp = s3.list_objects_v2(Bucket=config.S3_BUCKET, Prefix='jobs/')
for obj in resp.get('Contents', []):
    print(obj['Key'], obj['Size'])
"
```

Esperado: lista arquivos `jobs/{job_id}/clips/clip_0X_9x16.mp4`

- [ ] **Step 5: Abrir o frontend no browser e confirmar o fluxo completo**

Abrir `http://87.99.159.235` no browser.
- Fazer upload de um vídeo real
- Acompanhar a barra de progresso
- Confirmar que os links de download aparecem ao final
- Baixar um clipe e verificar: resolução 9:16, áudio presente, começa e termina em frase completa

- [ ] **Step 6: Commit final**

```bash
cd /opt/opussb
git add -A
git commit -m "feat: OpusSB MVP complete — full e2e pipeline operational"
```

---

## Checklist de Validação Final

- [ ] `curl http://localhost/health` retorna `{"status": "ok"}`
- [ ] Upload de formato inválido retorna HTTP 400
- [ ] Segundo upload enquanto job ativo retorna HTTP 429 com mensagem correta
- [ ] Status evolui por todos os estados até `done`
- [ ] Clipes entregues em 9:16 (verificar com ffprobe)
- [ ] Clipes têm áudio
- [ ] Clipes começam e terminam em frase completa
- [ ] Diretório `/opt/opussb/jobs/` está vazio após conclusão (cleanup)
- [ ] `pytest tests/ -v` — todos os testes passam
