"""Bifurcação por job_type (BACKEND_CONTEXT §39, Parte 1): as colunas novas
jobs.job_type (DEFAULT 'clips') e jobs.vinheta_id (NULL) não podem mudar NADA no
comportamento do pipeline de clipes. Cobre a migração sobre schema antigo (backfill
dos jobs existentes) e o caminho normal create/get/update com o schema novo."""
import os
import sqlite3

import db


def test_migracao_backfill_schema_antigo(tmp_path):
    """Simula um banco de produção antigo (tabela jobs SEM as colunas novas, com um
    job existente) e roda init_db(): as colunas entram via _ensure_column e a linha
    pré-existente ganha job_type='clips' e vinheta_id NULL pelo DEFAULT."""
    old_db = str(tmp_path / "antigo.db")
    conn = sqlite3.connect(old_db)
    conn.execute("""
        CREATE TABLE jobs (
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
    conn.execute(
        "INSERT INTO jobs (job_id, filename, status) VALUES ('TEST-legado', 'TEST.mp4', 'done')"
    )
    conn.commit()
    conn.close()

    db_path_da_fixture = os.environ["DB_PATH"]
    os.environ["DB_PATH"] = old_db
    try:
        db.init_db()
        job = db.get_job("TEST-legado")
        assert job["job_type"] == "clips"
        assert job["vinheta_id"] is None
        # idempotência: rodar de novo não pode quebrar (deploy repetido)
        db.init_db()
        assert db.get_job("TEST-legado")["job_type"] == "clips"
    finally:
        os.environ["DB_PATH"] = db_path_da_fixture


def test_create_job_continua_igual_e_nasce_clips():
    """O INSERT do create_job não menciona as colunas novas: todo job de clipe futuro
    nasce job_type='clips' e vinheta_id NULL pelo DEFAULT do schema, sem mudar código."""
    db.create_job("TEST-novo", "TEST.mp4", 30, 90, "/tmp/TEST.mp4", user_email="test@test")
    job = db.get_job("TEST-novo")
    assert job["job_type"] == "clips"
    assert job["vinheta_id"] is None
    assert job["status"] == "queued"
    # list_jobs (SELECT *) devolve os campos novos sem quebrar
    assert db.list_jobs("test@test")[0]["job_type"] == "clips"


def test_update_job_generico_segue_funcionando():
    """update_job por kwargs segue intacto pro fluxo de clipes, e já aceita as colunas
    novas (que o pipeline de podcast vai usar nas próximas partes)."""
    db.create_job("TEST-upd", "TEST.mp4", 30, 90, "/tmp/TEST.mp4")
    db.update_job("TEST-upd", status="extracting_audio", progress=10)
    job = db.get_job("TEST-upd")
    assert job["status"] == "extracting_audio" and job["job_type"] == "clips"

    db.update_job("TEST-upd", job_type="podcast", vinheta_id="TEST-vinheta")
    job = db.get_job("TEST-upd")
    assert job["job_type"] == "podcast" and job["vinheta_id"] == "TEST-vinheta"
