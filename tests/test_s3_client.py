import time

import pytest

import s3_client


def test_upload_file_chama_boto3(mocker, tmp_path):
    mock_s3 = mocker.MagicMock()
    mocker.patch("s3_client._client", return_value=mock_s3)

    test_file = tmp_path / "audio.mp3"
    test_file.write_bytes(b"fake audio data")

    s3_client.upload_file(str(test_file), "jobs/abc/audio/audio.mp3")

    mock_s3.upload_file.assert_called_once_with(
        str(test_file), "test-bucket", "jobs/abc/audio/audio.mp3"
    )


def test_get_presigned_url_retorna_string(mocker):
    mock_s3 = mocker.MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://presigned.example.com/clip?sig=xyz"
    mocker.patch("s3_client._client", return_value=mock_s3)

    result = s3_client.get_presigned_url("jobs/abc/clips/clip_01_9x16.mp4", expiry_seconds=604800)

    assert result == "https://presigned.example.com/clip?sig=xyz"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": "jobs/abc/clips/clip_01_9x16.mp4"},
        ExpiresIn=604800,
    )


def test_get_presigned_url_default_expiry_24h(mocker):
    mock_s3 = mocker.MagicMock()
    mocker.patch("s3_client._client", return_value=mock_s3)

    s3_client.get_presigned_url("jobs/abc/audio/audio.mp3")

    assert mock_s3.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 86400


def test_get_presigned_url_accelerate_usa_client_separado(mocker):
    """A URL que vai pro RunPod usa o client accelerate, NUNCA o _client() normal (o
    resto do app não pode ser afetado pela mitigação do download intermitente)."""
    mock_normal = mocker.MagicMock()
    mock_accel = mocker.MagicMock()
    mock_accel.generate_presigned_url.return_value = "https://bucket.s3-accelerate.amazonaws.com/audio.mp3?sig=xyz"
    mocker.patch("s3_client._client", return_value=mock_normal)
    mocker.patch("s3_client._accel_client", return_value=mock_accel)

    result = s3_client.get_presigned_url_accelerate("jobs/abc/audio/audio.mp3")

    assert result == "https://bucket.s3-accelerate.amazonaws.com/audio.mp3?sig=xyz"
    mock_accel.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": "jobs/abc/audio/audio.mp3"},
        ExpiresIn=86400,
    )
    mock_normal.generate_presigned_url.assert_not_called()


# ─── download_file: watchdog de stall (achado em produção 2026-07-24) ────────

def test_download_file_feliz_no_stall(mocker, tmp_path):
    """Download que segue progredindo (Callback chamado) termina normal, sem exceção."""
    mock_s3 = mocker.MagicMock()

    def _download(Bucket, Key, Filename, Callback=None):
        for _ in range(3):
            time.sleep(0.05)
            Callback(1024)

    mock_s3.download_file.side_effect = _download
    mocker.patch("s3_client._client", return_value=mock_s3)

    s3_client.download_file("jobs/abc/final.mp4", str(tmp_path / "out.mp4"), stall_timeout=0.3)

    mock_s3.download_file.assert_called_once()


def test_download_file_sem_progresso_desiste(mocker, tmp_path):
    """Sem NENHUM byte novo por stall_timeout, desiste com DownloadStalledError mesmo com
    a chamada real do boto3 ainda 'presa' (nunca retorna, igual o achado em produção)."""
    mock_s3 = mocker.MagicMock()

    def _download(Bucket, Key, Filename, Callback=None):
        Callback(1024)  # um pouco de progresso, depois trava de vez
        time.sleep(5)  # bem mais que o stall_timeout do teste

    mock_s3.download_file.side_effect = _download
    mocker.patch("s3_client._client", return_value=mock_s3)

    t0 = time.monotonic()
    with pytest.raises(s3_client.DownloadStalledError, match="sem progresso"):
        s3_client.download_file("jobs/abc/final.mp4", str(tmp_path / "out.mp4"), stall_timeout=0.3)
    # Desistiu perto do teto pedido, não esperou os 5s da chamada presa.
    assert time.monotonic() - t0 < 2.0


def test_download_file_propaga_erro_real_do_boto3(mocker, tmp_path):
    """Uma falha de verdade do boto3 (não um stall) ainda propaga normal pro chamador."""
    mock_s3 = mocker.MagicMock()
    mock_s3.download_file.side_effect = RuntimeError("acesso negado")
    mocker.patch("s3_client._client", return_value=mock_s3)

    with pytest.raises(RuntimeError, match="acesso negado"):
        s3_client.download_file("jobs/abc/final.mp4", str(tmp_path / "out.mp4"), stall_timeout=1.0)
