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
import podcast_compose


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
    dur = podcast_compose._duration(final)
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
