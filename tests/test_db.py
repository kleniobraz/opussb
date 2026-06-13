import time
import db


def test_create_e_get_job():
    db.create_job("job-001", "video.mp4", 30, 60, "/tmp/video.mp4")
    job = db.get_job("job-001")
    assert job is not None
    assert job["job_id"] == "job-001"
    assert job["filename"] == "video.mp4"
    assert job["status"] == "queued"
    assert job["progress"] == 5
    assert job["min_duration"] == 30
    assert job["max_duration"] == 60


def test_get_job_inexistente_retorna_none():
    result = db.get_job("job-nao-existe")
    assert result is None


def test_update_job_status_e_progress():
    db.create_job("job-002", "clip.mp4", 15, 45, "/tmp/clip.mp4")
    db.update_job("job-002", status="processing", progress=50)
    job = db.get_job("job-002")
    assert job["status"] == "processing"
    assert job["progress"] == 50


def test_update_job_campos_multiplos():
    db.create_job("job-003", "audio.mp4", 10, 30, "/tmp/audio.mp4")
    db.update_job("job-003", runpod_job_id="rp-abc123", status="transcribing", progress=70)
    job = db.get_job("job-003")
    assert job["runpod_job_id"] == "rp-abc123"
    assert job["status"] == "transcribing"
    assert job["progress"] == 70


def test_count_active_jobs_exclui_done_e_error():
    db.create_job("job-q", "q.mp4", 10, 30, "/tmp/q.mp4")
    db.create_job("job-d", "d.mp4", 10, 30, "/tmp/d.mp4")
    db.create_job("job-e", "e.mp4", 10, 30, "/tmp/e.mp4")
    db.create_job("job-t", "t.mp4", 10, 30, "/tmp/t.mp4")

    db.update_job("job-d", status="done")
    db.update_job("job-e", status="error")
    db.update_job("job-t", status="transcribing")

    count = db.count_active_jobs()
    assert count == 2


def test_updated_at_muda_ao_atualizar():
    db.create_job("job-005", "test.mp4", 20, 40, "/tmp/test.mp4")
    job_before = db.get_job("job-005")
    updated_at_before = job_before["updated_at"]

    time.sleep(0.01)
    db.update_job("job-005", status="processing")

    job_after = db.get_job("job-005")
    assert job_after["updated_at"] != updated_at_before
