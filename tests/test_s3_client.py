import time

import pytest

import s3_client


def test_upload_file_chama_boto3(mocker, tmp_path):
    mock_s3 = mocker.MagicMock()
    mocker.patch("s3_client._client", return_value=mock_s3)

    test_file = tmp_path / "audio.mp3"
    test_file.write_bytes(b"fake audio data")

    s3_client.upload_file(str(test_file), "jobs/abc/audio/audio.mp3")

    args, kwargs = mock_s3.upload_file.call_args
    assert args == (str(test_file), "test-bucket", "jobs/abc/audio/audio.mp3")
    # Callback obrigatório: é ele que alimenta o watchdog de progresso (_com_teto). Sem
    # Callback, um upload lento e legítimo de GBs seria morto pelo teto.
    assert callable(kwargs["Callback"])


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


def test_download_stalled_e_subclasse_de_s3_stalled():
    """O nome histórico (DownloadStalledError, mensagens de job já gravadas em 2026-07-24) tem
    que continuar existindo E ser pegável por um `except S3StalledError` genérico."""
    assert issubclass(s3_client.DownloadStalledError, s3_client.S3StalledError)
    assert issubclass(s3_client.S3StalledError, RuntimeError)


# ─── _com_teto: watchdog único de TODA chamada síncrona (generalização 2026-07-25) ──

def _transferencia_com_progresso(duracao, passo=0.02):
    """Simula uma transferência que dura `duracao` batendo ponto a cada `passo` (o Callback
    do boto3), pra provar teto de PROGRESSO e não de duração total."""
    def _fake(*_a, Callback=None, **_kw):
        fim = time.monotonic() + duracao
        while time.monotonic() < fim:
            time.sleep(passo)
            Callback(1024)
    return _fake


def test_transferencia_longa_com_progresso_nao_e_morta(mocker, tmp_path):
    """O teto é de PROGRESSO: uma transferência que dura MUITO mais que o stall_timeout, mas
    segue recebendo bytes, termina normal (um final.mp4 de dezenas de GB leva minutos legítimos
    e não pode ser morto por isso)."""
    mock_s3 = mocker.MagicMock()
    mock_s3.download_file.side_effect = _transferencia_com_progresso(0.6)
    mocker.patch("s3_client._client", return_value=mock_s3)

    s3_client.download_file("jobs/abc/final.mp4", str(tmp_path / "out.mp4"), stall_timeout=0.15)


def test_upload_file_sem_progresso_desiste(mocker, tmp_path):
    """Ponto 2 dos que faltavam (memória de 2026-07-24): upload do MP3 de volta pro S3 travado
    não pendura mais a thread pra sempre."""
    mock_s3 = mocker.MagicMock()

    def _upload_travado(*_a, Callback=None, **_kw):
        Callback(1024)   # um pouco de progresso e trava de vez
        time.sleep(5)

    mock_s3.upload_file.side_effect = _upload_travado
    mocker.patch("s3_client._client", return_value=mock_s3)
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"x" * 32)

    t0 = time.monotonic()
    with pytest.raises(s3_client.S3StalledError, match="upload"):
        s3_client.upload_file(str(f), "jobs/abc/podcast/audio.mp3", stall_timeout=0.3)
    assert time.monotonic() - t0 < 2.0


def test_upload_file_longo_com_progresso_nao_e_morto(mocker, tmp_path):
    mock_s3 = mocker.MagicMock()
    mock_s3.upload_file.side_effect = _transferencia_com_progresso(0.6)
    mocker.patch("s3_client._client", return_value=mock_s3)
    f = tmp_path / "audio.mp3"
    f.write_bytes(b"x" * 32)

    s3_client.upload_file(str(f), "jobs/abc/podcast/audio.mp3", stall_timeout=0.15)


def test_callback_com_delta_negativo_nao_conta_como_progresso(mocker, tmp_path):
    """O s3transfer chama o Callback com delta NEGATIVO quando perde o progresso de um GET e vai
    reiniciar a parte. Se isso renovasse o relógio, um S3 flapando esticaria o teto efetivo pra
    (tentativas x stall_timeout), segurando o slot da queima por minutos."""
    mock_s3 = mocker.MagicMock()

    def _download_flapando(*_a, Callback=None, **_kw):
        Callback(1024)          # progresso de verdade
        fim = time.monotonic() + 5
        while time.monotonic() < fim:
            time.sleep(0.05)
            Callback(-1024)     # "all progress for this GetObject has been lost"

    mock_s3.download_file.side_effect = _download_flapando
    mocker.patch("s3_client._client", return_value=mock_s3)

    t0 = time.monotonic()
    with pytest.raises(s3_client.DownloadStalledError):
        s3_client.download_file("jobs/abc/final.mp4", str(tmp_path / "out.mp4"), stall_timeout=0.3)
    assert time.monotonic() - t0 < 2.0


def test_upload_file_arquivo_inexistente_falha_antes_do_watchdog(mocker, tmp_path):
    """FileNotFoundError continua imediato (o check é local, não gasta thread nem teto)."""
    mock_s3 = mocker.MagicMock()
    mocker.patch("s3_client._client", return_value=mock_s3)

    with pytest.raises(FileNotFoundError):
        s3_client.upload_file(str(tmp_path / "nao_existe.mp3"), "jobs/abc/x.mp3")
    mock_s3.upload_file.assert_not_called()


def test_head_size_travado_desiste(mocker, monkeypatch):
    """Ponto 1 dos que faltavam: o HEAD do guard-rail de disco da extração de MP3. Chamada
    curta = o teto é a DURAÇÃO TOTAL (não há byte pra medir progresso)."""
    import config
    monkeypatch.setattr(config, "S3_API_TIMEOUT", 0.3)
    mock_s3 = mocker.MagicMock()
    mock_s3.head_object.side_effect = lambda **_kw: time.sleep(5)
    mocker.patch("s3_client._client", return_value=mock_s3)

    t0 = time.monotonic()
    with pytest.raises(s3_client.S3StalledError, match="head"):
        s3_client.head_size("jobs/abc/podcast/final.mp4")
    assert time.monotonic() - t0 < 2.0


def test_head_size_devolve_tamanho(mocker):
    """O caminho feliz atravessa o wrapper e devolve o valor de verdade (o _com_teto não pode
    engolir o retorno da chamada)."""
    mock_s3 = mocker.MagicMock()
    mock_s3.head_object.return_value = {"ContentLength": 7_600_000_000}
    mocker.patch("s3_client._client", return_value=mock_s3)

    assert s3_client.head_size("jobs/abc/podcast/final.mp4") == 7_600_000_000
    mock_s3.head_object.assert_called_once_with(
        Bucket="test-bucket", Key="jobs/abc/podcast/final.mp4")


def test_exists_ausente_e_false_mas_travado_propaga(mocker, monkeypatch):
    """Objeto ausente (ClientError) segue False; HEAD TRAVADO propaga S3StalledError em vez de
    virar um False silencioso, que faria o chamador queimar/renderizar de novo à toa."""
    import config
    from botocore.exceptions import ClientError
    monkeypatch.setattr(config, "S3_API_TIMEOUT", 0.3)
    mock_s3 = mocker.MagicMock()
    mocker.patch("s3_client._client", return_value=mock_s3)

    mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    assert s3_client.exists("jobs/abc/clips/clip_01_cap.mp4") is False

    mock_s3.head_object.side_effect = lambda **_kw: time.sleep(5)
    with pytest.raises(s3_client.S3StalledError):
        s3_client.exists("jobs/abc/clips/clip_01_cap.mp4")


def test_delete_object_travado_desiste(mocker, monkeypatch):
    import config
    monkeypatch.setattr(config, "S3_API_TIMEOUT", 0.3)
    mock_s3 = mocker.MagicMock()
    mock_s3.delete_object.side_effect = lambda **_kw: time.sleep(5)
    mocker.patch("s3_client._client", return_value=mock_s3)

    with pytest.raises(s3_client.S3StalledError, match="delete"):
        s3_client.delete_object("jobs/abc/clips/clip_01_cap.mp4")


def test_delete_prefix_pagina_e_apaga_em_lote(mocker):
    """Paginação na mão (ContinuationToken) em vez do paginator lazy do boto3: as duas páginas
    são listadas, a 2a chamada leva o token, e tudo é apagado."""
    mock_s3 = mocker.MagicMock()
    paginas = [
        {"Contents": [{"Key": "jobs/abc/a.mp4"}, {"Key": "jobs/abc/b.mp4"}],
         "NextContinuationToken": "tok1"},
        {"Contents": [{"Key": "jobs/abc/c.mp4"}]},
    ]
    chamadas = []

    def _list(**kwargs):
        chamadas.append(kwargs)
        return paginas[len(chamadas) - 1]

    mock_s3.list_objects_v2.side_effect = _list
    mocker.patch("s3_client._client", return_value=mock_s3)

    assert s3_client.delete_prefix("jobs/abc/") == 3
    assert "ContinuationToken" not in chamadas[0]
    assert chamadas[1]["ContinuationToken"] == "tok1"
    assert mock_s3.delete_objects.call_count == 1     # um lote só (< 1000 keys)
    apagadas = [o["Key"] for o in
                mock_s3.delete_objects.call_args.kwargs["Delete"]["Objects"]]
    assert apagadas == ["jobs/abc/a.mp4", "jobs/abc/b.mp4", "jobs/abc/c.mp4"]


def test_delete_prefix_travado_na_listagem_desiste(mocker, monkeypatch):
    import config
    monkeypatch.setattr(config, "S3_API_TIMEOUT", 0.3)
    mock_s3 = mocker.MagicMock()
    mock_s3.list_objects_v2.side_effect = lambda **_kw: time.sleep(5)
    mocker.patch("s3_client._client", return_value=mock_s3)

    t0 = time.monotonic()
    with pytest.raises(s3_client.S3StalledError, match="list"):
        s3_client.delete_prefix("jobs/abc/")
    assert time.monotonic() - t0 < 2.0


def test_ensure_lifecycle_rule_travado_no_get_desiste(mocker, monkeypatch):
    """O achado que travou o BOOT por 6min20s em 2026-07-24. Agora o GET tem teto (e o app.py
    ainda chama isso em thread de fundo, então nem esse teto bloqueia o boot)."""
    import config
    monkeypatch.setattr(config, "S3_API_TIMEOUT", 0.3)
    mock_s3 = mocker.MagicMock()
    mock_s3.get_bucket_lifecycle_configuration.side_effect = lambda **_kw: time.sleep(5)
    mocker.patch("s3_client._client", return_value=mock_s3)

    t0 = time.monotonic()
    with pytest.raises(s3_client.S3StalledError, match="get_lifecycle"):
        s3_client.ensure_lifecycle_rule("source16x9/", 30, "rid")
    assert time.monotonic() - t0 < 2.0
    mock_s3.put_bucket_lifecycle_configuration.assert_not_called()


def test_ensure_lifecycle_rule_travado_no_put_desiste(mocker, monkeypatch):
    import config
    monkeypatch.setattr(config, "S3_API_TIMEOUT", 0.3)
    mock_s3 = mocker.MagicMock()
    mock_s3.get_bucket_lifecycle_configuration.return_value = {"Rules": []}
    mock_s3.put_bucket_lifecycle_configuration.side_effect = lambda **_kw: time.sleep(5)
    mocker.patch("s3_client._client", return_value=mock_s3)

    with pytest.raises(s3_client.S3StalledError, match="put_lifecycle"):
        s3_client.ensure_lifecycle_rule("source16x9/", 30, "rid")


def test_api_propaga_erro_real_e_devolve_valor(mocker, monkeypatch):
    """O wrapper das chamadas curtas é transparente: repassa exceção de verdade intacta e
    devolve o retorno da chamada."""
    import config
    monkeypatch.setattr(config, "S3_API_TIMEOUT", 1.0)

    assert s3_client._api("op", lambda: {"ok": 1}) == {"ok": 1}
    with pytest.raises(ValueError, match="boom"):
        s3_client._api("op", lambda: (_ for _ in ()).throw(ValueError("boom")))


# ─── _disposition: sanitização do Content-Disposition ─────────────────────────
# Trava o conserto do achado de segurança de 2026-07-25: o download_name ia cru pra dentro de
# f'attachment; filename="{...}"'. O botocore percent-encoda o valor na URL assinada (então
# nunca houve injeção de parâmetro nem CRLF na presignada), MAS o S3 decodifica e devolve o
# valor VERBATIM no header, então uma aspa dupla fechava o filename e deixava acrescentar
# outro parâmetro. Sem estes testes, uma refatoração que inverta a ordem NFKD/allowlist
# reabre o buraco em silêncio.

def test_disposition_mata_aspa_e_parametro_extra():
    """Aspa dupla fecharia o filename="..."; filename*= tem precedência no navegador e faria
    o arquivo salvar com outro nome e outra extensão."""
    r = s3_client._disposition('a";filename*=UTF-8\'\'nota_fiscal.html;x.mp4')
    assert '";' not in r and "filename*" not in r
    assert r.startswith('attachment; filename="') and r.endswith('"')
    assert r.count('"') == 2          # só as duas aspas delimitadoras


def test_disposition_mata_crlf_e_controle():
    """CR/LF quebrariam o header em dois; NUL e afins são inválidos em header."""
    r = s3_client._disposition("a\r\nSet-Cookie: x=1\x00\x07b.mp4")
    assert "\r" not in r and "\n" not in r and "\x00" not in r and ":" not in r


def _nome(disposition):
    """Extrai só o filename de dentro das aspas, pra assertar sobre o valor e não sobre o
    header inteiro (o prefixo tem aspa por construção)."""
    assert disposition.startswith('attachment; filename="') and disposition.endswith('"')
    return disposition[len('attachment; filename="'):-1]


def test_disposition_normaliza_antes_de_filtrar():
    """Ordem importa: NFKD converte homoglyph de compatibilidade (U+FF02 vira aspa ASCII) e o
    allowlist roda DEPOIS, matando o resultado. Se alguém inverter a ordem, o homoglyph vira
    aspa de verdade DENTRO do filename e o bypass volta."""
    assert _nome(s3_client._disposition("a＂b.mp4")) == "ab.mp4"
    assert _nome(s3_client._disposition("a＼b＊c；d.mp4")) == "abcd.mp4"


def test_disposition_preserva_portugues_legivel():
    """Acento vira ASCII em vez de sumir: o nome tem que continuar reconhecível pro time."""
    assert s3_client._disposition("Ep 3 Inovação e Gestão.mp4") == \
        'attachment; filename="Ep 3 Inovacao e Gestao.mp4"'


def test_disposition_fallback_quando_sanitiza_pra_vazio():
    """Nome 100% não-ASCII, só pontuação ou vazio não pode virar filename="" (header inválido)."""
    for entrada in ("日本語", "!!!", "....", "", None):
        assert s3_client._disposition(entrada) == 'attachment; filename="download"'


def test_disposition_limita_tamanho():
    nome = s3_client._disposition("A" * 500)
    assert len(nome[len('attachment; filename="'):-1]) == 120


# ─── get_presigned_download_url: o roteamento da flag de custo ────────────────

def test_download_url_flag_off_usa_client_normal(mocker, monkeypatch):
    import config
    monkeypatch.setattr(config, "S3_ACCELERATE_DOWNLOADS", False)
    normal = mocker.patch("s3_client.get_presigned_url", return_value="normal")
    accel = mocker.patch("s3_client.get_presigned_url_accelerate", return_value="accel")

    assert s3_client.get_presigned_download_url("jobs/a/final.mp4", "ep.mp4") == "normal"
    accel.assert_not_called()
    assert normal.call_args.kwargs["download_name"] == "ep.mp4"


def test_download_url_flag_on_usa_accelerate(mocker, monkeypatch):
    """Com a flag ligada o download do usuário sai pelo endpoint accelerate, que custa
    +US$0,04/GB: se este teste inverter sozinho, é dinheiro indo embora em silêncio."""
    import config
    monkeypatch.setattr(config, "S3_ACCELERATE_DOWNLOADS", True)
    normal = mocker.patch("s3_client.get_presigned_url", return_value="normal")
    accel = mocker.patch("s3_client.get_presigned_url_accelerate", return_value="accel")

    assert s3_client.get_presigned_download_url("jobs/a/final.mp4", "ep.mp4") == "accel"
    normal.assert_not_called()
    assert accel.call_args.kwargs["download_name"] == "ep.mp4"
