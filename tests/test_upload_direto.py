"""Upload DIRETO do navegador pro S3 (multipart presignado) e a materialização do fonte.

Por que esta suíte é densa: o caminho antigo tinha 21% de sucesso (8 de 37 em 14 dias) e o
novo troca o Flask por credenciais presignadas na mão do cliente. As duas famílias de teste
aqui existem por razões diferentes:

  - As de SEGURANÇA travam que a key nasce no SERVIDOR e que o token é validado por DONO.
    Uma presignada é credencial ao portador: se qualquer um desses dois cair, um usuário
    logado escreve por cima do arquivo de outro.
  - As de IDEMPOTÊNCIA travam a única janela que sobra depois desta mudança (restart entre o
    último byte e o complete). Se elas quebrarem, a feature volta a perder upload de 10GB,
    que é exatamente o incidente que ela existe pra eliminar.
"""
import os
import sqlite3

import pytest
from botocore.exceptions import ClientError
from werkzeug.security import generate_password_hash

import config
import db
import podcast_ingest
import s3_client


@pytest.fixture(autouse=True)
def feature_ligada(monkeypatch):
    """A feature é default OFF em produção (kill switch). A suíte liga, menos onde o próprio
    default é o objeto do teste."""
    monkeypatch.setattr(config, "PODCAST_UPLOAD_DIRETO", True)
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)


@pytest.fixture
def client():
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _login(client, email="dono@test", admin=False):
    db.upsert_user(email, generate_password_hash("senha-de-teste"), must_change=False)
    if admin:
        con = sqlite3.connect(os.environ["DB_PATH"])
        con.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
        con.commit()
        con.close()
    r = client.post("/api/login", json={"email": email, "password": "senha-de-teste"})
    assert r.status_code == 200, r.get_json()


def _vinheta():
    db.add_vinheta("vtest", "Vinheta de teste", "assets/vinhetas/vtest.mp4", 5.0)
    return "vtest"


@pytest.fixture
def s3_falso(mocker):
    """Nenhum teste aqui toca rede. Devolve o espião pra assertar o que foi chamado."""
    m = mocker.patch("s3_client.criar_multipart", return_value="upload-id-123")
    mocker.patch("s3_client.presign_parte",
                 side_effect=lambda k, u, n, e: f"https://s3.example/{k}?parte={n}")
    mocker.patch("s3_client.concluir_multipart", return_value="etag-final")
    mocker.patch("s3_client.abortar_multipart")
    mocker.patch("s3_client.delete_object")
    mocker.patch("s3_client.get_presigned_url", side_effect=lambda k, **kw: f"https://s3.example/{k}")
    # O probe por range roda contra a presignada; sem mock ele tentaria abrir a URL falsa.
    mocker.patch("app._probe_podcast_video", return_value=(1920, 1080, 16 / 9))
    return m


def _init(client, size=100 * 2**20, filename="ep.mp4", vinheta="vtest"):
    return client.post("/api/podcast/upload/init",
                       json={"filename": filename, "size": size, "vinheta_id": vinheta})


# ─── calcular_partes: a aritmética que o S3 não perdoa ───────────────────────

def test_partes_arredondam_pra_CIMA():
    """Divisão pra baixo deixaria o último pedaço de fora e o objeto sairia truncado, com o
    complete respondendo 200 alegremente."""
    parte, n = podcast_ingest.calcular_partes(config.PODCAST_UPLOAD_PART_BYTES + 1)
    assert n == 2 and parte == config.PODCAST_UPLOAD_PART_BYTES


def test_arquivo_de_1_byte_ainda_tem_1_parte():
    assert podcast_ingest.calcular_partes(1)[1] == 1


def test_acima_de_10000_partes_falha_ANTES_do_primeiro_byte(monkeypatch):
    """O limite é do S3. Sem esta guarda o usuário subiria horas de arquivo pra receber um
    InvalidPart incompreensível no fim."""
    monkeypatch.setattr(config, "PODCAST_UPLOAD_PART_BYTES", 1024)
    with pytest.raises(ValueError, match="10000"):
        podcast_ingest.calcular_partes(1024 * 10001)


# ─── o token: é uma credencial ao portador ───────────────────────────────────

def test_token_de_OUTRO_usuario_e_recusado():
    """Sem a checagem de dono, um token vazado autoriza escrever no upload alheio."""
    t = podcast_ingest.emitir_token({"user_email": "a@test", "job_id": "x"})
    assert podcast_ingest.ler_token(t, "a@test")["job_id"] == "x"
    with pytest.raises(ValueError, match="outro usuário"):
        podcast_ingest.ler_token(t, "b@test")


def test_token_adulterado_e_recusado():
    """O payload viaja no cliente. Se a assinatura não fosse verificada, trocar o job_id no
    token daria escrita em qualquer key."""
    t = podcast_ingest.emitir_token({"user_email": "a@test", "job_id": "x"})
    with pytest.raises(ValueError, match="inválido"):
        podcast_ingest.ler_token(t[:-4] + "AAAA", "a@test")


def test_token_expirado_e_recusado(monkeypatch):
    monkeypatch.setattr(config, "PODCAST_UPLOAD_TOKEN_TTL", -1)
    t = podcast_ingest.emitir_token({"user_email": "a@test"})
    with pytest.raises(ValueError, match="expirou"):
        podcast_ingest.ler_token(t, "a@test")


# ─── /init ────────────────────────────────────────────────────────────────────

def test_init_abre_multipart_e_NAO_cria_job(client, s3_falso):
    """Nada no banco entre init e complete: um job criado aqui apareceria na lista de projetos
    por 75min com todos os botões dando 409, e seria excluível pelo DELETE por baixo das
    partes em voo."""
    _vinheta()
    _login(client)
    r = _init(client)

    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    assert d["part_count"] == -(-100 * 2**20 // config.PODCAST_UPLOAD_PART_BYTES) == 7
    assert d["concurrency"] == config.PODCAST_UPLOAD_CONCURRENCY
    assert db.get_job(d["job_id"]) is None, "init criou linha no banco"


def test_init_deriva_a_key_no_SERVIDOR(client, s3_falso):
    """A key NUNCA pode vir do cliente: presignada é credencial ao portador, então aceitar
    key do cliente deixaria qualquer usuário logado assinar um PUT pra
    jobs/<outro>/podcast/final.mp4 e sobrescrever o episódio pronto de outra pessoa."""
    _vinheta()
    _login(client)
    r = client.post("/api/podcast/upload/init",
                    json={"filename": "ep.mp4", "size": 1024, "vinheta_id": "vtest",
                          "key": "jobs/VITIMA/podcast/final.mp4"})

    assert r.status_code == 201
    key_usada = s3_falso.call_args.args[0]
    assert key_usada == f"jobs/{r.get_json()['job_id']}/podcast/source.mp4"
    assert "VITIMA" not in key_usada


def test_init_recusa_extensao_e_vinheta_invalidas(client, s3_falso):
    _vinheta()
    _login(client)
    assert _init(client, filename="virus.exe").status_code == 400
    assert _init(client, vinheta="nao-existe").status_code == 400
    s3_falso.assert_not_called()


def test_init_recusa_acima_do_teto(client, s3_falso, monkeypatch):
    """Com upload direto o Flask nunca vê o corpo, então o MAX_CONTENT_LENGTH dele não protege
    mais nada: esta é a ÚNICA barreira de tamanho que sobra."""
    monkeypatch.setattr(config, "PODCAST_UPLOAD_MAX_BYTES", 2**30)
    _vinheta()
    _login(client)

    r = _init(client, size=5 * 2**30)

    assert r.status_code == 400 and "limite" in r.get_json()["error"].lower()
    s3_falso.assert_not_called(), "abriu multipart de arquivo que já sabia ser grande demais"


def test_init_recusa_anonimo(client, s3_falso):
    assert _init(client).status_code in (401, 403)
    s3_falso.assert_not_called()


def test_kill_switch_off_devolve_404(client, s3_falso, monkeypatch):
    """Default OFF: enquanto não for ligado no .env, a rota nem existe pro cliente."""
    monkeypatch.setattr(config, "PODCAST_UPLOAD_DIRETO", False)
    _vinheta()
    _login(client)
    assert _init(client).status_code == 404


def test_kill_switch_marca_o_404_pro_front_distinguir(client, s3_falso, monkeypatch):
    """O front cai no caminho ANTIGO (o de 21% de sucesso) quando vê 404 aqui. Sem o marcador,
    um 404 de outra causa (deploy fora de ordem: front novo, backend antigo) rebaixaria todo
    mundo em silêncio, e ninguém veria pelos sintomas."""
    monkeypatch.setattr(config, "PODCAST_UPLOAD_DIRETO", False)
    _vinheta()
    _login(client)

    r = _init(client)

    assert r.status_code == 404
    assert r.get_json()["upload_direto"] is False


# ─── /parts ───────────────────────────────────────────────────────────────────

def test_parts_devolve_presignadas_do_lote(client, s3_falso):
    _vinheta()
    _login(client)
    tok = _init(client).get_json()["token"]

    r = client.post("/api/podcast/upload/parts", json={"token": tok, "parts": [1, 2, 3]})

    assert r.status_code == 200
    assert sorted(r.get_json()["urls"]) == ["1", "2", "3"]


def test_parts_recusa_lote_gigante(client, s3_falso, monkeypatch):
    """Sem teto, um POST pede 100 mil URLs e o servidor assina 100 mil HMACs numa das 4
    threads do gunicorn."""
    monkeypatch.setattr(config, "PODCAST_UPLOAD_LOTE", 5)
    _vinheta()
    _login(client)
    tok = _init(client).get_json()["token"]

    r = client.post("/api/podcast/upload/parts", json={"token": tok, "parts": list(range(1, 50))})

    assert r.status_code == 400


def test_parts_com_token_de_outro_usuario_da_403(client, s3_falso):
    """O ataque direto: pegar o token de outra sessão e pedir presignadas de escrita pra key
    dela."""
    _vinheta()
    _login(client, "dono@test")
    tok = _init(client).get_json()["token"]
    client.post("/api/logout")
    _login(client, "invasor@test")

    r = client.post("/api/podcast/upload/parts", json={"token": tok, "parts": [1]})

    assert r.status_code == 403


def test_parts_recusa_numero_acima_do_declarado_no_init(client, s3_falso):
    """O teto de tamanho é auto-declarado se este teste não existir: o cliente anuncia 16MiB
    (1 parte), pede presignadas de 1 a 10000 e, como a presignada não fixa Content-Length,
    sobe 5GiB em cada uma. Seriam ~48TB num bucket pago com o servidor tendo autorizado 16MiB."""
    _vinheta()
    _login(client)
    tok = _init(client, size=1024).get_json()["token"]      # 1 parte só

    ok = client.post("/api/podcast/upload/parts", json={"token": tok, "parts": [1]})
    abuso = client.post("/api/podcast/upload/parts", json={"token": tok, "parts": [1, 2, 3]})

    assert ok.status_code == 200
    assert abuso.status_code == 400


# ─── /complete: onde o job nasce, e a idempotência que salva os 10GB ─────────

def test_complete_cria_o_job_em_uploaded_com_a_key(client, s3_falso):
    _vinheta()
    _login(client)
    ini = _init(client).get_json()

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "abc"}]})

    assert r.status_code == 201, r.get_json()
    job = db.get_job(ini["job_id"])
    assert job["status"] == "uploaded" and job["job_type"] == "podcast"
    assert job["podcast_source_key"] == f"jobs/{ini['job_id']}/podcast/source.mp4"
    assert job["local_path"] is None, "com upload direto o fonte ainda não está em disco"


def test_complete_e_IDEMPOTENTE(client, s3_falso):
    """A única janela que sobra: restart (ou queda de rede) entre o último byte e esta
    chamada. O cliente repete, e repetir não pode criar job duplicado nem dar erro, senão o
    usuário perde os 10GB que JÁ subiram, que é o incidente inteiro."""
    _vinheta()
    _login(client)
    ini = _init(client).get_json()
    corpo = {"token": ini["token"], "parts": [{"number": 1, "etag": "abc"}]}

    r1 = client.post("/api/podcast/upload/complete", json=corpo)
    r2 = client.post("/api/podcast/upload/complete", json=corpo)

    assert r1.status_code == 201 and r2.status_code == 200
    assert r2.get_json()["job_id"] == ini["job_id"]


def test_complete_aceita_NoSuchUpload_se_o_objeto_esta_montado(client, s3_falso, mocker):
    """NoSuchUpload é a MESMA resposta do S3 pra "já concluí", "abortei" e "expirou". Quem
    desambigua é o objeto final: com o tamanho certo, o complete anterior funcionou e este é
    um retry legítimo. Sem esta desambiguação o usuário perderia um upload que DEU CERTO."""
    _vinheta()
    _login(client)
    ini = _init(client, size=1234).get_json()
    mocker.patch("s3_client.concluir_multipart", side_effect=ClientError(
        {"Error": {"Code": "NoSuchUpload"}}, "CompleteMultipartUpload"))
    mocker.patch("s3_client.head_size", return_value=1234)     # o objeto ESTÁ lá, e certo

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 201
    assert db.get_job(ini["job_id"])["status"] == "uploaded"


def test_complete_recusa_NoSuchUpload_sem_objeto(client, s3_falso, mocker):
    """O outro lado da mesma moeda: uploadId sumiu E não há objeto. Aqui criar o job seria
    pior que o erro, porque nasceria um projeto apontando pra uma key que não existe."""
    _vinheta()
    _login(client)
    ini = _init(client, size=1234).get_json()
    mocker.patch("s3_client.concluir_multipart", side_effect=ClientError(
        {"Error": {"Code": "NoSuchUpload"}}, "CompleteMultipartUpload"))
    mocker.patch("s3_client.head_size", side_effect=Exception("404"))

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 409
    assert db.get_job(ini["job_id"]) is None


def test_complete_confere_o_TAMANHO_REAL_e_recusa_divergente(client, s3_falso, mocker):
    """O par do teste acima, na outra ponta: mesmo com as partes limitadas, nada impede que
    elas sejam maiores do que o anunciado. Sem conferir o objeto montado contra o tamanho
    declarado, PODCAST_UPLOAD_MAX_BYTES é só um número que o cliente escolheu."""
    _vinheta()
    _login(client)
    ini = _init(client, size=100 * 2**20).get_json()
    mocker.patch("s3_client.head_size", return_value=900 * 2**30)   # montou 900GB
    apagou = mocker.patch("s3_client.delete_object")

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 400
    assert db.get_job(ini["job_id"]) is None
    apagou.assert_called_once()


def test_complete_segue_se_nao_der_pra_conferir_o_tamanho(client, s3_falso, mocker):
    """S3 instável não pode custar um upload que deu certo: a conferência falha ABERTA de
    propósito (o teto do init e o lifecycle continuam valendo), porque recusar aqui jogaria
    fora 10GB por causa de um HEAD que não respondeu."""
    _vinheta()
    _login(client)
    ini = _init(client).get_json()
    mocker.patch("s3_client.head_size", side_effect=Exception("S3 instável"))

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 201


def test_telemetria_do_cliente_nao_derruba_o_complete(client, s3_falso):
    """A telemetria é entrada externa e é lida DEPOIS de o job existir: um TypeError ali
    viraria 500 com o job já criado, e o usuário acharia que perdeu o upload."""
    _vinheta()
    _login(client)
    ini = _init(client).get_json()

    r = client.post("/api/podcast/upload/complete", json={
        "token": ini["token"], "parts": [{"number": 1, "etag": "a"}],
        "medicao": {"ms": "nao-e-numero", "retries": "x\n2026-01-01 FALSO [opussb.app] forjado"}})

    assert r.status_code == 201
    assert db.get_job(ini["job_id"]) is not None


def test_complete_recusa_nao_16x9_e_APAGA_o_objeto(client, s3_falso, mocker):
    """Diferente do caminho antigo (que recusava antes de qualquer arquivo existir), aqui o
    objeto JÁ está no bucket pago quando a validação roda. Recusar sem apagar deixaria GB
    órfãos que nada ceifa."""
    _vinheta()
    _login(client)
    ini = _init(client).get_json()
    mocker.patch("app._probe_podcast_video", return_value=(1080, 1080, 1.0))
    apagou = mocker.patch("s3_client.delete_object")

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 400 and "16:9" in r.get_json()["error"]
    assert db.get_job(ini["job_id"]) is None
    apagou.assert_called_once_with(f"jobs/{ini['job_id']}/podcast/source.mp4")


def test_complete_aceita_quando_o_probe_ESTOURA(client, s3_falso, mocker):
    """Timeout do probe remoto não pode recusar: com o moov no fim do arquivo o ffprobe
    puxaria os 8GB inteiros. Aceita e deixa o processamento (com o arquivo local) decidir,
    porque recusar aqui jogaria fora um upload de 75 minutos por causa de layout de moov."""
    import subprocess as sp
    _vinheta()
    _login(client)
    ini = _init(client).get_json()
    mocker.patch("app._probe_podcast_video", side_effect=sp.TimeoutExpired("ffprobe", 60))

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 201
    assert db.get_job(ini["job_id"])["status"] == "uploaded"


def test_complete_recusa_se_a_vinheta_sumiu_no_meio(client, s3_falso, mocker):
    """75min é tempo de sobra pra um admin apagar a vinheta. Sem esta checagem o job nasceria
    e só morreria lá no /compose, depois de o usuário achar que deu tudo certo."""
    _vinheta()
    _login(client)
    ini = _init(client).get_json()
    apagou = mocker.patch("s3_client.delete_object")
    db.delete_vinheta("vtest")

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 409
    assert db.get_job(ini["job_id"]) is None
    apagou.assert_called_once()


def test_complete_concorrente_nao_estoura_500(client, s3_falso, mocker):
    """Duplo clique no botão: dois completes em voo passam juntos pelo guard de idempotência
    (o get_job ainda devolve None pros dois) e os dois tentam inserir. Sem tratar, o segundo
    devolve 500 por UNIQUE constraint e o usuário acha que perdeu o upload que DEU CERTO."""
    import sqlite3 as sq
    _vinheta()
    _login(client)
    ini = _init(client).get_json()
    mocker.patch("db.create_podcast_job", side_effect=sq.IntegrityError("UNIQUE"))

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 200 and r.get_json()["job_id"] == ini["job_id"]


def test_recusa_com_S3_fora_do_ar_ainda_responde(client, s3_falso, mocker):
    """A limpeza do objeto recusado é best-effort: se o delete falhar, o usuário tem que
    receber a recusa mesmo assim (com o órfão registrado no log), não um 500 genérico."""
    _vinheta()
    _login(client)
    ini = _init(client).get_json()
    mocker.patch("app._probe_podcast_video", return_value=(1080, 1080, 1.0))
    mocker.patch("s3_client.delete_object", side_effect=Exception("S3 fora do ar"))

    r = client.post("/api/podcast/upload/complete",
                    json={"token": ini["token"], "parts": [{"number": 1, "etag": "a"}]})

    assert r.status_code == 400 and "16:9" in r.get_json()["error"]


# ─── /abort ───────────────────────────────────────────────────────────────────

def test_abort_chama_o_S3_e_e_idempotente(client, s3_falso, mocker):
    """Cancelar sem AbortMultipartUpload deixa GB de partes COBRADAS e invisíveis no
    list_objects_v2. Foi o pedido explícito do dono ao usar a feature."""
    ab = mocker.patch("s3_client.abortar_multipart")
    _vinheta()
    _login(client)
    tok = _init(client).get_json()["token"]

    assert client.post("/api/podcast/upload/abort", json={"token": tok}).status_code == 200
    ab.assert_called_once()

    ab.side_effect = ClientError({"Error": {"Code": "NoSuchUpload"}}, "AbortMultipartUpload")
    assert client.post("/api/podcast/upload/abort", json={"token": tok}).status_code == 200


# ─── materialização: o fonte que nasce no S3 e precisa chegar ao disco ───────

def test_materializa_e_idempotente_e_nao_toca_a_rede(mocker, tmp_path):
    """Com o arquivo já em disco não pode haver download nenhum: os 8 pontos do pipeline
    chamam isto, e re-baixar 10GB a cada etapa seria pior que o problema original."""
    baixou = mocker.patch("s3_client.download_file")
    p = tmp_path / "original.mp4"
    p.write_bytes(b"x")

    assert podcast_ingest.garantir_fonte_local(
        {"job_id": "j", "local_path": str(p), "podcast_source_key": "k"}) == str(p)
    baixou.assert_not_called()


def test_materializa_usa_CONCORRENCIA_REDUZIDA(mocker, tmp_path, monkeypatch):
    """Download pesado é o padrão correlacionado 4 de 4 com o bloqueio de TCP do provedor (8 a
    20min de rede parada). Esta feature INVERTE o tráfego de upload pra download, então a
    concorrência reduzida é a mitigação: se sumir, o time perde a rede sem ninguém entender
    por quê."""
    monkeypatch.setattr(config, "PODCAST_MATERIALIZA_CONCURRENCY", 2)
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=1000)
    mocker.patch("podcast_ingest.checar_cfr", return_value=True)
    baixou = mocker.patch("s3_client.download_file")

    podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None,
                                         "podcast_source_key": "k"})

    assert baixou.call_args.kwargs["transfer_config"].max_concurrency == 2


def test_materializa_respeita_o_TETO_DE_BANDA(mocker, tmp_path, monkeypatch):
    """A mitigação de verdade do bloqueio de rede. A hipótese de que bastava reduzir
    concorrência foi REFUTADA em 2026-07-26 (concorrência 2 derrubou o TCP igual, aos 84s), e
    quem ficou no lugar dela foi este teto. Sem teste, ele sumiria num refactor e o time
    voltaria a perder 8 a 20min de rede sem ninguém ligar uma coisa à outra."""
    monkeypatch.setattr(config, "PODCAST_MATERIALIZA_MAX_MBPS", 8)
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=1000)
    mocker.patch("podcast_ingest.checar_cfr", return_value=True)
    baixou = mocker.patch("s3_client.download_file")

    podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None,
                                         "podcast_source_key": "k"})

    assert baixou.call_args.kwargs["transfer_config"].max_bandwidth == 8 * 2**20


def test_teto_de_banda_ZERO_nao_limita(mocker, tmp_path, monkeypatch):
    """Default 0 = comportamento de antes. Ligar o limite é decisão de operação (uma linha no
    .env), não efeito colateral de deploy."""
    monkeypatch.setattr(config, "PODCAST_MATERIALIZA_MAX_MBPS", 0)
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=1000)
    mocker.patch("podcast_ingest.checar_cfr", return_value=True)
    baixou = mocker.patch("s3_client.download_file")

    podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None,
                                         "podcast_source_key": "k"})

    assert baixou.call_args.kwargs["transfer_config"].max_bandwidth is None


def test_materializa_BATE_HEARTBEAT_durante_o_download(mocker, tmp_path, monkeypatch):
    """O bloqueador que o code-review pegou. A materialização não escreve nada no banco
    enquanto baixa, e leva ~55min sob estrangulamento; o reap_stale_jobs ceifa em 30min e roda
    em 6 rotas, então qualquer outra pessoa clicando em algo matava um job vivo. Pelo
    /transcribe não havia volta: nada devolve um podcast pra 'uploaded', então a única saída
    era excluir e reenviar os 10GB, que é o incidente que esta frente existe pra eliminar."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=1000)
    mocker.patch("podcast_ingest.checar_cfr", return_value=True)
    bateu = mocker.patch("db.heartbeat_job")
    # O heartbeat vive numa thread com intervalo de 60s: em vez de esperar, verifico que o
    # download roda DENTRO do _com_heartbeat (é isso que a regressão quebraria).
    dentro = {}
    def _fake_download(*a, **kw):
        dentro["ativo"] = True
    mocker.patch("s3_client.download_file", side_effect=_fake_download)
    real = podcast_ingest._com_heartbeat
    chamado = {}
    def _espia(job_id, status, fn):
        chamado["args"] = (job_id, status)
        return real(job_id, status, fn)
    mocker.patch("podcast_ingest._com_heartbeat", side_effect=_espia)

    podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None,
                                         "podcast_source_key": "k", "status": "montando"})

    assert dentro.get("ativo"), "o download não rodou"
    assert chamado["args"] == ("j", "montando"), "download fora do heartbeat: o watchdog ceifa"
    assert bateu.call_count == 0, "não deve bater antes do primeiro intervalo"


def test_materializa_RECUSA_VFR(mocker, tmp_path, monkeypatch):
    """A validação de CFR existia só na rota antiga: pelo caminho novo um export VFR passava,
    gastava RunPod + Claude + o tempo do produtor aprovando o gancho, e só estourava na
    montagem com 'a duração não bate', que não diz a ninguém o que fazer."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=1000)
    mocker.patch("podcast_ingest.checar_cfr", return_value=False)
    def _cria(key, destino, **kw):
        open(destino, "wb").write(b"x")
    mocker.patch("s3_client.download_file", side_effect=_cria)

    with pytest.raises(RuntimeError, match="VFR"):
        podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None,
                                             "podcast_source_key": "k", "status": "uploaded"})
    assert not os.path.exists(str(tmp_path / "j" / "original.mp4")), "8GB de VFR ficaram no disco"


def test_corte_PODE_rebaixar_o_original_pra_refazer(mocker, tmp_path, monkeypatch):
    """Sem isto o projeto travava pra sempre: com o corte aplicado e a cópia local perdida, a
    mensagem mandava 'refaça o corte' e o refazer batia no mesmo guard, porque o podcast_cut
    chama garantir_fonte_local na primeira linha. Quem não pode receber o original é a
    montagem e a transcrição (o teste abaixo), não quem vai reaplicar o cut_at."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=1000)
    mocker.patch("podcast_ingest.checar_cfr", return_value=True)
    mocker.patch("s3_client.download_file")
    job = {"job_id": "j", "local_path": "/sumiu.mp4", "podcast_source_key": "k",
           "intro_cut_at": 120.0, "status": "cortando_inicio"}

    assert podcast_ingest.garantir_fonte_local(job, aceita_original=True)


def test_materializa_respeita_o_FATOR_DE_DISCO_do_chamador(mocker, tmp_path, monkeypatch):
    """O corte exige 3,5x do tamanho do fonte. Com o fator do MP3 (1,3x) o download roda
    inteiro (dezenas de minutos, egress pago) pra o job morrer na linha seguinte."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=10**10)
    baixou = mocker.patch("s3_client.download_file")
    mocker.patch("shutil.disk_usage", return_value=type("U", (), {"free": 2 * 10**10})())

    with pytest.raises(RuntimeError, match="disco"):
        podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None,
                                             "podcast_source_key": "k", "status": "uploaded"},
                                            fator_disco=3.5)
    baixou.assert_not_called()


def test_materializa_RECUSA_por_cima_de_corte_ja_aplicado(mocker, tmp_path, monkeypatch):
    """Depois do corte de início o local_path aponta pro arquivo CORTADO. Re-baixar o original
    desfaria o corte do produtor em silêncio, e o episódio sairia com a parte que ele mandou
    tirar."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    baixou = mocker.patch("s3_client.download_file")

    with pytest.raises(RuntimeError, match="corte"):
        podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": "/sumiu.mp4",
                                             "podcast_source_key": "k", "intro_cut_at": 12.5})
    baixou.assert_not_called()


def test_materializa_para_se_o_disco_nao_couber(mocker, tmp_path, monkeypatch):
    """A VPS não tem swap. Sem o guard-rail, um episódio grande enche o disco e derruba o
    site inteiro, não só o job."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    mocker.patch("s3_client.head_size", return_value=10**15)
    baixou = mocker.patch("s3_client.download_file")

    with pytest.raises(RuntimeError, match="disco"):
        podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None,
                                             "podcast_source_key": "k"})
    baixou.assert_not_called()


def test_materializa_sem_key_falha_claro(tmp_path, monkeypatch):
    """Job antigo (caminho velho) que perdeu o arquivo do disco: mensagem própria, não
    AttributeError."""
    monkeypatch.setattr(config, "JOB_DIR", str(tmp_path))
    with pytest.raises(RuntimeError, match="armazenamento"):
        podcast_ingest.garantir_fonte_local({"job_id": "j", "local_path": None})


# ─── as telas continuam funcionando com o fonte só no S3 ─────────────────────

def _job_no_s3(job_id="TESTdireto", user="dono@test", status="uploaded"):
    db.create_podcast_job(job_id, "ep.mp4", None, user, "vtest",
                          podcast_source_key=f"jobs/{job_id}/podcast/source.mp4")
    db.update_job(job_id, status=status)
    return job_id


def test_source_redireciona_pro_S3_quando_nao_ha_arquivo_local(client, s3_falso):
    """Sem isto o upload funcionaria e a tela seguinte (preview do trim) daria 409. O 302 pra
    presignada mantém o Range: o S3 responde 206 nativamente, então o preview funciona igual e
    a VPS sai do caminho dos bytes também na leitura."""
    _vinheta()
    _login(client)
    jid = _job_no_s3()

    r = client.get(f"/api/podcast/{jid}/source")

    assert r.status_code == 302
    assert f"jobs/{jid}/podcast/source.mp4" in r.headers["Location"]


def test_source_usa_TTL_CURTO_no_master_bruto(client, s3_falso, mocker):
    """A presignada é link ao portador que nenhuma revogação de sessão alcança, e isto é o
    MASTER BRUTO não editado (o arquivo mais sensível que a produtora tem antes de publicar).
    O default de 24h dos entregáveis seria longo demais aqui."""
    url = mocker.patch("s3_client.get_presigned_url", return_value="https://s3.example/x")
    _vinheta()
    _login(client)
    jid = _job_no_s3()

    client.get(f"/api/podcast/{jid}/source")

    assert url.call_args.kwargs["expiry_seconds"] <= 3600


def test_source_available_enxerga_o_fonte_no_S3(client, s3_falso):
    """O front esconde os botões de trim e disparo quando isto é false: com o fonte só no S3
    ele bloquearia o pipeline inteiro logo depois de um upload bem-sucedido."""
    _vinheta()
    _login(client)
    jid = _job_no_s3()

    d = client.get(f"/api/podcast/{jid}/hooks").get_json()

    assert d["source_available"] is True


def test_hooks_de_job_SEM_fonte_nenhum_segue_false(client, s3_falso):
    """Não regressão da outra ponta: job antigo que perdeu o arquivo não pode passar a
    anunciar fonte que não existe."""
    _vinheta()
    _login(client)
    db.create_podcast_job("TESTsemfonte", "ep.mp4", "/sumiu.mp4", "dono@test", "vtest")

    d = client.get("/api/podcast/TESTsemfonte/hooks").get_json()

    assert d["source_available"] is False


# ─── a duplicação do CFR, travada contra o original ──────────────────────────

def _mp4(tmp_path, name, rate=30, dur=1):
    import subprocess
    out = str(tmp_path / name)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc2=size=320x180:rate={rate}:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", out], check=True)
    return out


def test_cfr_bate_com_a_rota_antiga(tmp_path):
    """podcast_ingest.checar_cfr é uma cópia de app._podcast_is_cfr (importar de volta fecharia
    ciclo, porque app importa podcast_ingest). Cópia sem teste de equivalência é como as duas
    versões divergem em silêncio: uma recusa VFR e a outra aceita, dependendo do caminho de
    upload que o usuário pegou."""
    import subprocess
    import app as app_module

    cfr = _mp4(tmp_path, "cfr.mp4", rate=30)
    a, b = _mp4(tmp_path, "a.mp4", rate=30), _mp4(tmp_path, "b.mp4", rate=15)
    lst = tmp_path / "c.txt"
    lst.write_text(f"file '{a}'\nfile '{b}'\n")
    vfr = str(tmp_path / "vfr.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", vfr], check=True)

    for arq, esperado in ((cfr, True), (vfr, False)):
        assert podcast_ingest.checar_cfr(arq) is esperado
        assert app_module._podcast_is_cfr(arq) is esperado, "as duas implementações divergiram"
