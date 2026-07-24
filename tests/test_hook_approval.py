"""Parte 4 do pipeline de podcast (§39): tela de aprovação do gancho.
O foco de regressão segue sendo os DOIS lados da contagem de ativos ('gancho_aprovado'
fica fora do count e do reaper; clipe preso continua ceifado) e as guardas das rotas
novas. Vídeo sintético pequeno via ffmpeg; preview testado com Range de verdade (206)."""
import json
import os
import sqlite3
import subprocess

import pytest
from werkzeug.security import generate_password_hash

import config
import db


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mp4(tmp_path, name, dur):
    out = str(tmp_path / name)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc2=size=160x90:rate=10:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", out], check=True)
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


HOOKS = [
    {"start": 10.0, "end": 55.0, "text": "gancho um", "reason": "abre bem"},
    {"start": 20.0, "end": 60.0, "text": "gancho dois", "reason": "boa história"},
    {"start": 5.0, "end": 40.0, "text": "gancho três", "reason": "opinião forte"},
]


def _podcast_aguardando(tmp_path, job_id="TEST-hookok", owner="t@t", dur=70):
    """Job de podcast estacionado em 'aguardando_aprovacao' com 3 ganchos e um 16:9
    sintético de `dur`s no lugar do original (o approve valida contra a duração real)."""
    src = _mp4(tmp_path, f"{job_id}.mp4", dur)
    db.create_podcast_job(job_id, "pod.mp4", src, owner, "v1")
    db.update_job(job_id, status="aguardando_aprovacao", progress=85,
                  hooks_json=json.dumps({"hooks": HOOKS, "model": "m", "cost_usd": 0.01}))
    return job_id


# ─── Camada db: os dois lados de count/reap com o estado novo ────────────────

def test_gancho_aprovado_fora_da_contagem_e_clipe_dentro():
    db.create_podcast_job("TEST-pod4", "p.mp4", "/tmp/TEST-p4.mp4", "t@t", "v1")
    db.update_job("TEST-pod4", status="gancho_aprovado")
    assert db.count_active_jobs() == 0

    db.create_job("TEST-clip4", "c.mp4", 30, 90, "/tmp/TEST-c4.mp4")
    assert db.count_active_jobs() == 1
    db.update_job("TEST-clip4", status="done")
    assert db.count_active_jobs() == 0


def test_reaper_ignora_gancho_aprovado_e_segue_matando_clipe_preso():
    db.create_podcast_job("TEST-pod4", "p.mp4", "/tmp/TEST-p4.mp4", "t@t", "v1")
    db.update_job("TEST-pod4", status="gancho_aprovado")
    db.create_job("TEST-clip4", "c.mp4", 30, 90, "/tmp/TEST-c4.mp4")
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00'")
    con.commit()
    con.close()

    assert db.reap_stale_jobs(30) == 1
    assert db.get_job("TEST-pod4")["status"] == "gancho_aprovado"
    assert db.get_job("TEST-clip4")["status"] == "error"


# ─── Guardas comuns das 3 rotas ───────────────────────────────────────────────

def test_rotas_atras_da_flag_e_do_login(client, monkeypatch, tmp_path):
    jid = _podcast_aguardando(tmp_path)
    monkeypatch.setattr(config, "PODCAST_ENABLED", False)
    _login(client, "t@t")
    assert client.get(f"/api/podcast/{jid}/hooks").status_code == 404
    assert client.get(f"/api/podcast/{jid}/source").status_code == 404
    assert client.post(f"/api/podcast/{jid}/approve", json={}).status_code == 404

    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    client.post("/api/logout")
    assert client.get(f"/api/podcast/{jid}/hooks").status_code == 401
    assert client.get(f"/api/podcast/{jid}/source").status_code == 401
    assert client.post(f"/api/podcast/{jid}/approve", json={}).status_code == 401


def test_rotas_rejeitam_job_de_clipes(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    db.create_job("TEST-clip4", "c.mp4", 30, 90, "/tmp/TEST-c4.mp4")
    _login(client, "t@t")
    assert client.get("/api/podcast/TEST-clip4/hooks").status_code == 400
    assert client.get("/api/podcast/TEST-clip4/source").status_code == 400
    assert client.post("/api/podcast/TEST-clip4/approve", json={}).status_code == 400
    assert client.get("/api/podcast/TEST-nao-existe/hooks").status_code == 404


# ─── GET /hooks ───────────────────────────────────────────────────────────────

def test_hooks_devolve_sugestoes_e_choice(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = _podcast_aguardando(tmp_path)
    _login(client, "t@t")

    body = client.get(f"/api/podcast/{jid}/hooks").get_json()
    assert body["status"] == "aguardando_aprovacao"
    assert [h["text"] for h in body["hooks"]] == ["gancho um", "gancho dois", "gancho três"]
    assert body["hook_choice"] is None
    assert body["source_available"] is True
    assert body["fps"] is None  # _podcast_aguardando não informa fps (jobs antigos)

    r = client.post(f"/api/podcast/{jid}/approve", json={"index": 1, "start": 18.5, "end": 61.0})
    assert r.status_code == 200, r.get_json()
    body = client.get(f"/api/podcast/{jid}/hooks").get_json()
    assert body["status"] == "gancho_aprovado"
    assert body["hook_choice"]["index"] == 1


# ─── GET /source (preview com Range, sem re-encode) ───────────────────────────

def test_source_serve_com_range_206(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = _podcast_aguardando(tmp_path, dur=2)
    _login(client, "t@t")

    r = client.get(f"/api/podcast/{jid}/source", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.data) == 100
    assert r.headers["Content-Range"].startswith("bytes 0-99/")

    r = client.get(f"/api/podcast/{jid}/source")
    assert r.status_code == 200
    assert r.headers["Accept-Ranges"] == "bytes"


def test_source_409_sem_arquivo(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    db.create_podcast_job("TEST-semarq", "p.mp4", "/tmp/TEST-sumiu.mp4", "t@t", "v1")
    _login(client, "t@t")
    assert client.get("/api/podcast/TEST-semarq/source").status_code == 409


# ─── POST /approve ────────────────────────────────────────────────────────────

def test_approve_feliz_grava_choice_e_estaciona(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = _podcast_aguardando(tmp_path)
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/approve",
                    json={"index": 0, "start": 8.25, "end": 56.0})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["status"] == "gancho_aprovado"

    job = db.get_job(jid)
    assert job["status"] == "gancho_aprovado"
    choice = json.loads(job["hook_choice"])
    assert choice["index"] == 0
    assert choice["start"] == 8.25 and choice["end"] == 56.0
    assert choice["approved_by"] == "t@t"
    assert choice["approved_at"]
    assert db.count_active_jobs() == 0


def test_approve_validacoes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = _podcast_aguardando(tmp_path)
    _login(client, "t@t")
    ok = {"index": 0, "start": 10.0, "end": 55.0}

    for ruim in [{}, {**ok, "index": 5}, {**ok, "index": "0"}, {**ok, "index": True},
                 {**ok, "start": "abc"}, {**ok, "start": -1.0},
                 {**ok, "end": 9.0},                       # fim antes do início
                 {**ok, "end": 15.0},                      # 5s: abaixo da guarda de 10s
                 {**ok, "end": 105.0},                     # 95s: acima da guarda de 90s
                 {**ok, "start": 30.0, "end": 85.0}]:      # passa dos 70s do vídeo
        r = client.post(f"/api/podcast/{jid}/approve", json=ruim)
        assert r.status_code == 400, (ruim, r.get_json())

    assert db.get_job(jid)["status"] == "aguardando_aprovacao"


def test_approve_exige_status_e_dono(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = _podcast_aguardando(tmp_path)
    ok = {"index": 0, "start": 10.0, "end": 55.0}

    _login(client, "outro@test")
    assert client.post(f"/api/podcast/{jid}/approve", json=ok).status_code == 403
    client.post("/api/logout")

    _login(client, "adm@test", admin=True)
    r = client.post(f"/api/podcast/{jid}/approve", json=ok)
    assert r.status_code == 200, r.get_json()

    # Já aprovado: segunda aprovação (de qualquer um) leva 409, não sobrescreve
    r = client.post(f"/api/podcast/{jid}/approve", json={**ok, "index": 2})
    assert r.status_code == 409
    assert json.loads(db.get_job(jid)["hook_choice"])["index"] == 0
