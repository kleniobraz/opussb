"""Parte 2 do pipeline de podcast (§39): biblioteca de vinhetas + entrada do job.
O foco de regressão são os DOIS lados da contagem de ativos: o podcast 'uploaded'
fica FORA (não trava a fila de clipes nem morre no reaper) e o clipe em
processamento continua DENTRO. Vídeos sintéticos via ffmpeg; S3 e pipeline mockados."""
import os
import sqlite3
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import config
import db


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mp4(tmp_path, name, size="640x360", rate=30, dur=1):
    out = str(tmp_path / name)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", out], check=True)
    return out


def _mp4_vfr(tmp_path):
    """Concat-copy de dois segmentos com fps diferentes: produz deltas de DTS mistos
    de verdade (a mesma anomalia que a detecção precisa pegar)."""
    a = _mp4(tmp_path, "seg30.mp4", rate=30)
    b = _mp4(tmp_path, "seg15.mp4", rate=15)
    lst = tmp_path / "concat.txt"
    lst.write_text(f"file '{a}'\nfile '{b}'\n")
    out = str(tmp_path / "vfr.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", out], check=True)
    return out


@pytest.fixture
def client():
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _login(client, email, admin=False):
    db.upsert_user(email, generate_password_hash("senha-de-teste"), must_change=False)
    if admin:
        con = sqlite3.connect(os.environ["DB_PATH"])
        con.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
        con.commit()
        con.close()
    r = client.post("/api/login", json={"email": email, "password": "senha-de-teste"})
    assert r.status_code == 200, r.get_json()


def _vinheta_semeada():
    db.add_vinheta("vtest", "Vinheta de teste", "assets/vinhetas/vtest.mp4", 10.0)
    return "vtest"


# ─── Camada db ────────────────────────────────────────────────────────────────

def test_vinhetas_crud():
    db.init_vinhetas()
    db.add_vinheta("v1", "Carbone", "assets/vinhetas/v1.mp4", 10.0)
    assert [v["id"] for v in db.list_vinhetas()] == ["v1"]
    db.add_vinheta("v1", "Carbone v2", "assets/vinhetas/v1.mp4", 12.0)
    assert db.get_vinheta("v1")["label"] == "Carbone v2"
    assert len(db.list_vinhetas()) == 1
    db.delete_vinheta("v1")
    assert db.get_vinheta("v1") is None


def test_uploaded_fora_da_contagem_e_clipe_dentro():
    db.create_podcast_job("TEST-pod", "p.mp4", "/tmp/TEST-p.mp4", "t@t", "v1")
    assert db.count_active_jobs() == 0

    db.create_job("TEST-clip", "c.mp4", 30, 90, "/tmp/TEST-c.mp4")
    assert db.count_active_jobs() == 1
    db.update_job("TEST-clip", status="reframing")
    assert db.count_active_jobs() == 1
    db.update_job("TEST-clip", status="done")
    assert db.count_active_jobs() == 0


def test_create_podcast_job_grava_e_le_fps():
    db.create_podcast_job("TEST-podfps", "p.mp4", "/tmp/TEST-podfps.mp4", "t@t", "v1", fps=29.97)
    assert db.get_job("TEST-podfps")["fps"] == pytest.approx(29.97)

    # sem informar fps (default None), pra não quebrar chamadas antigas do pipeline
    db.create_podcast_job("TEST-podfps2", "p.mp4", "/tmp/TEST-podfps2.mp4", "t@t", "v1")
    assert db.get_job("TEST-podfps2")["fps"] is None


def test_reaper_ignora_uploaded_e_segue_matando_clipe_preso():
    db.create_podcast_job("TEST-pod", "p.mp4", "/tmp/TEST-p.mp4", "t@t", "v1")
    db.create_job("TEST-clip", "c.mp4", 30, 90, "/tmp/TEST-c.mp4")
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00'")
    con.commit()
    con.close()

    assert db.reap_stale_jobs(30) == 1
    assert db.get_job("TEST-pod")["status"] == "uploaded"
    assert db.get_job("TEST-clip")["status"] == "error"


# ─── Rotas de vinheta ─────────────────────────────────────────────────────────

def test_vinheta_upload_exige_admin(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    assert client.get("/api/vinhetas").status_code == 401

    _login(client, "user@test")
    assert client.get("/api/vinhetas").status_code == 200
    src = _mp4(tmp_path, "vin.mp4", dur=2)
    with open(src, "rb") as fh:
        r = client.post("/api/vinhetas", data={"file": (fh, "vin.mp4")},
                        content_type="multipart/form-data")
    assert r.status_code == 403


def test_admin_sobe_vinheta_e_lista(client, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    up = mocker.patch("s3_client.upload_file")
    _login(client, "adm@test", admin=True)

    src = _mp4(tmp_path, "vin.mp4", dur=2)
    with open(src, "rb") as fh:
        r = client.post("/api/vinhetas", data={"file": (fh, "vin.mp4"), "label": "Carbone"},
                        content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    v = r.get_json()["vinheta"]
    assert v["label"] == "Carbone" and 1.5 < v["duration"] < 2.5
    up.assert_called_once()
    key = up.call_args[0][1]
    assert key.startswith("assets/vinhetas/") and key.endswith(".mp4")

    r = client.get("/api/vinhetas")
    assert [x["id"] for x in r.get_json()["vinhetas"]] == [v["id"]]


def test_vinheta_delete_degrada_e_e_idempotente(client, monkeypatch, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    dele = mocker.patch("s3_client.delete_object")
    _login(client, "adm@test", admin=True)
    v_id = _vinheta_semeada()

    assert client.delete(f"/api/vinhetas/{v_id}").status_code == 200
    assert db.get_vinheta(v_id) is None
    dele.assert_called_once_with("assets/vinhetas/vtest.mp4")
    assert client.delete(f"/api/vinhetas/{v_id}").status_code == 200


def test_flag_off_rotas_404(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", False)
    _login(client, "adm@test", admin=True)
    assert client.get("/api/vinhetas").status_code == 404
    assert client.post("/api/vinhetas", data={}).status_code == 404
    assert client.post("/api/podcast/upload", data={}).status_code == 404


# ─── Upload de podcast ────────────────────────────────────────────────────────

def test_podcast_upload_rejeita_vertical(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    _login(client, "user@test")
    v_id = _vinheta_semeada()

    src = _mp4(tmp_path, "vert.mp4", size="360x640")
    with open(src, "rb") as fh:
        r = client.post("/api/podcast/upload",
                        data={"video": (fh, "vert.mp4"), "vinheta_id": v_id},
                        content_type="multipart/form-data")
    assert r.status_code == 400
    assert "16:9" in r.get_json()["error"]
    assert db.list_jobs() == []


def test_podcast_upload_rejeita_vfr_com_mensagem_acionavel(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    _login(client, "user@test")
    v_id = _vinheta_semeada()

    src = _mp4_vfr(tmp_path)
    with open(src, "rb") as fh:
        r = client.post("/api/podcast/upload",
                        data={"video": (fh, "vfr.mp4"), "vinheta_id": v_id},
                        content_type="multipart/form-data")
    assert r.status_code == 422
    # mensagem pro produtor: diz o que FAZER (export CFR), não só o nome técnico
    assert "frame rate constante" in r.get_json()["error"]
    assert db.list_jobs() == []


def test_podcast_upload_exige_vinheta_valida(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    _login(client, "user@test")

    src = _mp4(tmp_path, "pod.mp4")
    with open(src, "rb") as fh:
        r = client.post("/api/podcast/upload",
                        data={"video": (fh, "pod.mp4"), "vinheta_id": "nao-existe"},
                        content_type="multipart/form-data")
    assert r.status_code == 400
    assert "vinheta" in r.get_json()["error"].lower()


def test_podcast_upload_feliz_estaciona_sem_disparar_pipeline(client, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    thread1 = mocker.patch("processor.run_thread1")
    _login(client, "user@test")
    v_id = _vinheta_semeada()

    src = _mp4(tmp_path, "pod.mp4", size="640x360", dur=2)
    with open(src, "rb") as fh:
        r = client.post("/api/podcast/upload",
                        data={"video": (fh, "pod.mp4"), "vinheta_id": v_id},
                        content_type="multipart/form-data")
    assert r.status_code == 201, r.get_json()
    job = db.get_job(r.get_json()["job_id"])
    assert job["job_type"] == "podcast" and job["status"] == "uploaded"
    assert job["vinheta_id"] == v_id and job["captions"] == 0
    assert os.path.exists(job["local_path"])
    # fps veio do probe_av_signature (reusado, sem probe novo) e bate com o vídeo gerado
    assert job["fps"] == pytest.approx(30.0)
    thread1.assert_not_called()
    assert db.count_active_jobs() == 0

    # o outro lado: com o podcast estacionado, upload de CLIPE não leva 429 e conta como ativo
    src2 = _mp4(tmp_path, "clip.mp4", size="640x360")
    with open(src2, "rb") as fh:
        r2 = client.post("/api/upload",
                         data={"video": (fh, "clip.mp4"), "min_duration": "30", "max_duration": "90"},
                         content_type="multipart/form-data")
    assert r2.status_code == 202, r2.get_json()
    assert db.count_active_jobs() == 1
