"""Rotas básicas do fluxo de clipes: /health, /api/status e /api/upload.

ESTE ARQUIVO FICOU 11 MESES VERMELHO, e a razão importa mais que o conserto: ele é do
primeiro commit e nunca acompanhou o app. Quando o login virou obrigatório, os 10 testes de
status/upload passaram a receber 401 e a suíte passou a reprovar SEMPRE. Suíte que reprova
sempre é suíte que ninguém lê: o próprio conftest já contava as falhas como paisagem ("a
suíte ia de 11 pra 12 falhas"), ou seja, o número era conhecido e ninguém conseguia usar a
suíte pra dizer se uma mudança tinha quebrado algo.

Duas causas, as duas de teste desatualizado, nenhum bug de produto:
  - o fixture nunca autenticava, e /api/status e /api/upload têm @login_required;
  - o /health cresceu (devolve status + db + active_jobs) e o teste comparava o dict INTEIRO.

O fixture agora autentica, e o preço disso está pago logo abaixo: como logar em todo teste
apagaria o sinal de que essas rotas SÃO protegidas, o test_rotas_exigem_sessao fixa isso de
propósito. Sem ele, alguém removeria o @login_required e esta suíte ficaria verde.
"""
import io
import json
import uuid

import pytest
from werkzeug.security import generate_password_hash

import db


def _login(client, email="dono@test"):
    """Mesma convenção de tests/test_upload_direto.py. must_change=False porque o
    /api/upload gateia quem ainda não trocou a senha do primeiro acesso (403), e não é isso
    que estes testes exercitam."""
    db.upsert_user(email, generate_password_hash("senha-de-teste"), must_change=False)
    r = client.post("/api/login", json={"email": email, "password": "senha-de-teste"})
    assert r.status_code == 200, r.get_json()


@pytest.fixture
def anon(tmp_path):
    """Cliente SEM sessão, pro teste que fixa a exigência de autenticação."""
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def client(anon):
    """Cliente autenticado: é o estado em que todo o resto deste arquivo faz sentido."""
    _login(anon)
    return anon


# 0. As rotas deste arquivo exigem sessão. Fixa o que o fixture autenticado esconde.
@pytest.mark.parametrize("metodo,rota", [
    ("get", "/api/status/qualquer"),
    ("post", "/api/upload"),
])
def test_rotas_exigem_sessao(anon, metodo, rota):
    resp = getattr(anon, metodo)(rota)
    assert resp.status_code == 401, (
        f"{rota} respondeu {resp.status_code} sem sessão: o @login_required saiu?")


# 1. Health endpoint returns 200 with status ok
def test_health_retorna_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    # Subconjunto, não igualdade: o /health ganhou `db` e `active_jobs` (as duas metades do
    # "dá pra reiniciar?") e comparar o dict inteiro faz o teste quebrar a cada campo novo,
    # que é como ele ficou vermelho. O contrato que importa aqui é o status.
    assert data["status"] == "ok"


# 2. Status for non-existent job returns 404
def test_status_job_inexistente_retorna_404(client):
    resp = client.get("/api/status/nao-existe")
    assert resp.status_code == 404


# 3. Status for existing job returns correct status and progress
def test_status_job_existente_retorna_progress(client):
    job_id = str(uuid.uuid4())
    db.create_job(job_id, "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job(job_id, status="transcribing", progress=40)

    resp = client.get(f"/api/status/{job_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "transcribing"
    assert data["progress"] == 40


# 4. Status for done job returns the Clip objects
def test_status_done_retorna_clips(client):
    job_id = "job-done"
    db.create_job(job_id, "video.mp4", 30, 90, "/tmp/video.mp4")
    result = [{"key": "jobs/job-done/clips/clip_01_base.mp4", "title": "Melhor momento",
               "score": 9.1, "reason": "gancho forte", "duration": 42}]
    db.update_job(job_id, status="done", progress=100, result_urls=json.dumps(result))

    resp = client.get(f"/api/status/{job_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "done"
    # `clips` deixou de ser uma lista de presignadas quando a queima virou SOB DEMANDA: hoje
    # é objeto por clipe e a `url` aponta pra rota de vídeo, que queima, cacheia e devolve
    # 302. O teste antigo mockava get_presigned_url e comparava com a URL crua, ou seja,
    # exigia de volta o comportamento que a feature central do produto substituiu.
    assert data["clips"] == [{
        "id": "job-done:0",
        "url": "/api/clips/job-done:0/video",
        "title": "Melhor momento",
        "reason": "gancho forte",
        "score": 9.1,
        "duration": 42,
        "thumbnail_url": "/api/clips/job-done:0/thumb",
    }]


# 5. Status for error job returns error message
def test_status_error_retorna_mensagem(client):
    job_id = str(uuid.uuid4())
    db.create_job(job_id, "video.mp4", 30, 90, "/tmp/video.mp4")
    db.update_job(job_id, status="error", error="FFmpeg falhou")

    resp = client.get(f"/api/status/{job_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "error"
    assert data["error"] == "FFmpeg falhou"


# 6. Upload with no file field returns 400
def test_upload_sem_arquivo_retorna_400(client):
    resp = client.post("/api/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


# 7. Upload with invalid file format (.txt) returns 400
def test_upload_formato_invalido_retorna_400(client):
    resp = client.post(
        "/api/upload",
        data={
            "video": (io.BytesIO(b"not a video"), "document.txt"),
            "min_duration": "30",
            "max_duration": "90",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


# 8. Upload with valid mp4 but no min/max_duration returns 400
def test_upload_sem_duracao_retorna_400(client):
    resp = client.post(
        "/api/upload",
        data={
            "video": (io.BytesIO(b"fake mp4 data"), "video.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


# 9. Upload with min_duration >= max_duration returns 400
def test_upload_min_maior_que_max_retorna_400(client):
    resp = client.post(
        "/api/upload",
        data={
            "video": (io.BytesIO(b"fake mp4 data"), "video.mp4"),
            "min_duration": "90",
            "max_duration": "30",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


# 10. Upload when there's already an active job returns 429 with "água" in error
def test_upload_segundo_job_retorna_429(client):
    # Ocupa a única vaga (MAX_CONCURRENT_JOBS=1, pinado no conftest).
    existing_job_id = str(uuid.uuid4())
    db.create_job(existing_job_id, "existing.mp4", 30, 90, "/tmp/existing.mp4")
    db.update_job(existing_job_id, status="transcribing", progress=30)

    resp = client.post(
        "/api/upload",
        data={
            "video": (io.BytesIO(b"fake mp4 data"), "video.mp4"),
            "min_duration": "30",
            "max_duration": "90",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 429
    data = resp.get_json()
    assert "água" in data["error"]


# 11. Valid upload creates job and returns job_id
def test_upload_valido_cria_job_e_retorna_job_id(client, mocker):
    mocker.patch("processor.run_thread1")   # não dispara o pipeline de verdade

    resp = client.post(
        "/api/upload",
        data={
            "video": (io.BytesIO(b"fake mp4 data"), "video.mp4"),
            "min_duration": "30",
            "max_duration": "90",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert "job_id" in data

    # Verify it is a valid UUID4
    job_id = data["job_id"]
    parsed = uuid.UUID(job_id, version=4)
    assert str(parsed) == job_id

    # Verify job exists in db with status "queued"
    job = db.get_job(job_id)
    assert job is not None
    assert job["status"] == "queued"
    assert job["user_email"] == "dono@test"   # o dono é quem subiu, não NULL
