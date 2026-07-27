"""Autorização NO SERVIDOR das rotas que a auditoria de 2026-07-27 encontrou permissivas.

POR QUE ESTA SUÍTE EXISTE: o bundle JavaScript público revela as 44 rotas da API, então um
usuário legítimo (ou uma sessão roubada) tem o mapa pronto e pode ir direto testar autorização
em cada uma, sem adivinhar nome de rota. Esconder botão no front não protege nada; o que
protege é o 403 na rota, e é isso que estes testes travam.

Cada teste aqui corresponde a um achado real, não a um caso hipotético.
"""
import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import config
import db


@pytest.fixture(autouse=True)
def podcast_ligado(monkeypatch):
    """PODCAST_ENABLED gateia todas as rotas de podcast com 404 ANTES do gate de dono. Sem
    ligar aqui, os testes de 403 passariam por acidente (404 também não é 200)."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)


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


# ─── /audio: POST que GASTA DINHEIRO no projeto de outro ─────────────────────

def _podcast_montado(job_id="TESTaudio", dono="dono@test"):
    db.create_podcast_job(job_id, "ep.mp4", f"/tmp/{job_id}.mp4", dono, "vtest")
    db.update_job(job_id, status="done", podcast_final_key=f"jobs/{job_id}/podcast/final.mp4")
    return job_id


def test_audio_de_outro_usuario_da_403_NA_ROTA(client, mocker, monkeypatch):
    """Era a ÚNICA das cinco rotas de podcast sem gate de dono, e a única que gasta dinheiro:
    baixa o episódio inteiro do bucket pago e roda ffmpeg. Qualquer usuário disparava extração
    nos projetos de todos os outros."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    disparou = mocker.patch("podcast_audio.run_podcast_audio_extract")
    db.add_vinheta("vtest", "v", "assets/vinhetas/v.mp4", 5.0)
    jid = _podcast_montado(dono="dono@test")
    _login(client, "invasor@test")

    r = client.post(f"/api/podcast/{jid}/audio")

    assert r.status_code == 403
    assert "outro usuário" in r.get_json()["error"]
    disparou.assert_not_called(), "disparou extração paga no projeto de outro"


def test_audio_do_proprio_dono_passa(client, mocker, monkeypatch):
    """O gate não pode quebrar o caso legítimo."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    mocker.patch("podcast_audio.run_podcast_audio_extract")
    mocker.patch("app._cache_quente", return_value=False)
    db.add_vinheta("vtest", "v", "assets/vinhetas/v.mp4", 5.0)
    jid = _podcast_montado(dono="dono@test")
    _login(client, "dono@test")

    assert client.post(f"/api/podcast/{jid}/audio").status_code in (200, 202)


def test_audio_admin_pode_de_outro(client, mocker, monkeypatch):
    """Admin é a exceção em todas as outras quatro rotas; aqui também."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    mocker.patch("podcast_audio.run_podcast_audio_extract")
    mocker.patch("app._cache_quente", return_value=False)
    db.add_vinheta("vtest", "v", "assets/vinhetas/v.mp4", 5.0)
    jid = _podcast_montado(dono="dono@test")
    _login(client, "chefe@test", admin=True)

    assert client.post(f"/api/podcast/{jid}/audio").status_code in (200, 202)


# ─── glossário: o servidor bloqueava o VERBO e não o EFEITO ──────────────────

def test_PUT_do_glossario_exige_admin(client):
    """O DELETE já era admin-only e o front escondia o botão de excluir. Mas o PUT chama
    db.add_glossary, que é UPSERT: qualquer usuário sobrescrevia o conteúdo de qualquer termo,
    com o mesmo efeito prático de apagar. É o único ponto onde o front escondia um controle que
    o servidor não protegia de forma equivalente."""
    db.add_glossary("t1", "Quatro5", "marca", "grafia oficial", created_by="chefe@test")
    _login(client, "comum@test")

    r = client.put("/api/glossary/t1", json={"term": "SOBRESCRITO", "category": "x"})

    assert r.status_code == 403
    assert db.get_glossary("t1")["term"] == "Quatro5", "o termo foi sobrescrito por não-admin"


def test_PUT_do_glossario_admin_passa(client):
    db.add_glossary("t1", "Quatro5", "marca", "n", created_by="chefe@test")
    _login(client, "chefe@test", admin=True)

    assert client.put("/api/glossary/t1", json={"term": "Grupo Quatro5"}).status_code == 200
    assert db.get_glossary("t1")["term"] == "Grupo Quatro5"


def test_POST_do_glossario_segue_aberto_e_NAO_sobrescreve(client):
    """Criar termo continua liberado (é colaborativo por decisão de produto). O que torna isso
    seguro é o id nascer de uuid4 no SERVIDOR: sem isso, o POST seria outra via de upsert."""
    db.add_glossary("t1", "Quatro5", "marca", "n", created_by="chefe@test")
    _login(client, "comum@test")

    r = client.post("/api/glossary", json={"term": "Carbone", "category": "marca"})

    assert r.status_code == 201
    assert db.get_glossary("t1")["term"] == "Quatro5", "o POST alcançou um termo existente"


# ─── tela final: asset GLOBAL, sem lixeira ───────────────────────────────────

def test_delete_de_tela_final_exige_admin(client, mocker):
    """Espelha a biblioteca de vinhetas, que já era admin-only. A assimetria entre as duas não
    tinha justificativa: as duas apagam asset global do S3, sem lixeira, e todo projeto que
    referenciava degrada em silêncio pra 'sem tela final'."""
    apagou = mocker.patch("s3_client.delete_object")
    mocker.patch("s3_client.delete_prefix")
    db.add_end_screen("es1", "Tela da casa", "assets/end_screens/es1.mp4", 4.0)
    _login(client, "comum@test")

    r = client.delete("/api/end_screens/es1")

    assert r.status_code == 403
    assert db.get_end_screen("es1") is not None, "asset global apagado por não-admin"
    apagou.assert_not_called()


def test_delete_de_tela_final_admin_passa(client, mocker):
    mocker.patch("s3_client.delete_object")
    mocker.patch("s3_client.delete_prefix")
    db.add_end_screen("es1", "Tela da casa", "assets/end_screens/es1.mp4", 4.0)
    _login(client, "chefe@test", admin=True)

    assert client.delete("/api/end_screens/es1").status_code == 200
    assert db.get_end_screen("es1") is None


def test_leitura_da_biblioteca_segue_compartilhada(client):
    """NÃO REGRESSÃO: fechar a escrita não pode fechar a leitura. A biblioteca é compartilhada
    por decisão de produto, e qualquer usuário precisa continuar ESCOLHENDO uma tela final."""
    db.add_end_screen("es1", "Tela da casa", "assets/end_screens/es1.mp4", 4.0)
    _login(client, "comum@test")

    r = client.get("/api/end_screens")

    assert r.status_code == 200
    assert any(e["id"] == "es1" for e in r.get_json()["end_screens"])
