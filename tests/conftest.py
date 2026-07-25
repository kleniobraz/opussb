import os

# Set before any module import — config.py reads at import time.
#
# S3 e o gate de lifecycle entram por ATRIBUIÇÃO DIRETA, não setdefault: setdefault não
# sobrescreve variável já exportada no shell, então quem fizesse `set -a; . .env; set +a`
# (hábito de depuração na VPS) rodaria a suíte com as credenciais REAIS, e o `import app` de
# vários módulos de teste dispararia ensure_lifecycle_rule contra o bucket de PRODUÇÃO, com
# PUT se a regra divergisse. Violação da regra 8 por acidente de shell. Aqui o teste nunca
# alcança o bucket real, aconteça o que acontecer no ambiente.
os.environ["S3_ENDPOINT"] = "https://test.s3.example.com"
os.environ["S3_BUCKET"] = "test-bucket"
os.environ["S3_ACCESS_KEY"] = "AKIATEST"
os.environ["S3_SECRET_KEY"] = "secrettest"
os.environ["SOURCE16X9_LIFECYCLE_ENSURE"] = "false"
os.environ.setdefault("RUNPOD_API_KEY", "rp_test")
os.environ.setdefault("RUNPOD_ENDPOINT_ID", "testendpoint")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/opussb")
os.environ.setdefault("MAX_CONCURRENT_JOBS", "1")
os.environ.setdefault("FLASK_PORT", "5001")
os.environ.setdefault("JOB_DIR", "/tmp/test-opussb-jobs")
# Worktree de staging tem S3_KEY_PREFIX=staging/ no .env; os testes assertam keys
# cruas, então o pino garante suíte determinística em qualquer worktree (load_dotenv
# não sobrescreve env já setada).
os.environ.setdefault("S3_KEY_PREFIX", "")
# Mesma razão do S3_KEY_PREFIX acima: desde que as rotas de download passaram pelo
# get_presigned_download_url, a suíte ficaria dependente da flag do .env. Com ela ligada em
# produção (o caso desejado), o mock de get_presigned_url em test_podcast_audio deixaria de
# interceptar (o código chamaria get_presigned_url_accelerate) e a suíte ficaria vermelha sem
# nada estar quebrado. O pino mantém o teste determinístico com a flag em qualquer valor.
os.environ.setdefault("S3_ACCELERATE_DOWNLOADS", "false")

import pytest
import importlib


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    jobs_dir = str(tmp_path / "jobs")

    old_db_path = os.environ.get("DB_PATH")
    old_jobs_dir = os.environ.get("JOB_DIR")

    os.environ["DB_PATH"] = db_path
    os.environ["JOB_DIR"] = jobs_dir
    os.makedirs(jobs_dir, exist_ok=True)

    import db
    importlib.reload(db)
    db.init_db()
    yield

    if os.path.exists(db_path):
        os.remove(db_path)

    if old_db_path is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = old_db_path

    if old_jobs_dir is None:
        os.environ.pop("JOB_DIR", None)
    else:
        os.environ["JOB_DIR"] = old_jobs_dir
