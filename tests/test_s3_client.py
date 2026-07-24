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
