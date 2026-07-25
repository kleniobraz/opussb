"""Como o app se comporta quando uma chamada ao S3 ESTOLA (S3StalledError do watchdog do
s3_client, 2026-07-25) num ponto que antes só sabia responder "existe" ou "não existe".

O risco que estes testes travam: o exists() passou a PROPAGAR um HEAD travado (antes pendurava a
thread pra sempre), e sem tratamento isso viraria 500 justamente nos caminhos que o projeto
promete degradar (o clipe sempre toca, o card cai no fundo, a faixa fica neutra). Também travam a
regra oposta: onde refazer o trabalho custa GBs, um estol NÃO pode virar "não existe"."""
import app
import s3_client


def _estola(*_a, **_k):
    raise s3_client.S3StalledError("head jobs/x: sem progresso por mais de 60s")


# ─── _cache_quente: os três estados ────────────────────────────────────────────

def test_cache_quente_tres_estados(mocker):
    mocker.patch("s3_client.exists", return_value=True)
    assert app._cache_quente("k") is True
    mocker.patch("s3_client.exists", return_value=False)
    assert app._cache_quente("k") is False
    mocker.patch("s3_client.exists", side_effect=_estola)
    assert app._cache_quente("k") is None      # estolou: não deu pra saber


def test_cache_quente_nao_engole_erro_que_nao_e_estol(mocker):
    """Só S3StalledError degrada. Qualquer outra exceção continua subindo (um bug de verdade
    não pode virar 'cache frio' silencioso)."""
    mocker.patch("s3_client.exists", side_effect=ValueError("bug de verdade"))
    try:
        app._cache_quente("k")
        assert False, "deveria ter propagado"
    except ValueError:
        pass


# ─── _ensure_burn: degrada pra base SEM gastar o teto do watchdog na request ───

def test_ensure_burn_com_cache_estolado_serve_base_sem_baixar(mocker):
    """O ponto central do Médio 1: com o HEAD estolado, _ensure_burn devolve None (fallback =
    servir a base sem legenda) IMEDIATAMENTE. Não pode tentar queimar: o download da base
    ficaria preso até S3_STALL_TIMEOUT (180s) prendendo uma das 4 threads do gunicorn."""
    mocker.patch("s3_client.exists", side_effect=_estola)
    baixar = mocker.patch("s3_client.download_file")
    subir = mocker.patch("s3_client.upload_file")

    r = app._ensure_burn("J:0", "jobs/J/clips/clip_01_base.mp4",
                         "jobs/J/clips/clip_01_cap.mp4", True, False, "[]",
                         None, None, None, False, None, None)

    assert r is None
    baixar.assert_not_called()
    subir.assert_not_called()


def test_ensure_burn_com_cache_frio_de_verdade_segue_queimando(mocker):
    """Contraprova: cache frio (False, não estol) tem que seguir o caminho normal de queima.
    O estol não pode ter virado um 'nunca queima nada'."""
    mocker.patch("s3_client.exists", return_value=False)
    baixar = mocker.patch("s3_client.download_file")
    mocker.patch("app._burn_clip")
    mocker.patch("s3_client.upload_file")
    mocker.patch("captions.build_ass_from_segments", return_value="[Script Info]\n")

    r = app._ensure_burn("J:0", "jobs/J/clips/clip_01_base.mp4",
                         "jobs/J/clips/clip_01_cap.mp4", True, False, "[]",
                         None, None, None, False, None, None)

    assert r == "jobs/J/clips/clip_01_cap.mp4"
    baixar.assert_called_once()


# ─── _serve_clip: o clipe sempre toca ──────────────────────────────────────────

def test_serve_clip_com_cache_estolado_serve_content_sem_anexar(mocker):
    """Com tela final pedida e o HEAD do cache estolado, serve o content direto (clipe toca) em
    vez de tentar produzir o anexo (que baixaria GBs) ou dar 500."""
    mocker.patch("s3_client.exists", side_effect=_estola)
    produzir = mocker.patch("app._produce_endscreen")
    redirect = mocker.patch("app._s3_redirect", return_value="302")

    app._serve_clip("jobs/J/clips/clip_01_cap.mp4", "es1", None)

    produzir.assert_not_called()
    assert redirect.call_args.args[0] == "jobs/J/clips/clip_01_cap.mp4"
