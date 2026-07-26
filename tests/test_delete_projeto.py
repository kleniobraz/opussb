"""DELETE /api/projects/<id>: a rota serve clipe E podcast (não há if por job_type).

Antes desta suíte a rota não tinha teste nenhum, e ela é destrutiva: apaga S3, disco e banco.
Os testes de clipe aqui são NÃO REGRESSÃO, porque o comportamento em falha de S3 mudou (antes
apagava o registro assim mesmo e deixava órfão invisível no bucket; agora aborta e preserva).
"""
import os
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

import config
import db
import s3_client


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


def _job_clipe(job_id="TEST-del-clipe", user="dono@test"):
    db.create_job(job_id, "video.mp4", 30, 90, f"/tmp/{job_id}.mp4", user_email=user)
    db.update_job(job_id, status="done")
    return job_id


def _job_podcast(job_id="TEST-del-pod", user="dono@test", status="done", **extra):
    db.create_podcast_job(job_id, "pod.mp4", f"/tmp/{job_id}.mp4", user, "vtest")
    db.update_job(job_id, status=status, **extra)
    return job_id


@pytest.fixture
def s3_espiao(mocker):
    """Registra os prefixos apagados sem tocar em rede nenhuma."""
    apagados = []
    mocker.patch("s3_client.delete_prefix",
                 side_effect=lambda p, timeout=None: apagados.append(p) or 1)
    return apagados


# ─── caminho feliz: os dois tipos ─────────────────────────────────────────────

def test_delete_clipe_apaga_s3_disco_e_banco(client, s3_espiao, tmp_path, monkeypatch):
    """NÃO REGRESSÃO: o que funcionava pro clipe tem que continuar funcionando."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    jid = _job_clipe()
    os.makedirs(os.path.join(str(tmp_path), jid), exist_ok=True)
    _login(client, "dono@test")

    r = client.delete(f"/api/projects/{jid}")

    assert r.status_code == 200, r.get_json()
    assert db.get_job(jid) is None                       # banco
    assert f"jobs/{jid}/" in s3_espiao                   # S3 do job
    assert f"{config.SOURCE16X9_PREFIX}{jid}/" in s3_espiao   # 16:9 retido
    assert not os.path.exists(os.path.join(str(tmp_path), jid))   # disco local


def test_delete_podcast_apaga_tudo(client, s3_espiao, tmp_path, monkeypatch):
    """O podcast usa a MESMA rota; final.mp4 e audio.mp3 vivem sob jobs/<id>/."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    jid = _job_podcast()
    os.makedirs(os.path.join(str(tmp_path), jid), exist_ok=True)
    _login(client, "dono@test")

    r = client.delete(f"/api/projects/{jid}")

    assert r.status_code == 200, r.get_json()
    assert db.get_job(jid) is None
    assert f"jobs/{jid}/" in s3_espiao
    assert not os.path.exists(os.path.join(str(tmp_path), jid))


# ─── permissão ────────────────────────────────────────────────────────────────

def test_quem_nao_criou_recebe_403_na_ROTA(client, s3_espiao):
    """Gate na ROTA, não só botão escondido no front: quem não é dono nem admin não passa,
    e NADA é apagado."""
    jid = _job_podcast(user="dono@test")
    _login(client, "outro@test")

    r = client.delete(f"/api/projects/{jid}")

    assert r.status_code == 403
    assert "dono" in r.get_json()["error"].lower()
    assert db.get_job(jid) is not None      # continua existindo
    assert s3_espiao == []                  # e nada foi tocado no S3


def test_admin_pode_excluir_de_outro(client, s3_espiao, tmp_path, monkeypatch):
    """Espelha o clipe: dono OU admin (a rota é a mesma pros dois tipos)."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))   # nunca apontar o rmtree pro real
    jid = _job_podcast(user="dono@test")
    _login(client, "chefe@test", admin=True)
    assert client.delete(f"/api/projects/{jid}").status_code == 200
    assert db.get_job(jid) is None


def test_projeto_inexistente_404(client, s3_espiao):
    _login(client, "dono@test")
    assert client.delete("/api/projects/nao-existe").status_code == 404
    assert s3_espiao == []


# ─── trava por job ────────────────────────────────────────────────────────────

def test_projeto_montando_nao_e_excluido(client, s3_espiao):
    """Trava POR JOB. Um projeto montando não pode ser excluído no meio."""
    jid = _job_podcast(status="montando")
    _login(client, "dono@test")

    r = client.delete(f"/api/projects/{jid}")

    assert r.status_code == 409
    assert "andamento" in r.get_json()["error"].lower()
    assert db.get_job(jid) is not None
    assert s3_espiao == []


def test_extracao_de_audio_tambem_trava(client, s3_espiao):
    """A extração de MP3 roda com o job já 'done': sem olhar podcast_audio_status a trava
    deixaria excluir no meio dela."""
    jid = _job_podcast(status="done", podcast_audio_status="extraindo")
    _login(client, "dono@test")
    assert client.delete(f"/api/projects/{jid}").status_code == 409
    assert db.get_job(jid) is not None


def test_trava_nao_bloqueia_por_trabalho_de_OUTRO_projeto(client, s3_espiao, tmp_path,
                                                          monkeypatch):
    """A checagem é por job, não global: o projeto A sai mesmo com o B montando. Usar
    count_active_jobs/count_trabalho_em_andamento aqui quebraria isto."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    _job_podcast(job_id="TEST-del-outro", status="montando")
    alvo = _job_podcast(job_id="TEST-del-alvo", status="done")
    _login(client, "dono@test")

    assert client.delete(f"/api/projects/{alvo}").status_code == 200
    assert db.get_job(alvo) is None
    assert db.get_job("TEST-del-outro") is not None      # o outro segue intocado


# ─── falha de S3: aborta e preserva, e o retry é idempotente ─────────────────

def test_falha_de_s3_preserva_o_projeto(client, mocker, tmp_path, monkeypatch):
    """MUDANÇA DE COMPORTAMENTO (vale pro clipe também): antes o registro era apagado mesmo
    com o S3 falhando, deixando GB órfãos e invisíveis no bucket pago. Agora aborta."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    jid = _job_podcast()
    d = os.path.join(str(tmp_path), jid)
    os.makedirs(d, exist_ok=True)
    mocker.patch("s3_client.delete_prefix",
                 side_effect=s3_client.S3StalledError("delete jobs/x: sem progresso"))
    _login(client, "dono@test")

    r = client.delete(f"/api/projects/{jid}")

    assert r.status_code == 503
    msg = r.get_json()["error"]
    # diz o que aconteceu e empurra pro retry, sem prometer "nada foi excluído" (o estol pode
    # vir no 2o prefixo, com o 1o já apagado) e sem ser um 500 genérico
    assert "não terminou" in msg and "continua" in msg and "tente de novo" in msg.lower()
    assert db.get_job(jid) is not None                    # projeto preservado
    assert os.path.exists(d)                              # e o disco também


def test_retry_depois_de_falha_parcial_conclui(client, mocker, tmp_path, monkeypatch):
    """Idempotência: a 1a tentativa apaga o prefixo do job e morre no source16x9; a 2a
    encontra o primeiro já vazio (0 objetos) e termina sem erro."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    jid = _job_podcast()
    os.makedirs(os.path.join(str(tmp_path), jid), exist_ok=True)
    _login(client, "dono@test")
    estado = {"vazios": set(), "tentativa": 0}

    def _delete_prefix(prefixo, timeout=None):
        if prefixo in estado["vazios"]:
            return 0                                  # já limpo: no-op idempotente
        if estado["tentativa"] == 0 and "source16x9" in prefixo:
            raise s3_client.S3StalledError("estolou no meio")
        estado["vazios"].add(prefixo)
        return 3

    mocker.patch("s3_client.delete_prefix", side_effect=_delete_prefix)

    assert client.delete(f"/api/projects/{jid}").status_code == 503
    assert db.get_job(jid) is not None                # preservado, retomável

    estado["tentativa"] = 1
    r2 = client.delete(f"/api/projects/{jid}")

    assert r2.status_code == 200, r2.get_json()       # retry conclui
    assert db.get_job(jid) is None


# ─── vinheta compartilhada ────────────────────────────────────────────────────

def test_delete_de_projeto_NAO_toca_a_vinheta(client, s3_espiao, tmp_path, monkeypatch):
    """A key do matched é <vinheta_id>_<assinatura de vídeo>, sem job nenhum: é cache
    compartilhado entre projetos. Apagar aqui quebraria a montagem dos outros."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    jid = _job_podcast()
    _login(client, "dono@test")

    assert client.delete(f"/api/projects/{jid}").status_code == 200

    assert not any(config.VINHETA_PREFIX in p for p in s3_espiao), s3_espiao
    assert not any("_matched" in p for p in s3_espiao), s3_espiao


def test_delete_da_VINHETA_varre_os_matched(client, mocker, monkeypatch):
    """É AQUI que o matched morre, e não no delete de projeto. Sem isto, excluir a vinheta
    da biblioteca deixa um órfão por assinatura de vídeo já montada, pra sempre."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    apagados = []
    mocker.patch("s3_client.delete_prefix",
                 side_effect=lambda p, timeout=None: apagados.append(p) or 2)
    mocker.patch("s3_client.delete_object")
    db.init_vinhetas()
    db.add_vinheta("vdel", "Vinheta a excluir", "assets/vinhetas/vdel.mp4", 5.0)
    _login(client, "chefe@test", admin=True)

    r = client.delete("/api/vinhetas/vdel")

    assert r.status_code == 200, r.get_json()
    assert f"{config.VINHETA_PREFIX}_matched/vdel_" in apagados, apagados
