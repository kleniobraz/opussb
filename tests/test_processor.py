import json
import os
import pytest
import importlib
import processor


@pytest.fixture(autouse=True)
def reload_processor():
    importlib.reload(processor)


# Test 1: _extract_audio calls ffmpeg with correct arguments
def test_extract_audio_chama_ffmpeg_correto(mocker, tmp_path):
    mock_run = mocker.patch(
        "processor.subprocess.run",
        return_value=mocker.MagicMock(returncode=0, stderr=""),
    )
    video = tmp_path / "original.mp4"
    video.write_bytes(b"fake video content")
    os.environ["JOB_DIR"] = str(tmp_path)
    importlib.reload(processor)

    processor._extract_audio(str(video), "job-extract")

    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert "ffmpeg" in cmd
    assert "-acodec" in cmd
    assert "libmp3lame" in cmd


# Test 2: _extract_audio raises RuntimeError when ffmpeg fails
def test_extract_audio_levanta_se_ffmpeg_falha(mocker, tmp_path):
    mocker.patch(
        "processor.subprocess.run",
        return_value=mocker.MagicMock(returncode=1, stderr="codec error"),
    )
    os.environ["JOB_DIR"] = str(tmp_path)
    importlib.reload(processor)

    with pytest.raises(RuntimeError, match="FFmpeg audio extraction failed"):
        processor._extract_audio("/tmp/fake.mp4", "job-fail")


# Test 3: _s3_upload_with_retry raises after 3 failed attempts
def test_s3_upload_retry_levanta_apos_3_tentativas(mocker):
    mocker.patch(
        "processor.s3_client.upload_file",
        side_effect=Exception("network error"),
    )
    mocker.patch("processor.time.sleep")

    with pytest.raises(RuntimeError, match="S3 upload failed after 3 attempts"):
        processor._s3_upload_with_retry("/tmp/audio.mp3", "jobs/abc/audio/audio.mp3")


# Test 4: _s3_upload_with_retry succeeds on second attempt
def test_s3_upload_retry_sucede_na_segunda_tentativa(mocker):
    call_count = {"n": 0}

    def upload_side_effect(local_path, s3_key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("transient error")

    mocker.patch("processor.s3_client.upload_file", side_effect=upload_side_effect)
    mocker.patch("processor.time.sleep")

    processor._s3_upload_with_retry("/tmp/audio.mp3", "jobs/abc/audio/audio.mp3")

    assert call_count["n"] == 2


# Test 5: _trigger_n8n_with_retry raises after 3 failures
def test_trigger_n8n_retry_levanta_apos_3_falhas(mocker):
    mocker.patch(
        "processor.requests.post",
        side_effect=Exception("connection refused"),
    )
    mocker.patch("processor.time.sleep")

    with pytest.raises(RuntimeError, match="N8N trigger failed after 3 attempts"):
        processor._trigger_n8n_with_retry(
            "job-123", "http://s3.example.com/audio.mp3", 30, 90
        )


# Test 6: _cut_clips calls ffmpeg once per valid clip
def test_cut_clips_chama_ffmpeg_por_clip(mocker, tmp_path):
    def fake_run(cmd, **kwargs):
        out_path = cmd[-1]
        open(out_path, "w").close()
        return mocker.MagicMock(returncode=0)

    mock_run = mocker.patch("processor.subprocess.run", side_effect=fake_run)

    clips = [
        {"start": 0, "end": 30},
        {"start": 60, "end": 120},
    ]
    output_dir = str(tmp_path / "clips")
    os.makedirs(output_dir, exist_ok=True)

    result = processor._cut_clips("/fake/video.mp4", clips, output_dir)

    assert len(result) == 2
    assert mock_run.call_count == 2


# Test 7: _cut_clips skips clips shorter than 5 seconds
def test_cut_clips_pula_clips_muito_curtos(mocker, tmp_path):
    def fake_run(cmd, **kwargs):
        out_path = cmd[-1]
        open(out_path, "w").close()
        return mocker.MagicMock(returncode=0)

    mock_run = mocker.patch("processor.subprocess.run", side_effect=fake_run)

    clips = [
        {"start": 0, "end": 3},   # 3s — too short, should be skipped
        {"start": 10, "end": 70},  # 60s — valid
    ]
    output_dir = str(tmp_path / "clips")
    os.makedirs(output_dir, exist_ok=True)

    result = processor._cut_clips("/fake/video.mp4", clips, output_dir)

    assert len(result) == 1
    assert mock_run.call_count == 1
