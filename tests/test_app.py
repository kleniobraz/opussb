import io
import json
import uuid
import pytest
import importlib
import db


@pytest.fixture
def client(tmp_path):
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# 1. Health endpoint returns 200 with {"status": "ok"}
def test_health_retorna_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


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


# 4. Status for done job returns clips as presigned URLs
def test_status_done_retorna_clips_como_presigned_urls(client, mocker):
    job_id = "job-done"
    db.create_job(job_id, "video.mp4", 30, 90, "/tmp/video.mp4")
    result_keys = ["jobs/job-done/clips/clip_01_9x16.mp4"]
    db.update_job(job_id, status="done", progress=100, result_urls=json.dumps(result_keys))

    presigned_url = "https://presigned.example.com/clip.mp4"
    mocker.patch("s3_client.get_presigned_url", return_value=presigned_url)

    resp = client.get(f"/api/status/{job_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "done"
    assert "clips" in data
    assert data["clips"] == [presigned_url]


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
def test_upload_formato_invalido_retorna_400(client, tmp_path):
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
def test_upload_sem_duracao_retorna_400(client, tmp_path):
    resp = client.post(
        "/api/upload",
        data={
            "video": (io.BytesIO(b"fake mp4 data"), "video.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


# 9. Upload with min_duration >= max_duration returns 400
def test_upload_min_maior_que_max_retorna_400(client, tmp_path):
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
def test_upload_segundo_job_retorna_429(client, mocker, tmp_path):
    # Create an active job to hit the concurrency limit
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
def test_upload_valido_cria_job_e_retorna_job_id(client, mocker, tmp_path):
    mock_thread1 = mocker.patch("processor.run_thread1")

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
