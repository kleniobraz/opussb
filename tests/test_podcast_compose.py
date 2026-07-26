"""Parte 5 do pipeline de podcast (§39): montagem gancho + vinheta + corpo.
Prova com ffmpeg de verdade (sintéticos pequenos) os requisitos do teste de VFR:
assinatura POR ARQUIVO (profile/level/SAR), anullsrc na vinheta SEM áudio, corpo em
-c copy (travado pela função pura de comando), e as DUAS emendas contínuas (DTS
estritamente crescente, salto <= 1,5 frame). S3 mockado; DB/JOB_DIR do conftest."""
import json
import os
import shutil
import sqlite3
import subprocess
import time

import pytest
from werkzeug.security import generate_password_hash

import config
import db
import podcast_audio
import podcast_compose
import s3_client


# ─── Sintéticos ───────────────────────────────────────────────────────────────

def _mp4(path, *, size, rate, dur, audio):
    inputs = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}"]
    if audio:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                   "-c:a", "aac", "-ar", "48000", "-ac", "2"]
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


def _corpo(tmp_path, dur=10):
    return _mp4(tmp_path / "corpo.mp4", size="320x180", rate=30, dur=dur, audio=True)


def _vinheta_muda(tmp_path, dur=2):
    return _mp4(tmp_path / "vinheta.mp4", size="180x320", rate=60, dur=dur, audio=False)


def _packet_dts(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "packet=dts_time", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return [float(x) for x in r.stdout.split() if x and x != "N/A"]


def _espera(cond, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


# ─── probe_av_signature (estendida; contrato antigo preservado) ───────────────

def test_probe_estendida_le_assinatura_por_arquivo(tmp_path):
    sig = podcast_compose.probe_av_signature(_corpo(tmp_path))
    assert sig["fps_s"] == "30/1"
    assert sig["ar"] == 48000 and sig["ac"] == 2 and sig["has_audio"] is True
    assert sig["width"] == 320 and sig["height"] == 180
    assert sig["pix_fmt"] == "yuv420p"
    assert sig["profile"]                      # x264 reporta (High nos sintéticos)
    assert sig["nb_v"] == 1 and sig["nb_a"] == 1


def test_probe_sem_audio_mantem_contrato_da_tela_final(tmp_path):
    muda = _vinheta_muda(tmp_path)
    assert podcast_compose.probe_av_signature(muda) is None          # contrato antigo
    sig = podcast_compose.probe_av_signature(muda, require_audio=False)
    assert sig["has_audio"] is False
    assert sig["ar"] == 48000 and sig["ac"] == 2                     # defaults pro anullsrc
    assert podcast_compose.probe_av_signature("/tmp/nao-existe.mp4") is None


# ─── Funções puras de comando (travam a técnica) ──────────────────────────────

def test_cmd_do_corpo_e_c_copy_e_pedacos_nunca_sao_copy():
    concat = podcast_compose.build_concat_cmd("lista.txt", "final.mp4")
    assert "-c" in concat and concat[concat.index("-c") + 1] == "copy"

    sig = {"fps_s": "30/1", "timescale": 15360, "ar": 48000, "ac": 2, "has_audio": True,
           "profile": "High", "level": 41, "sar": "1:1", "width": 1920, "height": 1080,
           "pix_fmt": "yuv420p", "nb_v": 1, "nb_a": 1}
    cmd = podcast_compose.build_matched_cmd("v.mp4", "out.mp4", sig, silence=True)
    assert "copy" not in cmd
    assert any("anullsrc=r=48000:cl=stereo" in c for c in cmd)
    assert "-shortest" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:v:0" and "1:a:0" in cmd  # áudio vem do silêncio
    assert cmd[cmd.index("-profile:v") + 1] == "high"
    assert cmd[cmd.index("-level:v") + 1] == "4.1"
    assert cmd[cmd.index("-r") + 1] == "30/1"

    cmd = podcast_compose.build_matched_cmd("p.mp4", "g.mp4", sig, seek=(32.0, 61.5))
    assert cmd[cmd.index("-ss") + 1] == "32.000" and cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-t") + 1] == "61.500"
    assert "0:a:0" in cmd and "anullsrc" not in " ".join(cmd)

    corpo_mudo = dict(sig, has_audio=False)
    cmd = podcast_compose.build_matched_cmd("v.mp4", "out.mp4", corpo_mudo, silence=False)
    assert "0:a:0" not in cmd and "-c:a" not in cmd                  # pedaço sai só com vídeo

    perfil_exotico = dict(sig, profile="High 4:4:4 Predictive", level=-99)
    cmd = podcast_compose.build_matched_cmd("v.mp4", "out.mp4", perfil_exotico)
    assert "-profile:v" not in cmd and "-level:v" not in cmd         # sem flag > x264 falhar


# ─── Montagem real com sintéticos ─────────────────────────────────────────────

@pytest.fixture
def s3_falso(mocker, tmp_path):
    """S3 em disco: download serve a vinheta sintética; upload captura o final."""
    caixa = {"uploads": {}}

    def _download(key, dst):
        shutil.copyfile(caixa["objetos"][key], dst)

    def _upload(local, key):
        dst = tmp_path / ("up_" + key.replace("/", "_"))
        shutil.copyfile(local, dst)
        caixa["uploads"][key] = str(dst)

    mocker.patch("s3_client.exists", return_value=False)
    mocker.patch("s3_client.download_file", side_effect=_download)
    mocker.patch("s3_client.upload_file", side_effect=_upload)
    return caixa


def _job_pronto(tmp_path, caixa, job_id="TEST-compose", dur=10):
    """Job de podcast 'gancho_aprovado' com corpo sintético DENTRO do JOB_DIR e vinheta
    muda na biblioteca (o download do S3 mockado serve o arquivo local)."""
    job_dir = os.path.join(config.JOB_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    corpo = _corpo(tmp_path, dur=dur)
    src = os.path.join(job_dir, "original.mp4")
    shutil.copyfile(corpo, src)
    db.init_vinhetas()
    db.add_vinheta("vtest", "Vinheta de teste", "assets/vinhetas/vtest.mp4", 2.0)
    caixa["objetos"] = {"assets/vinhetas/vtest.mp4": _vinheta_muda(tmp_path)}
    db.create_podcast_job(job_id, "epis.mp4", src, "t@t", "vtest")
    db.update_job(job_id, status="gancho_aprovado", progress=90,
                  hook_choice=json.dumps({"index": 0, "start": 2.0, "end": 4.5}))
    return job_id


def test_compose_monta_com_emendas_continuas_e_anullsrc(tmp_path, s3_falso):
    jid = _job_pronto(tmp_path, s3_falso)
    db.update_job(jid, status="montando", progress=10)
    podcast_compose.run_podcast_compose(jid)

    job = db.get_job(jid)
    assert job["status"] == "done", job.get("error")
    key = job["podcast_final_key"]
    assert key == f"jobs/{jid}/podcast/final.mp4"
    final = s3_falso["uploads"][key]

    # Duração = gancho (2,5s) + vinheta (2s) + corpo (10s)
    dur = podcast_compose.duration(final)
    assert abs(dur - 14.5) < 0.6, dur

    # Assinatura do final = a do corpo, e as trilhas existem (anullsrc entrou na vinheta)
    sig = podcast_compose.probe_av_signature(final)
    assert sig and sig["fps_s"] == "30/1" and sig["width"] == 320 and sig["height"] == 180
    assert sig["nb_v"] == 1 and sig["nb_a"] == 1

    # As DUAS emendas contínuas: DTS estritamente crescente e sem salto > 1,5 frame
    dts = _packet_dts(final)
    assert len(dts) > 400
    passo = 1.0 / 30.0
    for antes, agora in zip(dts, dts[1:]):
        assert agora > antes, "DTS regrediu (emenda corrompida)"
        assert agora - antes <= passo * 1.5 + 1e-6, f"salto de {agora - antes:.4f}s na emenda"

    # Regra 7: a fonte só morre DEPOIS do final no S3 (e aqui o upload foi confirmado)
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid))


# ─── Cancelamento cooperativo (bug real: _compose não checava, ressuscitava "done") ──

def test_cancela_apos_upload(tmp_path, s3_falso, mocker):
    """Cancelamento chegando logo após o upload caro do final não deixa o status virar
    'done' nem perde a mensagem original "Cancelado pelo usuário."."""
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-cancel-upload")
    db.update_job(jid, status="montando")
    final_key = f"jobs/{jid}/podcast/final.mp4"

    def _upload_e_cancela(local, key):
        dst = tmp_path / ("up_" + key.replace("/", "_"))
        shutil.copyfile(local, dst)
        s3_falso["uploads"][key] = str(dst)
        if key == final_key:
            db.update_job(jid, status="error", error="Cancelado pelo usuário.")

    mocker.patch("s3_client.upload_file", side_effect=_upload_e_cancela)

    podcast_compose.run_podcast_compose(jid)

    job = db.get_job(jid)
    assert job["status"] == "error"
    assert job["error"] == "Cancelado pelo usuário."          # não "montagem falhou: ..."
    assert job["podcast_final_key"] is None                   # a linha do "done" nunca rodou
    assert final_key in s3_falso["uploads"]                    # upload caro não foi refeito nem perdido
    assert os.path.exists(job["local_path"])                   # regra 7: fonte preservada
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid, "compose"))


def test_cancela_antes_do_corte(tmp_path, s3_falso, mocker):
    """Cancelamento antes do primeiro ffmpeg para cedo, sem rodar nada à toa."""
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-cancel-cedo")
    db.update_job(jid, status="error", error="Cancelado pelo usuário.")

    espiao = mocker.patch("podcast_compose.run_ffmpeg")

    podcast_compose.run_podcast_compose(jid)

    espiao.assert_not_called()
    job = db.get_job(jid)
    assert job["status"] == "error" and job["error"] == "Cancelado pelo usuário."
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid, "compose"))


def test_compose_falha_preserva_fonte_e_vira_error(tmp_path, s3_falso):
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-err")
    db.update_job(jid, hook_choice=json.dumps({"index": 0, "start": 2.0, "end": 999.0}))
    db.update_job(jid, status="montando")
    podcast_compose.run_podcast_compose(jid)

    job = db.get_job(jid)
    assert job["status"] == "error" and "montagem falhou" in job["error"]
    assert os.path.exists(job["local_path"])                     # fonte fica pro retry
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid, "compose"))


def test_guard_rail_de_disco(tmp_path, s3_falso, monkeypatch):
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-disk")
    monkeypatch.setattr(config, "COMPOSE_DISK_FACTOR", 10 ** 9)  # exige o impossível
    db.update_job(jid, status="montando")
    podcast_compose.run_podcast_compose(jid)
    job = db.get_job(jid)
    assert job["status"] == "error" and "disco insuficiente" in job["error"]


# ─── Estados: montando é ATIVO (os dois lados) ────────────────────────────────

def test_montando_conta_na_fila_e_e_ceifavel():
    db.create_podcast_job("TEST-mont", "p.mp4", "/tmp/TEST-m.mp4", "t@t", "v1")
    db.update_job("TEST-mont", status="montando")
    assert db.count_active_jobs() == 1

    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00'")
    con.commit()
    con.close()
    assert db.reap_stale_jobs(30) == 1
    assert db.get_job("TEST-mont")["status"] == "error"


# ─── Rotas ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _login(client, email):
    db.upsert_user(email, generate_password_hash("senha-de-teste"), must_change=False)
    r = client.post("/api/login", json={"email": email, "password": "senha-de-teste"})
    assert r.status_code == 200, r.get_json()


def test_compose_route_guardas(client, monkeypatch, tmp_path, s3_falso):
    jid = _job_pronto(tmp_path, s3_falso)
    monkeypatch.setattr(config, "PODCAST_ENABLED", False)
    _login(client, "t@t")
    assert client.post(f"/api/podcast/{jid}/compose").status_code == 404

    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    db.create_job("TEST-clip5", "c.mp4", 30, 90, "/tmp/TEST-c5.mp4")
    assert client.post("/api/podcast/TEST-clip5/compose").status_code == 400

    db.update_job("TEST-clip5", status="cutting")                # fila ocupada -> 429
    assert client.post(f"/api/podcast/{jid}/compose").status_code == 429
    db.update_job("TEST-clip5", status="done")

    client.post("/api/logout")
    _login(client, "outro@test")
    assert client.post(f"/api/podcast/{jid}/compose").status_code == 403


def test_compose_route_feliz_e_final(client, monkeypatch, tmp_path, s3_falso, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso)
    _login(client, "t@t")

    assert client.get(f"/api/podcast/{jid}/final").status_code == 409   # antes de montar

    r = client.post(f"/api/podcast/{jid}/compose")
    assert r.status_code == 202, r.get_json()
    assert db.get_job(jid)["status"] == "montando"
    assert _espera(lambda: db.get_job(jid)["status"] in ("done", "error"))
    assert db.get_job(jid)["status"] == "done", db.get_job(jid).get("error")

    r = client.get(f"/api/podcast/{jid}/hooks")
    body = r.get_json()
    assert body["final_available"] is True and body["status"] == "done"

    mocker.patch("s3_client.get_presigned_url", return_value="https://s3.fake/final")
    r = client.get(f"/api/podcast/{jid}/final")
    assert r.status_code == 302 and r.headers["Location"] == "https://s3.fake/final"

    # Sem gancho aprovado (status errado pro compose): 409
    r = client.post(f"/api/podcast/{jid}/compose")
    assert r.status_code == 409


def test_compose_retry_apos_error(client, monkeypatch, tmp_path, s3_falso):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-retry")
    db.update_job(jid, status="error", error="montagem falhou: teste")
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/compose")
    assert r.status_code == 202
    assert _espera(lambda: db.get_job(jid)["status"] in ("done", "error"))
    assert db.get_job(jid)["status"] == "done", db.get_job(jid).get("error")


# ─── MP3 extraído NA MONTAGEM (elimina o round-trip de dezenas de GB) ─────────

def test_montagem_extrai_mp3_sem_round_trip(tmp_path, s3_falso, monkeypatch):
    """Caminho feliz: o MP3 sai junto da montagem, com o final.mp4 ainda local, e o
    download do episódio de volta do S3 NUNCA acontece (é o ponto inteiro da mudança)."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-ok")
    db.update_job(jid, status="montando")

    podcast_compose.run_podcast_compose(jid)

    job = db.get_job(jid)
    assert job["status"] == "done", job.get("error")
    assert job["podcast_final_key"] == f"jobs/{jid}/podcast/final.mp4"
    assert job["podcast_audio_status"] == "done"
    assert job["podcast_audio_key"] == f"jobs/{jid}/podcast/audio.mp3"
    # o MP3 chegou ao S3 e tem áudio de verdade (não é arquivo vazio)
    mp3 = s3_falso["uploads"][job["podcast_audio_key"]]
    assert os.path.getsize(mp3) > 1024
    assert abs((podcast_compose.duration(mp3) or 0) - 14.5) < 1.0
    # regra 7 continua valendo: fonte apagada no fim
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid))


def test_montagem_nao_baixa_o_final_de_volta(tmp_path, s3_falso, mocker, monkeypatch):
    """Trava o objetivo da mudança: durante a montagem, o final.mp4 NÃO pode ser baixado
    do S3. Se alguém reintroduzir o round-trip, este teste quebra."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-nodl")
    db.update_job(jid, status="montando")
    baixados = []
    orig = s3_client.download_file

    def _spy(key, dst):
        baixados.append(key)
        return orig(key, dst)

    mocker.patch("s3_client.download_file", side_effect=_spy)

    podcast_compose.run_podcast_compose(jid)

    assert db.get_job(jid)["podcast_audio_status"] == "done"
    assert f"jobs/{jid}/podcast/final.mp4" not in baixados, baixados


def test_montagem_sobrevive_a_falha_do_mp3(tmp_path, s3_falso, mocker, monkeypatch):
    """Requisito do dono: o MP3 é acessório. ffmpeg do áudio explodindo NÃO pode derrubar
    a montagem, nem deixar o vídeo fonte órfão no disco."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-falha")
    db.update_job(jid, status="montando")
    mocker.patch("podcast_audio._extract_audio",
                 side_effect=RuntimeError("ffmpeg do audio explodiu"))

    podcast_compose.run_podcast_compose(jid)

    job = db.get_job(jid)
    assert job["status"] == "done"                 # episódio entregue
    assert job["error"] is None                    # e sem erro de montagem
    assert job["podcast_final_key"] == f"jobs/{jid}/podcast/final.mp4"
    assert job["podcast_audio_status"] == "error"  # só o áudio falhou
    assert "explodiu" in (job["podcast_audio_error"] or "")
    # o fonte NÃO pode ficar órfão (guard-rail de disco da próxima montagem depende disso)
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid))


def test_montagem_com_flag_off_nao_extrai_nem_marca_erro(tmp_path, s3_falso, mocker,
                                                         monkeypatch):
    """Kill switch off: nada de ffmpeg de áudio, e o estado fica VAZIO (não 'error'),
    senão o front mentiria "Falha ao extrair o áudio" pra algo que nem foi tentado."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", False)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-off")
    db.update_job(jid, status="montando")
    extrai = mocker.patch("podcast_audio._extract_audio")

    podcast_compose.run_podcast_compose(jid)

    job = db.get_job(jid)
    assert job["status"] == "done"
    assert job["podcast_audio_status"] is None
    extrai.assert_not_called()


def test_estado_extraindo_entra_junto_com_done(tmp_path, s3_falso, mocker, monkeypatch):
    """A corrida que o dono pediu pra cobrir: o front NUNCA pode ver 'done' com o áudio
    vazio, senão mostra o botão "Baixar áudio (MP3)" e o usuário dispara o fallback
    (download do episódio inteiro de volta do S3) em paralelo com a extração local.

    Espia os UPDATEs em vez de ler o banco de dentro da extração: a garantia é de
    ATOMICIDADE, e ela só é observável no momento da escrita. A primeira versão deste teste
    lia o estado de dentro do ffmpeg mockado e continuava VERDE mesmo com a atomicidade
    quebrada em dois updates sequenciais (verificado na mão), ou seja, dava por provada uma
    garantia que não checava."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-corrida")
    db.update_job(jid, status="montando")
    updates = []
    real = db.update_job

    def _espia(job_id, **campos):
        updates.append(campos)
        return real(job_id, **campos)

    mocker.patch("podcast_compose.db.update_job", side_effect=_espia)

    podcast_compose.run_podcast_compose(jid)

    marcou_done = [u for u in updates if u.get("status") == "done"]
    assert len(marcou_done) == 1, f"esperava UM update marcando done: {updates}"
    assert marcou_done[0].get("podcast_audio_status") == "extraindo", (
        "'done' foi gravado sem 'extraindo' no MESMO update: abre a janela em que o front "
        f"mostra o botão e o usuário dispara o download de GB. Update: {marcou_done[0]}")


def test_montagem_desiste_do_slot_ocupado_sem_travar(tmp_path, s3_falso, monkeypatch):
    """Slot de áudio ocupado por uma extração sob demanda: a montagem NÃO pode ficar
    esperando pra sempre, porque enquanto espera o vídeo fonte (dezenas de GB) segue no
    disco e nada mais o libera (job 'done' não conta em count_active_jobs nem é ceifado
    pelo reap_stale_jobs). Desiste no teto, entrega o episódio, áudio fica pro fallback."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    monkeypatch.setattr(podcast_compose, "_AUDIO_SLOT_TIMEOUT", 0.3)   # teto curto no teste
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-slot")
    db.update_job(jid, status="montando")

    podcast_audio._audio_sem.acquire()      # simula outra extração segurando a fila
    try:
        t0 = time.monotonic()
        podcast_compose.run_podcast_compose(jid)
        gasto = time.monotonic() - t0
    finally:
        podcast_audio._audio_sem.release()

    job = db.get_job(jid)
    assert job["status"] == "done"                     # episódio entregue mesmo assim
    assert job["error"] is None
    assert job["podcast_audio_status"] == "error"
    assert "ocupada" in (job["podcast_audio_error"] or "")
    assert gasto < 30, f"esperou {gasto:.1f}s: o teto do slot não pegou"
    # e o fonte foi liberado, que é o motivo de existir o teto
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid))


def test_health_nao_reporta_zero_durante_a_extracao(tmp_path, s3_falso, mocker, monkeypatch):
    """O ritual de deploy é "restart do opussb só com active_jobs=0". Se o /health responder
    0 enquanto a extração de MP3 roda, o restart mata a thread no meio e deixa o final.mp4
    órfão no disco (job já 'done' não é ceifado pelo reap_stale_jobs). Este teste é o que
    impede o gate de deploy de mentir."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-health")
    db.update_job(jid, status="montando")
    medido = {}

    def _durante_extracao(src, out):
        # roda no meio da extração: é exatamente quando o deploy consultaria o /health
        medido["andamento"] = db.count_trabalho_em_andamento()
        medido["fila"] = db.count_active_jobs()
        return _extract_real(src, out)

    _extract_real = podcast_audio._extract_audio
    mocker.patch("podcast_audio._extract_audio", side_effect=_durante_extracao)

    podcast_compose.run_podcast_compose(jid)

    assert medido, "a extração nem rodou"
    assert medido["andamento"] >= 1, (
        "/health reportaria active_jobs=0 durante a extração: o gate de deploy daria "
        "sinal verde no pior momento")
    # e o outro uso NÃO pode ter sido contaminado: a fila de admissão segue livre, senão
    # cada episódio montado travaria jobs novos por minutos
    assert medido["fila"] == 0, (
        "a extração entrou em count_active_jobs e vai travar a admissão de jobs novos")
    assert db.count_trabalho_em_andamento() == 0      # e zera quando termina


def test_fonte_apagada_logo_apos_done(tmp_path, s3_falso, mocker, monkeypatch):
    """A fonte (maior arquivo do job) morre assim que o final está no S3, sem esperar a
    extração terminar: encolhe a janela de exposição a um restart."""
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid = _job_pronto(tmp_path, s3_falso, job_id="TEST-compose-mp3-fonte")
    src = db.get_job(jid)["local_path"]
    db.update_job(jid, status="montando")
    visto = {}

    def _durante_extracao(src_arg, out):
        visto["fonte_existe"] = os.path.exists(src)
        return _extract_real(src_arg, out)

    _extract_real = podcast_audio._extract_audio
    mocker.patch("podcast_audio._extract_audio", side_effect=_durante_extracao)

    podcast_compose.run_podcast_compose(jid)

    assert visto.get("fonte_existe") is False, (
        "a fonte de dezenas de GB ainda estava no disco durante a extração")
    assert db.get_job(jid)["status"] == "done"
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid))
