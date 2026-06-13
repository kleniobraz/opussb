import os

# Set before any module import — config.py reads at import time
os.environ.setdefault("S3_ENDPOINT", "https://test.s3.example.com")
os.environ.setdefault("S3_BUCKET", "test-bucket")
os.environ.setdefault("S3_ACCESS_KEY", "AKIATEST")
os.environ.setdefault("S3_SECRET_KEY", "secrettest")
os.environ.setdefault("RUNPOD_API_KEY", "rp_test")
os.environ.setdefault("RUNPOD_ENDPOINT_ID", "testendpoint")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/opussb")
os.environ.setdefault("MAX_CONCURRENT_JOBS", "1")
os.environ.setdefault("FLASK_PORT", "5001")

import pytest
import importlib


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    jobs_dir = str(tmp_path / "jobs")
    os.environ["DB_PATH"] = db_path
    os.environ["JOB_DIR"] = jobs_dir
    os.makedirs(jobs_dir, exist_ok=True)

    import db
    importlib.reload(db)
    db.init_db()
    yield
    if os.path.exists(db_path):
        os.remove(db_path)
