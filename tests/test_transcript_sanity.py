"""Sanity check de transcript degenerado (captions.avaliar_transcript + barreira no
/api/callback). Origem: incidente 2026-08-20, Whisper alucinou a MESMA frase em
263/263 segmentos (silêncio longo no início do arquivo) e o pipeline rodou por cima,
entregando 18 clipes falsos como 'done'. Limiares validados contra os 21 transcripts
reais do banco (pior saudável: top_share 11,1%, unique 84,2%, cobertura 72,3%)."""
import time

import pytest

import captions
import config
import db


# ─── Helpers (mesmo formato do SRT real do Whisper) ───────────────────────────

def _ts(s):
    h, m, sec = int(s // 3600), int(s % 3600 // 60), s % 60
    return f"{h:02d}:{m:02d}:{int(sec):02d},{int(round((sec % 1) * 1000)):03d}"


def _srt(cues):
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1))


def _srt_alucinado(n=263, passo=30.0, dur=2.56, texto="Legenda Adriana Zanotto"):
    """Reprodução do padrão do incidente: um cue idêntico por janela de ~30s."""
    return _srt([(i * passo + 25.6, i * passo + 25.6 + dur, texto) for i in range(n)])


def _srt_saudavel(n=120, passo=5.0):
    """Fala contígua e variada, como um episódio real."""
    return _srt([(i * passo, i * passo + passo - 0.5, f"fala número {i} sobre o assunto {i % 7}")
                 for i in range(n)])


def _espera(cond, timeout=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ─── avaliar_transcript: casos do incidente e limiares ────────────────────────

def test_incidente_real_263_cues_identicos_reprova():
    v = captions.avaliar_transcript(_srt_alucinado())
    assert v["degenerado"] is True
    assert "mesmo texto" in v["motivo"]
    assert v["metricas"]["top_share"] == 1.0
    assert v["metricas"]["n_segmentos"] == 263


def test_saudavel_passa():
    v = captions.avaliar_transcript(_srt_saudavel())
    assert v["degenerado"] is False
    assert v["metricas"]["unique_ratio"] > 0.8
    assert v["metricas"]["cobertura_fala"] > 0.7


def test_transcript_vazio_reprova():
    for vazio in ("", None, "srt", "texto sem cue nenhum"):
        v = captions.avaliar_transcript(vazio)
        assert v["degenerado"] is True, f"esperava reprovar: {vazio!r}"
        assert "vazia" in v["motivo"]


def test_amostra_pequena_nunca_reprova():
    # 9 cues idênticos: abaixo de SANITY_MIN_SEGS não avalia (arquivo curto legítimo)
    v = captions.avaliar_transcript(_srt([(i * 5, i * 5 + 4, "mesma frase") for i in range(9)]))
    assert v["degenerado"] is False
    assert "amostra pequena" in v["motivo"]


def test_dez_cues_identicos_reprova_massivo():
    v = captions.avaliar_transcript(_srt([(i * 5, i * 5 + 4, "mesma frase") for i in range(10)]))
    assert v["degenerado"] is True


def test_top_share_40_por_cento_reprova():
    # 12 idênticos + 13 variados = top_share 0.48 (>= 0.40 com n >= 20)
    cues = [(i * 5, i * 5 + 4, "frase repetida") for i in range(12)]
    cues += [(60 + i * 5, 60 + i * 5 + 4, f"fala variada {i}") for i in range(13)]
    v = captions.avaliar_transcript(_srt(cues))
    assert v["degenerado"] is True
    assert v["metricas"]["top_share"] == round(12 / 25, 3)


def test_unique_ratio_baixo_reprova():
    # 30 cues com só 8 textos distintos (unique 0.267 <= 0.30), nenhum dominante
    cues = [(i * 5, i * 5 + 4, f"bordão {i % 8}") for i in range(30)]
    v = captions.avaliar_transcript(_srt(cues))
    assert v["degenerado"] is True
    assert "unicos" in v["motivo"]


def test_cobertura_de_fala_esparsa_reprova():
    # 25 cues variados de 1s a cada 30s: fala cobre ~3% da duração
    cues = [(i * 30.0, i * 30.0 + 1.0, f"ilha de fala {i}") for i in range(25)]
    v = captions.avaliar_transcript(_srt(cues))
    assert v["degenerado"] is True
    assert "cobre" in v["motivo"]


def test_normalizacao_junta_variacoes_triviais():
    # "Então," / "então" / "ENTÃO." são a MESMA alucinação pra contagem
    cues = [(i * 5, i * 5 + 4, ["Então,", "então", "ENTÃO."][i % 3]) for i in range(12)]
    v = captions.avaliar_transcript(_srt(cues))
    assert v["degenerado"] is True
    assert v["metricas"]["unique_ratio"] == round(1 / 12, 3)


def test_pior_caso_saudavel_do_banco_passa_com_folga():
    # Perfil do pior job legítimo observado (Depoimento Pablo): 27 cues, top_share 11%
    cues = [(i * 4, i * 4 + 3.5, "Perfeito.") for i in range(3)]
    cues += [(12 + i * 4, 12 + i * 4 + 3.5, f"resposta longa e única {i}") for i in range(24)]
    v = captions.avaliar_transcript(_srt(cues))
    assert v["degenerado"] is False


# ─── Barreira no /api/callback ────────────────────────────────────────────────

@pytest.fixture
def client():
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_callback_clips_degenerado_vira_error_sem_thread2(client, mocker):
    import os

    t2 = mocker.patch("processor.run_thread2")
    mocker.patch("processor._cleanup_job_s3")  # _fail_job limpa S3; teste não toca rede
    db.create_job("TEST-san1", "v.mp4", 30, 90, "/tmp/TEST-san1.mp4")
    db.update_job("TEST-san1", status="transcribing")
    # simula o vídeo de origem ainda no disco (é onde ele está quando o callback chega)
    job_dir = os.path.join(config.JOB_DIR, "TEST-san1")
    os.makedirs(job_dir, exist_ok=True)
    open(os.path.join(job_dir, "original.mp4"), "wb").write(b"x")

    r = client.post("/api/callback", json={"job_id": "TEST-san1",
                                           "transcript": _srt_alucinado(),
                                           "clips_json": '[{"start": 1}]'})
    assert r.status_code == 200
    assert r.get_json().get("skipped") == "transcript degenerado"
    job = db.get_job("TEST-san1")
    assert job["status"] == "error"
    assert "degenerada" in job["error"]
    # NÃO grava transcript/clips_json nem dispara a thread2 (nada de corte/reframe/S3)
    assert job["transcript"] is None and job["clips_json"] is None
    # erro terminal de clipes limpa o disco como todo _fail_job (senão GBs vazam na VPS)
    assert not os.path.exists(job_dir)
    time.sleep(0.1)
    t2.assert_not_called()
    db.delete_job("TEST-san1")


def test_callback_podcast_degenerado_vira_error_sem_hooks(client, monkeypatch, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    rodou = mocker.patch("hooks.run_podcast_hooks")
    db.create_podcast_job("TEST-san2", "pod.mp4", "/tmp/TEST-san2.mp4", "user@test", "vtest")
    db.update_job("TEST-san2", status="transcribing")

    r = client.post("/api/callback", json={"job_id": "TEST-san2",
                                           "transcript": _srt_alucinado()})
    assert r.status_code == 200 and r.get_json().get("skipped") == "transcript degenerado"
    job = db.get_job("TEST-san2")
    assert job["status"] == "error" and "degenerada" in job["error"]
    assert job["transcript"] is None
    time.sleep(0.1)
    rodou.assert_not_called()
    db.delete_job("TEST-san2")


def test_callback_saudavel_segue_pro_corte(client, mocker):
    t2 = mocker.patch("processor.run_thread2")
    db.create_job("TEST-san3", "v.mp4", 30, 90, "/tmp/TEST-san3.mp4")
    db.update_job("TEST-san3", status="transcribing")

    r = client.post("/api/callback", json={"job_id": "TEST-san3",
                                           "transcript": _srt_saudavel(),
                                           "clips_json": "[]"})
    assert r.status_code == 200 and "skipped" not in r.get_json()
    assert db.get_job("TEST-san3")["status"] == "cutting"
    assert _espera(lambda: t2.called)
    db.delete_job("TEST-san3")


def test_callback_duplicata_em_done_nao_sobrescreve_com_error(client, mocker):
    mocker.patch("processor.run_thread2")
    db.create_job("TEST-san4", "v.mp4", 30, 90, "/tmp/TEST-san4.mp4")
    db.update_job("TEST-san4", status="done")

    r = client.post("/api/callback", json={"job_id": "TEST-san4",
                                           "transcript": _srt_alucinado()})
    assert r.get_json().get("skipped")  # guarda de idempotência vem ANTES da checagem
    assert db.get_job("TEST-san4")["status"] == "done"
    db.delete_job("TEST-san4")


def test_callback_teto_de_corpo_413(client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "_MAX_CALLBACK_BODY", 128)
    r = client.post("/api/callback", data=b"x" * 4096,
                    content_type="application/json")
    assert r.status_code == 413


def test_callback_corpo_nao_dict_vira_400(client):
    r = client.post("/api/callback", json=["uma", "lista"])
    assert r.status_code == 400