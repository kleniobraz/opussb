"""Extração de áudio (MP3) do episódio de podcast já montado, sob demanda (thread de
FUNDO + polling). Cobre: função pura do comando ffmpeg, o caminho feliz da extração com
ffmpeg de verdade (S3 mockado), a admissão da rota (lock, staleness, cache-hit) e a rota
de download. Jobs sempre com prefixo TEST; DB/JOB_DIR isolados pelo conftest."""
import os
import shutil
import sqlite3
import subprocess
import threading
import time

import pytest
from werkzeug.security import generate_password_hash

import config
import db
import podcast_audio
import s3_client


# ─── Sintético ────────────────────────────────────────────────────────────────

def _final_sintetico(tmp_path, dur=2):
    out = tmp_path / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=15:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
                   check=True)
    return str(out)


@pytest.fixture
def s3_falso(mocker, tmp_path):
    """S3 em disco: download serve o final.mp4 sintético; upload captura o mp3 gerado;
    head_size lê o tamanho real do arquivo sintético (guard-rail de disco)."""
    caixa = {"objetos": {}, "uploads": {}}

    def _download(key, dst):
        shutil.copyfile(caixa["objetos"][key], dst)

    def _upload(local, key):
        dst = tmp_path / ("up_" + key.replace("/", "_"))
        shutil.copyfile(local, dst)
        caixa["uploads"][key] = str(dst)

    def _head_size(key):
        return os.path.getsize(caixa["objetos"][key])

    mocker.patch("s3_client.exists", return_value=False)
    mocker.patch("s3_client.download_file", side_effect=_download)
    mocker.patch("s3_client.upload_file", side_effect=_upload)
    mocker.patch("s3_client.head_size", side_effect=_head_size)
    return caixa


def _job_montado(job_id="TEST-audio", final_key=None):
    """Job de podcast já em 'done', com o episódio montado (pré-condição da feature)."""
    final_key = final_key or f"jobs/{job_id}/podcast/final.mp4"
    db.create_podcast_job(job_id, "epis.mp4", f"/tmp/{job_id}.mp4", "t@t", "vtest")
    db.update_job(job_id, status="done", podcast_final_key=final_key)
    return job_id, final_key


# ─── Função pura do comando ────────────────────────────────────────────────────

def test_build_extract_cmd_puro():
    cmd = podcast_audio._build_extract_cmd("in.mp4", "out.mp3")
    assert cmd[:3] == ["ffmpeg", "-y", "-i"]
    assert "-vn" in cmd
    assert cmd[cmd.index("-acodec") + 1] == "libmp3lame"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert cmd[-1] == "out.mp3"
    assert "-threads" not in cmd            # libmp3lame não é multi-thread


# ─── run_podcast_audio_extract (ffmpeg de verdade, S3 mockado) ────────────────

def test_run_podcast_audio_extract_feliz(tmp_path, s3_falso):
    jid, final_key = _job_montado()
    s3_falso["objetos"] = {final_key: _final_sintetico(tmp_path)}
    db.update_job(jid, podcast_audio_status="extraindo")

    podcast_audio.run_podcast_audio_extract(jid)

    job = db.get_job(jid)
    assert job["podcast_audio_status"] == "done", job.get("podcast_audio_error")
    key = job["podcast_audio_key"]
    assert key == f"jobs/{jid}/podcast/audio.mp3"
    assert key in s3_falso["uploads"]
    assert os.path.getsize(s3_falso["uploads"][key]) > 1024
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid, "audio"))


def test_run_podcast_audio_extract_falha_vira_error(s3_falso):
    jid, _ = _job_montado("TEST-audio-err", final_key="jobs/nao-existe/final.mp4")
    db.update_job(jid, podcast_audio_status="extraindo")

    podcast_audio.run_podcast_audio_extract(jid)

    job = db.get_job(jid)
    assert job["podcast_audio_status"] == "error"
    assert job["podcast_audio_error"]
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid, "audio"))


def test_run_podcast_audio_extract_ignora_job_de_clipe():
    db.create_job("TEST-clip-audio", "c.mp4", 30, 90, "/tmp/TEST-clip-audio.mp4")
    podcast_audio.run_podcast_audio_extract("TEST-clip-audio")
    assert db.get_job("TEST-clip-audio")["podcast_audio_status"] is None


# ─── Rotas ─────────────────────────────────────────────────────────────────────

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


def test_audio_route_exige_episodio_montado(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    db.create_podcast_job("TEST-audio-pre", "epis.mp4", "/tmp/x.mp4", "t@t", "vtest")
    db.update_job("TEST-audio-pre", status="gancho_aprovado")
    _login(client, "t@t")
    r = client.post("/api/podcast/TEST-audio-pre/audio")
    assert r.status_code == 409


def test_audio_route_flag_off_404(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", False)
    _login(client, "t@t")
    assert client.post("/api/podcast/qualquer/audio").status_code == 404
    assert client.get("/api/podcast/qualquer/audio").status_code == 404


def test_audio_route_kill_switch_off_por_padrao_503(client, monkeypatch):
    """Kill switch de emergência (2026-07-24): sem setar PODCAST_AUDIO_ENABLED, o POST
    recusa com 503 mesmo com o job pronto pra extrair (depois do gate de PODCAST_ENABLED/
    job existir, antes de qualquer outra checagem de estado)."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid, _ = _job_montado("TEST-audio-kill-switch")
    _login(client, "t@t")
    r = client.post(f"/api/podcast/{jid}/audio")
    assert r.status_code == 503
    assert "desativada" in r.get_json()["error"]


def test_audio_route_admissao_nao_duplica_disparo(client, monkeypatch, mocker):
    """Dois disparos seguidos (corrida) no mesmo job: só a 1ª chamada dispara a
    extração de fato; a 2ª encontra 'extraindo' fresco e devolve 202 sem redisparar."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    extrai = mocker.patch("podcast_audio.run_podcast_audio_extract")
    jid, _ = _job_montado("TEST-audio-race")
    _login(client, "t@t")

    r1 = client.post(f"/api/podcast/{jid}/audio")
    assert r1.status_code == 202 and r1.get_json()["audio_status"] == "extraindo"
    r2 = client.post(f"/api/podcast/{jid}/audio")
    assert r2.status_code == 202 and r2.get_json()["audio_status"] == "extraindo"

    time.sleep(0.05)
    assert extrai.call_count == 1
    assert db.get_job(jid)["podcast_audio_status"] == "extraindo"


def test_audio_route_redispara_apos_staleness(client, monkeypatch, mocker):
    """updated_at antigo (> JOB_STALE_MINUTES) com status 'extraindo' é tratado como
    thread morta (ex.: restart do systemd): a rota redispara em vez de travar pra sempre."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    extrai = mocker.patch("podcast_audio.run_podcast_audio_extract")
    jid, _ = _job_montado("TEST-audio-stale")
    db.update_job(jid, podcast_audio_status="extraindo")
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE job_id = ?", (jid,))
    con.commit()
    con.close()
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/audio")
    assert r.status_code == 202 and r.get_json()["audio_status"] == "extraindo"
    time.sleep(0.05)
    assert extrai.call_count == 1


def test_audio_route_extraindo_recente_nao_redispara(client, monkeypatch, mocker):
    """Contraprova do teste de staleness: 'extraindo' com updated_at FRESCO não
    redispara (thread legitimamente em andamento)."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    extrai = mocker.patch("podcast_audio.run_podcast_audio_extract")
    jid, _ = _job_montado("TEST-audio-fresco")
    db.update_job(jid, podcast_audio_status="extraindo")
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/audio")
    assert r.status_code == 202
    time.sleep(0.05)
    extrai.assert_not_called()


def test_audio_route_cache_hit_200_sem_nova_thread(client, monkeypatch, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    extrai = mocker.patch("podcast_audio.run_podcast_audio_extract")
    jid, _ = _job_montado("TEST-audio-cache")
    db.update_job(jid, podcast_audio_status="done",
                  podcast_audio_key=f"jobs/{jid}/podcast/audio.mp3")
    mocker.patch("s3_client.exists", return_value=True)
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/audio")
    assert r.status_code == 200 and r.get_json()["audio_status"] == "done"
    time.sleep(0.05)
    extrai.assert_not_called()


def test_audio_route_cache_estolado_503_sem_redisparar(client, monkeypatch, mocker):
    """S3 estolado (S3StalledError do watchdog) no HEAD do MP3 já pronto: 503 pedindo pra
    tentar de novo, e NÃO uma extração nova. É o único ponto do app onde um estol não degrada
    pra "não existe": aqui isso custaria baixar + reprocessar + subir um episódio de vários GB
    só porque um HEAD não respondeu."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    extrai = mocker.patch("podcast_audio.run_podcast_audio_extract")
    jid, _ = _job_montado("TEST-audio-estol")
    db.update_job(jid, podcast_audio_status="done",
                  podcast_audio_key=f"jobs/{jid}/podcast/audio.mp3")
    mocker.patch("s3_client.exists",
                 side_effect=s3_client.S3StalledError("head: sem progresso por mais de 60s"))
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/audio")
    assert r.status_code == 503
    assert "instável" in r.get_json()["error"]
    time.sleep(0.05)
    extrai.assert_not_called()
    # E o status no banco segue 'done' (não foi rebaixado pra 'extraindo' por causa do estol).
    assert db.get_job(jid)["podcast_audio_status"] == "done"


def test_audio_route_feliz_idle_extraindo_done(client, monkeypatch, tmp_path, s3_falso, mocker):
    """Caminho feliz completo: idle -> POST dispara 'extraindo' -> a thread real
    (ffmpeg + S3 mockado) termina e o status vira 'done'."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid, final_key = _job_montado("TEST-audio-feliz")
    s3_falso["objetos"] = {final_key: _final_sintetico(tmp_path)}

    _login(client, "t@t")
    r = client.post(f"/api/podcast/{jid}/audio")
    assert r.status_code == 202

    def _terminou():
        return db.get_job(jid)["podcast_audio_status"] in ("done", "error")

    t0 = time.time()
    while time.time() - t0 < 10.0 and not _terminou():
        time.sleep(0.05)
    job = db.get_job(jid)
    assert job["podcast_audio_status"] == "done", job.get("podcast_audio_error")

    # GET /hooks expõe os campos novos
    r = client.get(f"/api/podcast/{jid}/hooks")
    body = r.get_json()
    assert body["audio_status"] == "done" and body["audio_available"] is True

    # GET /audio devolve 302 pra presignada
    mocker.patch("s3_client.get_presigned_url", return_value="https://s3.fake/audio.mp3")
    r = client.get(f"/api/podcast/{jid}/audio")
    assert r.status_code == 302 and r.headers["Location"] == "https://s3.fake/audio.mp3"


def test_audio_download_route_409_antes_de_extrair(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid, _ = _job_montado("TEST-audio-download-409")
    _login(client, "t@t")
    assert client.get(f"/api/podcast/{jid}/audio").status_code == 409


def test_hooks_view_expoe_campos_de_audio_idle():
    """Job recém-montado, sem NENHUMA extração pedida ainda: os campos novos existem
    e refletem o estado inicial (None / False), sem quebrar o contrato antigo."""
    jid, _ = _job_montado("TEST-audio-hooks-idle")
    job = db.get_job(jid)
    assert job.get("podcast_audio_status") is None
    assert not (bool(job.get("podcast_audio_key")) and job.get("podcast_audio_status") == "done")


# ─── Heartbeat (achado 1 do code-review/segurança) ─────────────────────────────

def _envelhecer(job_id: str) -> None:
    """Força updated_at pro passado distante (mesmo padrão dos testes de staleness
    já existentes), simulando uma thread que parou de dar sinal de vida."""
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00' WHERE job_id = ?",
               (job_id,))
    con.commit()
    con.close()


def test_heartbeat_podcast_audio_renova_updated_at():
    jid, _ = _job_montado("TEST-audio-hb-unidade")
    db.update_job(jid, podcast_audio_status="extraindo")
    _envelhecer(jid)
    assert db.get_job(jid)["updated_at"] < "2020-01-01"

    db.heartbeat_podcast_audio(jid)

    assert db.get_job(jid)["updated_at"] > "2020-01-01"


def test_heartbeat_podcast_audio_nao_ressuscita_status_diferente():
    """O heartbeat só vale enquanto podcast_audio_status='extraindo': um job que já
    terminou (ou nunca começou) não tem seu updated_at mexido à toa."""
    jid, _ = _job_montado("TEST-audio-hb-status-errado")
    db.update_job(jid, podcast_audio_status="done")
    _envelhecer(jid)

    db.heartbeat_podcast_audio(jid)

    assert db.get_job(jid)["updated_at"] == "2000-01-01T00:00:00+00:00"


def test_heartbeat_renova_durante_trabalho_real(monkeypatch, mocker, tmp_path, s3_falso):
    """Extração 'demorada' (upload lento): o updated_at é renovado ENQUANTO o trabalho
    ainda está rodando (não só no início/no fim), o que evita o falso-positivo de
    staleness descrito no achado 1 (download/fila mais lentos que JOB_STALE_MINUTES)."""
    monkeypatch.setattr(podcast_audio, "_HEARTBEAT_INTERVAL", 0.05)
    jid, final_key = _job_montado("TEST-audio-hb-real")
    s3_falso["objetos"] = {final_key: _final_sintetico(tmp_path)}
    db.update_job(jid, podcast_audio_status="extraindo")
    _envelhecer(jid)

    visto_durante = {}

    def _upload_lento(local, key):
        time.sleep(0.2)
        visto_durante["updated_at"] = db.get_job(jid)["updated_at"]
        dst = tmp_path / ("up_" + key.replace("/", "_"))
        shutil.copyfile(local, dst)

    mocker.patch("s3_client.upload_file", side_effect=_upload_lento)

    podcast_audio.run_podcast_audio_extract(jid)

    # Já renovado ANTES do fim do trabalho (o upload lento leu isso no meio do sleep).
    assert visto_durante["updated_at"] > "2020-01-01"
    job = db.get_job(jid)
    assert job["podcast_audio_status"] == "done", job.get("podcast_audio_error")


# ─── Auto-cura via GET /hooks (achado 2 do code-review/segurança) ─────────────

def test_hooks_view_auto_cura_extracao_travada(client, monkeypatch):
    """O front nunca re-POSTa enquanto audio_status=='extraindo' (só barra de
    progresso); a auto-cura tem que acontecer na rota de POLLING que ele já chama."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid, _ = _job_montado("TEST-audio-hooks-travado")
    db.update_job(jid, podcast_audio_status="extraindo")
    _envelhecer(jid)
    _login(client, "t@t")

    r = client.get(f"/api/podcast/{jid}/hooks")
    assert r.status_code == 200
    assert r.get_json()["audio_status"] == "error"

    job = db.get_job(jid)
    assert job["podcast_audio_status"] == "error"
    assert job["podcast_audio_error"]


def test_hooks_view_extraindo_fresco_nao_e_auto_curado(client, monkeypatch):
    """Contraprova: 'extraindo' com updated_at FRESCO é trabalho legítimo em
    andamento, o GET não pode marcar como erro."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    jid, _ = _job_montado("TEST-audio-hooks-fresco")
    db.update_job(jid, podcast_audio_status="extraindo")
    _login(client, "t@t")

    r = client.get(f"/api/podcast/{jid}/hooks")
    assert r.get_json()["audio_status"] == "extraindo"
    assert db.get_job(jid)["podcast_audio_status"] == "extraindo"


# ─── Corrida GET/POST na auto-cura (achado 1 da 2ª revisão) ───────────────────

def test_hooks_view_get_nao_sobrescreve_post_concorrente_redisparado(client, monkeypatch, mocker):
    """Corrida determinística: o GET decide 'stale' num snapshot velho (fora do lock),
    mas ANTES de pegar o lock um POST concorrente vence a corrida, adquire o lock,
    também vê stale e redispara (grava 'extraindo' fresco). Sem o double-check da
    releitura DENTRO do lock, o GET sobrescreveria esse status fresco de volta pra
    'error' às cegas, e um 'tentar de novo' subsequente do usuário disputaria o mesmo
    workdir com a extração redisparada pelo POST (ainda rodando)."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_AUDIO_ENABLED", True)
    extrai = mocker.patch("podcast_audio.run_podcast_audio_extract")
    jid, _ = _job_montado("TEST-audio-corrida-get-post")
    db.update_job(jid, podcast_audio_status="extraindo")
    _envelhecer(jid)

    import app as app_module
    real_stale = app_module._podcast_audio_stale
    pre_checagem_feita = threading.Event()
    pode_prosseguir = threading.Event()
    chamadas = {"n": 0}

    def _stale_orquestrado(job):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            # Pré-checagem do GET, ainda FORA do lock: calcula o resultado real (True,
            # snapshot velho) e só então pausa, simulando o intervalo entre "decidiu
            # stale" e "conseguiu entrar no with _podcast_audio_lock(job_id)".
            resultado = real_stale(job)
            pre_checagem_feita.set()
            assert pode_prosseguir.wait(2), "POST concorrente não liberou a tempo"
            return resultado
        return real_stale(job)

    mocker.patch("app._podcast_audio_stale", side_effect=_stale_orquestrado)

    resultado_get = {}

    def _rodar_get():
        with app_module.app.test_client() as c_get:
            _login(c_get, "t@t")   # idem: dois clientes, o MESMO dono
            r = c_get.get(f"/api/podcast/{jid}/hooks")
            resultado_get["status_code"] = r.status_code
            resultado_get["body"] = r.get_json()

    t_get = threading.Thread(target=_rodar_get, daemon=True)
    t_get.start()
    assert pre_checagem_feita.wait(2), "GET não chegou na pré-checagem a tempo"

    # Enquanto o GET está pausado (já decidiu 'stale', ainda não pegou o lock), o POST
    # concorrente entra, também vê stale e redispara: grava 'extraindo' fresco.
    with app_module.app.test_client() as c_post:
        # Os dois clientes logam como o DONO: o gate de dono de 2026-07-27 recusaria
        # outro usuário, e este teste é sobre CORRIDA GET/POST, não sobre autorização.
        _login(c_post, "t@t")
        r_post = c_post.post(f"/api/podcast/{jid}/audio")
    assert r_post.status_code == 202
    assert r_post.get_json()["audio_status"] == "extraindo"
    assert extrai.call_count == 1

    pode_prosseguir.set()
    t_get.join(2)
    assert not t_get.is_alive()

    # O GET não pode ter sobrescrito o 'extraindo' fresco do POST de volta pra 'error'.
    assert resultado_get["status_code"] == 200
    assert resultado_get["body"]["audio_status"] == "extraindo"
    job = db.get_job(jid)
    assert job["podcast_audio_status"] == "extraindo", job.get("podcast_audio_error")


# ─── Guard-rail de disco (achado 3 do code-review/segurança) ──────────────────

def test_extract_aborta_por_disco_insuficiente(mocker, tmp_path, s3_falso):
    jid, final_key = _job_montado("TEST-audio-disco-cheio")
    s3_falso["objetos"] = {final_key: _final_sintetico(tmp_path)}
    db.update_job(jid, podcast_audio_status="extraindo")

    mocker.patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(total=1, used=0, free=1))
    download = mocker.patch("s3_client.download_file")

    podcast_audio.run_podcast_audio_extract(jid)

    job = db.get_job(jid)
    assert job["podcast_audio_status"] == "error"
    assert "disco insuficiente" in job["podcast_audio_error"]
    download.assert_not_called()


# ─── Lock por job, não global (achado 4 do code-review/segurança) ─────────────

def test_podcast_audio_lock_e_por_job_nao_global(client):
    """Dois jobs diferentes não competem pelo mesmo lock de admissão: segurar o lock
    de um job não pode travar a checagem (que inclui uma chamada ao S3) de outro."""
    import app as app_module

    lock_a = app_module._podcast_audio_lock("TEST-lock-job-a")
    lock_b = app_module._podcast_audio_lock("TEST-lock-job-b")
    assert lock_a is not lock_b
    assert app_module._podcast_audio_lock("TEST-lock-job-a") is lock_a  # idempotente

    preso = threading.Event()
    pode_soltar = threading.Event()

    def _segura_a():
        with lock_a:
            preso.set()
            pode_soltar.wait(2)

    t = threading.Thread(target=_segura_a, daemon=True)
    t.start()
    assert preso.wait(1)
    try:
        assert lock_b.acquire(timeout=1)
        lock_b.release()
    finally:
        pode_soltar.set()
        t.join(2)
