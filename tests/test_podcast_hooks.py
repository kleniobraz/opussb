"""Parte 3 do pipeline de podcast (§39): disparo da transcrição bifurcada + hooks.py.
API do Claude SEMPRE mockada (custo zero); o foco de regressão é o caminho de clipes
(callback e admissão intactos) e a contagem de ativos com os estados novos."""
import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest
from werkzeug.security import generate_password_hash

import config
import db
import hooks


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ts(s):
    h, m, sec = int(s // 3600), int(s % 3600 // 60), s % 60
    return f"{h:02d}:{m:02d}:{int(sec):02d},{int(round((sec % 1) * 1000)):03d}"


def _srt(cues):
    """[(start, end, texto)] -> SRT no formato real do Whisper."""
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1))


def _srt_longo(total=600, passo=5):
    """Episódio sintético: uma fala a cada `passo` segundos até `total`."""
    return _srt([(i, i + passo - 0.5, f"fala número {i} do episódio de teste")
                 for i in range(0, total, passo)])


def _resp(hooks_list, inp=1000, out=200, model="claude-haiku-4-5"):
    """Resposta do anthropic_client.call com usage no formato do SDK."""
    return {"text": "", "data": {"hooks": hooks_list},
            "usage": SimpleNamespace(input_tokens=inp, output_tokens=out), "model": model}


def _espera(cond, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def client():
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _login(client, email, admin=False):
    db.upsert_user(email, generate_password_hash("senha-de-teste"), must_change=False)
    if admin:
        import sqlite3
        con = sqlite3.connect(os.environ["DB_PATH"])
        con.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
        con.commit()
        con.close()
    r = client.post("/api/login", json={"email": email, "password": "senha-de-teste"})
    assert r.status_code == 200, r.get_json()


def _podcast_job(job_id="TEST-pod", user="user@test", status="uploaded", local_path=None):
    db.create_podcast_job(job_id, "pod.mp4", local_path or f"/tmp/{job_id}.mp4", user, "vtest")
    if status != "uploaded":
        db.update_job(job_id, status=status)
    return job_id


# ─── hooks.py: compactação e validação ────────────────────────────────────────

def test_compact_transcript_blocos_30s():
    compacto = hooks.compact_transcript(_srt_longo(total=70, passo=5))
    linhas = compacto.split("\n")
    assert linhas[0].startswith("[00:00] ")
    assert "fala número 0" in linhas[0] and "fala número 25" in linhas[0]
    assert len(linhas) >= 2 and linhas[1].startswith("[00:")


def test_valida_criterio_duro():
    srt = _srt_longo(total=600)
    cues = hooks.captions.parse_srt(srt)
    brutos = [
        {"start": 10, "end": 55, "text": "x", "reason": "boa abertura"},    # válido (45s)
        {"start": 10, "end": 30, "text": "x", "reason": "curto"},           # < 30s
        {"start": 100, "end": 200, "text": "x", "reason": "longo"},         # > 60s
        {"start": 550, "end": 700, "text": "x", "reason": "estoura"},       # fora do episódio
        {"start": 12, "end": 50, "text": "x", "reason": "sobrepõe"},        # overlap > 50%
        {"start": "abc", "end": 55, "text": "x", "reason": "ruim"},         # não numérico
    ]
    validos, rejeicoes = hooks._valida(brutos, srt, cues, cues[-1]["end"], [])
    assert len(validos) == 1
    v = validos[0]
    assert 30 <= v["end"] - v["start"] <= 60
    # o texto persistido vem do SRT, não do modelo
    assert "fala número" in v["text"] and v["text"] != "x"
    assert len(rejeicoes) == 5
    motivos = " | ".join(m for _, m in rejeicoes)
    assert "30 a 60" in motivos and "duração do episódio" in motivos and "sobrepõe" in motivos


# ─── hooks.py: chamada + retry ────────────────────────────────────────────────

def test_suggest_hooks_feliz_uma_chamada(mocker):
    call = mocker.patch("anthropic_client.call", return_value=_resp([
        {"start": 10, "end": 55, "text": "a", "reason": "r1"},
        {"start": 200, "end": 250, "text": "b", "reason": "r2"},
        {"start": 400, "end": 455, "text": "c", "reason": "r3"},
    ]))
    validos, meta = hooks.suggest_hooks(_srt_longo())
    assert len(validos) == 3 and call.call_count == 1
    assert meta["usage"]["input_tokens"] == 1000
    assert meta["cost_usd"] == pytest.approx((1000 * 1.0 + 200 * 5.0) / 1_000_000)
    assert [v["reason"] for v in validos] == ["r1", "r2", "r3"]


def test_suggest_hooks_retry_corretiva_e_merge(mocker):
    call = mocker.patch("anthropic_client.call", side_effect=[
        _resp([{"start": 10, "end": 55, "text": "a", "reason": "ok"},
               {"start": 100, "end": 200, "text": "b", "reason": "longo demais"},
               {"start": 12, "end": 50, "text": "c", "reason": "sobrepõe"}]),
        _resp([{"start": 200, "end": 250, "text": "d", "reason": "novo1"},
               {"start": 400, "end": 455, "text": "e", "reason": "novo2"}]),
    ])
    validos, meta = hooks.suggest_hooks(_srt_longo())
    assert call.call_count == 2 and len(validos) == 3
    # a 2ª chamada recebe a correção com os motivos da rejeição
    corpo_retry = call.call_args_list[1][0][2][0]["text"]
    assert "rejeitadas" in corpo_retry.lower() and "30 a 60" in corpo_retry
    assert meta["usage"]["input_tokens"] == 2000  # somado das 2 chamadas


def test_suggest_hooks_zero_validos_apos_retry(mocker):
    call = mocker.patch("anthropic_client.call",
                        return_value=_resp([{"start": 1, "end": 20, "text": "x", "reason": "curto"}]))
    validos, meta = hooks.suggest_hooks(_srt_longo())
    assert validos == [] and call.call_count == 2  # teto duro de 2 chamadas


def test_run_podcast_hooks_grava_e_estaciona(mocker):
    mocker.patch("anthropic_client.call", return_value=_resp([
        {"start": 10, "end": 55, "text": "a", "reason": "r1"},
        {"start": 200, "end": 250, "text": "b", "reason": "r2"},
        {"start": 400, "end": 455, "text": "c", "reason": "r3"},
    ]))
    _podcast_job(status="analisando_gancho")
    db.update_job("TEST-pod", transcript=_srt_longo())

    hooks.run_podcast_hooks("TEST-pod")
    job = db.get_job("TEST-pod")
    assert job["status"] == "aguardando_aprovacao"
    dados = json.loads(job["hooks_json"])
    assert len(dados["hooks"]) == 3 and dados["cost_usd"] > 0
    assert dados["model"].startswith("claude-haiku")


def test_run_podcast_hooks_falha_vira_error(mocker):
    mocker.patch("anthropic_client.call", side_effect=RuntimeError("api fora do ar"))
    _podcast_job(status="analisando_gancho")
    db.update_job("TEST-pod", transcript=_srt_longo())

    hooks.run_podcast_hooks("TEST-pod")
    job = db.get_job("TEST-pod")
    assert job["status"] == "error" and "gancho" in job["error"]


# ─── Endpoint /transcribe ─────────────────────────────────────────────────────

def test_transcribe_gates(client, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    t1 = mocker.patch("processor.run_thread1")
    _login(client, "user@test")
    arquivo = tmp_path / "pod.mp4"
    arquivo.write_bytes(b"x" * 2048)

    # job de outro dono -> 403
    _podcast_job("TEST-alheio", user="outro@test", local_path=str(arquivo))
    assert client.post("/api/podcast/TEST-alheio/transcribe").status_code == 403
    # não-podcast -> 400
    db.create_job("TEST-clip", "c.mp4", 30, 90, str(arquivo), "user@test")
    db.update_job("TEST-clip", status="done")
    assert client.post("/api/podcast/TEST-clip/transcribe").status_code == 400
    # inexistente -> 404
    assert client.post("/api/podcast/nao-existe/transcribe").status_code == 404
    # arquivo sumiu -> 409
    _podcast_job("TEST-sem-arquivo", local_path="/tmp/nao-existe-TEST.mp4")
    assert client.post("/api/podcast/TEST-sem-arquivo/transcribe").status_code == 409
    t1.assert_not_called()


def test_transcribe_feliz_e_fila(client, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    t1 = mocker.patch("processor.run_thread1")
    _login(client, "user@test")
    arquivo = tmp_path / "pod.mp4"
    arquivo.write_bytes(b"x" * 2048)
    _podcast_job(local_path=str(arquivo))

    r = client.post("/api/podcast/TEST-pod/transcribe")
    assert r.status_code == 202 and r.get_json()["status"] == "queued"
    assert db.get_job("TEST-pod")["status"] == "queued"
    assert db.count_active_jobs() == 1
    assert _espera(lambda: t1.called)

    # segundo disparo do mesmo job -> 409 (não está mais em 'uploaded')
    assert client.post("/api/podcast/TEST-pod/transcribe").status_code == 409

    # outro podcast com a fila ocupada -> 429 (mesma admissão dos clipes)
    _podcast_job("TEST-pod2", local_path=str(arquivo))
    assert client.post("/api/podcast/TEST-pod2/transcribe").status_code == 429


def test_transcribe_flag_off_404(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", False)
    _login(client, "user@test")
    assert client.post("/api/podcast/qualquer/transcribe").status_code == 404


# ─── Callback bifurcado ───────────────────────────────────────────────────────

def test_callback_podcast_dispara_hooks_e_ignora_clips_json(client, monkeypatch, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    rodou = mocker.patch("hooks.run_podcast_hooks")
    _podcast_job(status="transcribing")

    r = client.post("/api/callback", json={"job_id": "TEST-pod",
                                           "transcript": _srt_longo(),
                                           "clips_json": '[{"start": 1}]'})
    assert r.status_code == 200
    job = db.get_job("TEST-pod")
    assert job["status"] == "analisando_gancho"
    assert job["transcript"] and job["clips_json"] is None
    assert _espera(lambda: rodou.called)

    # duplicata do N8N: já saiu de 'transcribing' -> skip, sem nova thread
    rodou.reset_mock()
    r = client.post("/api/callback", json={"job_id": "TEST-pod", "transcript": "x"})
    assert "skipped" in r.get_json()
    time.sleep(0.1)
    rodou.assert_not_called()


def test_callback_clipe_segue_intacto(client, mocker):
    t2 = mocker.patch("processor.run_thread2")
    db.create_job("TEST-clip", "c.mp4", 30, 90, "/tmp/TEST-c.mp4")
    db.update_job("TEST-clip", status="transcribing")

    r = client.post("/api/callback", json={"job_id": "TEST-clip",
                                           "transcript": "srt", "clips_json": "[]"})
    assert r.status_code == 200
    job = db.get_job("TEST-clip")
    assert job["status"] == "cutting" and job["clips_json"] == "[]"
    assert _espera(lambda: t2.called)


# ─── run_thread1: payload do N8N ──────────────────────────────────────────────

def _video_com_audio(tmp_path):
    out = str(tmp_path / "src.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out], check=True)
    return out


def _roda_thread1(job_id, tmp_path, mocker):
    import processor
    os.makedirs(os.path.join(config.JOB_DIR, job_id), exist_ok=True)
    mocker.patch("processor._s3_upload_with_retry")
    mocker.patch("s3_client.get_presigned_url", return_value="https://s3/audio.mp3")
    post = mocker.patch("processor.requests.post",
                        return_value=SimpleNamespace(raise_for_status=lambda: None))
    processor.run_thread1(job_id)
    return post.call_args.kwargs["json"]


def test_thread1_payload_podcast_usa_defaults(tmp_path, mocker):
    _podcast_job(local_path=_video_com_audio(tmp_path))
    payload = _roda_thread1("TEST-pod", tmp_path, mocker)
    assert payload["min_duration"] == 60 and payload["max_duration"] == 180
    assert db.get_job("TEST-pod")["status"] == "transcribing"


def test_thread1_payload_clipe_mantem_valores(tmp_path, mocker):
    db.create_job("TEST-clip", "c.mp4", 30, 90, _video_com_audio(tmp_path))
    payload = _roda_thread1("TEST-clip", tmp_path, mocker)
    assert payload["min_duration"] == 30 and payload["max_duration"] == 90


# ─── Contagem e reaper com os estados novos ───────────────────────────────────

def test_aguardando_aprovacao_fora_da_contagem_e_do_reaper():
    _podcast_job(status="aguardando_aprovacao")
    assert db.count_active_jobs() == 0

    db.update_job("TEST-pod", status="analisando_gancho")
    assert db.count_active_jobs() == 1  # trabalho real conta

    import sqlite3
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00'")
    con.commit()
    con.close()
    assert db.reap_stale_jobs(30) == 1  # analisando_gancho preso É ceifado

    db.update_job("TEST-pod", status="aguardando_aprovacao")
    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00'")
    con.commit()
    con.close()
    assert db.reap_stale_jobs(30) == 0  # estacionado NÃO é ceifado
    assert db.get_job("TEST-pod")["status"] == "aguardando_aprovacao"
