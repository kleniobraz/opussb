"""Parte 3 do corte de início do corpo bruto (§39 Parte 2): funções puras de probing de
keyframe (com GOP CONTROLADO via sintéticos), o motor do corte ponta a ponta, os estados
novos ('cortando_inicio') e a rota. Espelha a estrutura de tests/test_podcast_compose.py
(mesmos padrões de fixture/sintético/cliente).

Cobre também a regressão do bug achado escrevendo esta suíte: o read_intervals do ffprobe
com fim RELATIVO ("T%+N") soma N a partir de onde o ffprobe fez seek (o keyframe anterior
mais próximo de T), não a partir de T; com um keyframe distante antes de T, a janela lida
ficava deslocada e perdia um keyframe real dentro de [T, T+N). Corrigido pra fim ABSOLUTO
("T%T+N") + filtro explícito do limite inferior em _keyframes_no_intervalo."""
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
import podcast_cut


# ─── Sintéticos com GOP controlado ─────────────────────────────────────────────

def _mp4(path, *, size="320x180", rate=30, dur=10, audio=True, gop=99999, force_kf=None):
    """Vídeo sintético com GOP controlado. Sem force_kf: keyframe fixo a cada `gop` frames
    (gop=99999 ~= só o keyframe inicial, pra simular um trecho/arquivo esparso). Com
    force_kf (lista de timestamps): keyframes SÓ nesses pontos (gop gigante desliga o
    periódico). sc_threshold=0 desliga a detecção de corte de cena do x264, senão o
    testsrc2 pode inserir keyframe extra e quebrar o controle determinístico."""
    inputs = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}"]
    if audio:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                   "-c:a", "aac", "-ar", "48000", "-ac", "2"]
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-sc_threshold", "0", "-g", str(gop)]
    if force_kf:
        cmd += ["-force_key_frames", ",".join(str(t) for t in force_kf)]
    else:
        cmd += ["-keyint_min", str(gop)]
    cmd += [str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


def _denso(tmp_path, nome, dur=10, gop=30, rate=30):
    """Keyframe a cada `gop` frames (1s a 30fps por padrão): o caso comum medido na
    Carbone (GOP fixo de 1s)."""
    return _mp4(tmp_path / nome, dur=dur, rate=rate, gop=gop)


def _esparso(tmp_path, nome, dur=10, rate=30):
    """Só o keyframe inicial (GOP maior que a duração inteira): nenhum keyframe perto de
    onde o produtor for cortar, em lugar nenhum do arquivo."""
    return _mp4(tmp_path / nome, dur=dur, rate=rate, gop=99999)


def _esparso_ate_denso(tmp_path, nome, dur, denso_a_partir, rate=30):
    """Um único keyframe em 0 até `denso_a_partir` (GOP enorme; o keyframe anterior a
    qualquer ponto ali dentro fica LONGE), e um keyframe por segundo dali até o fim. É o
    sintético que reproduz o cenário do bug do read_intervals relativo: o ffprobe faz seek
    pro keyframe de 0 (distante) e, sem fim absoluto, perderia os keyframes reais que caem
    dentro da janela pretendida no trecho denso."""
    forcados = list(range(int(denso_a_partir), int(dur)))
    return _mp4(tmp_path / nome, dur=dur, rate=rate, force_kf=forcados)


def _espera(cond, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _job_pronto_para_corte(tmp_path, job_id, dono="t@t", dur=10, gop=30):
    """Job de podcast 'uploaded' com corpo sintético DENTRO do JOB_DIR (mesmo padrão do
    _job_pronto de test_podcast_compose.py, adaptado: corte não usa vinheta nem gancho)."""
    job_dir = os.path.join(config.JOB_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    corpo = _denso(tmp_path, f"{job_id}_corpo.mp4", dur=dur, gop=gop)
    src = os.path.join(job_dir, "original.mp4")
    shutil.copyfile(corpo, src)
    db.create_podcast_job(job_id, "epis.mp4", src, dono, "vtest")
    return src


# ─── find_next_keyframe (probing puro, GOP controlado) ────────────────────────

def test_find_next_keyframe_acha_o_certo_com_gop_denso(tmp_path):
    v = _denso(tmp_path, "denso.mp4", dur=10, gop=30)  # keyframes em 0,1,2,...,9
    assert podcast_cut.find_next_keyframe(v, 2.3, 5.0) == pytest.approx(3.0, abs=0.02)


def test_find_next_keyframe_margem_nao_empata_no_proprio_ponto(tmp_path):
    v = _denso(tmp_path, "denso_margem.mp4", dur=10, gop=30)
    # cortando EXATAMENTE num keyframe (3.0), o resultado tem que ser o PRÓXIMO (4.0), não
    # ele mesmo: a margem de 1ms garante que o corte sempre ANDA pra frente.
    assert podcast_cut.find_next_keyframe(v, 3.0, 5.0) == pytest.approx(4.0, abs=0.02)


def test_find_next_keyframe_none_sem_keyframe_no_lookahead(tmp_path):
    v = _esparso(tmp_path, "esparso.mp4", dur=10)  # só o keyframe de 0
    assert podcast_cut.find_next_keyframe(v, 5.0, 2.0) is None


def test_find_next_keyframe_nao_perde_keyframe_distante_do_seek_anterior(tmp_path):
    """Regressão do bug do read_intervals relativo ('T%+N'): com o único keyframe anterior
    LONGE de `t` (aqui, em 0), o ffprobe faz seek pra ele; o fim do read_intervals tem que
    ser ABSOLUTO (t+lookahead), senão a janela lida fica deslocada pra trás e perde o
    keyframe real que está dentro de [t, t+lookahead)."""
    v = _esparso_ate_denso(tmp_path, "sparso_denso.mp4", dur=16, denso_a_partir=8)
    # único keyframe antes de 6 é o de 0 (delta de 6s); o real está em 8, dentro de [6,10)
    assert podcast_cut.find_next_keyframe(v, 6.0, 4.0) == pytest.approx(8.0, abs=0.02)
    # janela [2,6) genuinamente não alcança o 8.0: None é o resultado CERTO aqui
    assert podcast_cut.find_next_keyframe(v, 2.0, 4.0) is None


# ─── keyframe_density (probing puro, GOP controlado) ──────────────────────────

def test_keyframe_density_media_correta_em_janela_conhecida(tmp_path):
    v = _denso(tmp_path, "denso_dens.mp4", dur=10, gop=30)  # keyframes 0..9
    dens = podcast_cut.keyframe_density(v, 2.0, 3.0)  # janela [2,5]: kfs 2,3,4,5
    assert dens == pytest.approx(1.0, abs=0.02)


def test_keyframe_density_none_com_menos_de_2_keyframes(tmp_path):
    v = _esparso(tmp_path, "esparso_dens.mp4", dur=10)
    assert podcast_cut.keyframe_density(v, 3.0, 2.0) is None  # janela sem keyframe nenhum


def test_keyframe_density_nao_conta_keyframe_de_fora_da_janela(tmp_path):
    """Efeito colateral do fim absoluto (fix do bug): o ffprobe devolve pacotes a partir do
    keyframe ANTERIOR a `inicio` (que pode ficar fora da janela pretendida); a função tem
    que filtrar isso, não confiar cru no que o ffprobe manda de volta."""
    v = _esparso_ate_denso(tmp_path, "sparso_denso_dens.mp4", dur=16, denso_a_partir=8)
    dens = podcast_cut.keyframe_density(v, 6.0, 4.0)  # janela [6,10): só 8 e 9 contam
    assert dens == pytest.approx(1.0, abs=0.02)


# ─── mensagem_sem_keyframe / _diagnostico_arquivo_todo (os dois ramos) ────────

def test_mensagem_localizada_gop_esparso_so_perto_do_ponto(tmp_path, monkeypatch):
    """GOP esparso só no trecho do corte; o meio do arquivo tem GOP normal -> mensagem
    aponta pro ponto marcado, não pro arquivo inteiro. Sintético de propósito no MESMO
    formato do bug do read_intervals: o único keyframe antes da amostra do meio fica longe
    (só há um em 0, o trecho denso só começa depois); prova que o diagnóstico escolhe o
    lado certo mesmo quando a amostra do meio também está longe do seek anterior."""
    monkeypatch.setattr(podcast_cut, "_DIAG_SAMPLE_WINDOW", 6.0)
    dur = 40.0
    v = _esparso_ate_denso(tmp_path, "loc.mp4", dur=dur, denso_a_partir=20)
    assert podcast_cut.find_next_keyframe(v, 5.0, 3.0) is None
    assert podcast_cut._diagnostico_arquivo_todo(v, dur, 3.0) is False
    msg = podcast_cut.mensagem_sem_keyframe(v, 5.0, 3.0, dur)
    assert "ponto marcado" in msg
    assert "episódio específico" not in msg


def test_mensagem_arquivo_inteiro_gop_largo_em_qualquer_lugar(tmp_path, monkeypatch):
    """GOP largo (só o keyframe inicial) o arquivo inteiro: a amostra do meio também
    acusa o problema -> mensagem aponta pro arquivo inteiro, não só pro ponto marcado."""
    monkeypatch.setattr(podcast_cut, "_DIAG_SAMPLE_WINDOW", 6.0)
    dur = 40.0
    v = _esparso(tmp_path, "geral.mp4", dur=dur)
    assert podcast_cut.find_next_keyframe(v, 5.0, 3.0) is None
    assert podcast_cut._diagnostico_arquivo_todo(v, dur, 3.0) is True
    msg = podcast_cut.mensagem_sem_keyframe(v, 5.0, 3.0, dur)
    assert "episódio específico" in msg


def test_diagnostico_menos_de_2_keyframes_conta_como_arquivo_inteiro(tmp_path, monkeypatch):
    """Ajuste de revisão: amostra do meio com menos de 2 keyframes já é evidência de GOP
    largo (não 'sem sinal'). Isolado da densidade calculada de fato (teste anterior):
    aqui a janela de diagnóstico não pega keyframe NENHUM."""
    monkeypatch.setattr(podcast_cut, "_DIAG_SAMPLE_WINDOW", 4.0)
    dur = 20.0
    v = _esparso(tmp_path, "unico_kf.mp4", dur=dur)
    assert podcast_cut.keyframe_density(v, 8.0, 4.0) is None  # janela [8,12): nenhum kf
    assert podcast_cut._diagnostico_arquivo_todo(v, dur, 3.0) is True


# ─── _cortar_inicio / run_podcast_cut_intro ponta a ponta ─────────────────────

def test_cortar_inicio_caminho_feliz(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-feliz"
    src = _job_pronto_para_corte(tmp_path, jid, dur=10, gop=30)
    total_antes = podcast_compose.duration(src)

    podcast_cut.run_podcast_cut_intro(jid, 2.3)

    job = db.get_job(jid)
    assert job["status"] == "uploaded", job.get("error")
    assert job["intro_cut_at"] == pytest.approx(2.3)
    assert job["local_path"] != src
    assert os.path.exists(job["local_path"])
    assert not os.path.exists(src)  # fonte pré-corte apagado só depois do ponteiro trocado

    dur_final = podcast_compose.duration(job["local_path"])
    assert abs(dur_final - (total_antes - 2.3)) < 0.3  # mesma tolerância apertada do módulo
    assert not os.path.exists(os.path.join(config.JOB_DIR, jid, "cut_intro"))  # workdir limpo


def test_cortar_inicio_guarda_sobra_minima(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 5.0)
    jid = "TEST-cut-sobra"
    src = _job_pronto_para_corte(tmp_path, jid, dur=10, gop=30)

    podcast_cut.run_podcast_cut_intro(jid, 9.5)  # sobra só 0.5s, menor que o mínimo de 5s

    job = db.get_job(jid)
    assert job["status"] == "error"
    assert "sobra insuficiente" in job["error"]
    assert job["local_path"] == src and os.path.exists(src)  # fonte intacto
    # gravado ANTES de qualquer validação, de propósito (mesmo padrão do hook_choice)
    assert job["intro_cut_at"] == pytest.approx(9.5)


def test_cortar_inicio_guarda_disco(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    monkeypatch.setattr(config, "PODCAST_CUT_DISK_FACTOR", 10 ** 9)  # exige o impossível
    jid = "TEST-cut-disco"
    src = _job_pronto_para_corte(tmp_path, jid, dur=10, gop=30)

    podcast_cut.run_podcast_cut_intro(jid, 2.0)

    job = db.get_job(jid)
    assert job["status"] == "error" and "disco insuficiente" in job["error"]
    assert job["local_path"] == src and os.path.exists(src)


def test_cortar_inicio_falha_sanidade_duracao(tmp_path, monkeypatch, mocker):
    """Sanidade da emenda apertada (0.3s): mocka só a duração do FINAL pra simular uma
    emenda que não bate, sem mexer na duração real do fonte (usada nos outros cálculos)."""
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-sanidade"
    src = _job_pronto_para_corte(tmp_path, jid, dur=10, gop=30)

    duration_real = podcast_compose.duration

    def duration_falsa(path):
        d = duration_real(path)
        if path and os.path.basename(path) == "final.mp4":
            return (d or 0) + 5.0  # bem fora da tolerância de 0.3s
        return d

    mocker.patch("podcast_compose.duration", side_effect=duration_falsa)

    podcast_cut.run_podcast_cut_intro(jid, 2.0)

    job = db.get_job(jid)
    assert job["status"] == "error"
    assert "não bate com a esperada" in job["error"]
    assert job["local_path"] == src and os.path.exists(src)


def test_cortar_inicio_erro_keyframe_nao_encontrado(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    monkeypatch.setattr(config, "PODCAST_CUT_KEYFRAME_LOOKAHEAD", 2.0)
    jid = "TEST-cut-sem-kf"
    job_dir = os.path.join(config.JOB_DIR, jid)
    os.makedirs(job_dir, exist_ok=True)
    corpo = _esparso(tmp_path, "esparso_e2e.mp4", dur=10)
    src = os.path.join(job_dir, "original.mp4")
    shutil.copyfile(corpo, src)
    db.create_podcast_job(jid, "epis.mp4", src, "t@t", "vtest")

    podcast_cut.run_podcast_cut_intro(jid, 5.0)  # janela [5,7): sem keyframe (só há em 0)

    job = db.get_job(jid)
    assert job["status"] == "error"
    assert "episódio específico" in job["error"]  # arquivo inteiro: só tem 1 kf, em lugar nenhum
    assert job["local_path"] == src and os.path.exists(src)


# ─── Estados: cortando_inicio é ATIVO (os dois lados) ─────────────────────────

def test_cortando_inicio_conta_na_fila_e_e_ceifavel():
    db.create_podcast_job("TEST-cut-fila", "p.mp4", "/tmp/TEST-cut-fila.mp4", "t@t", "v1")
    db.update_job("TEST-cut-fila", status="cortando_inicio")
    assert db.count_active_jobs() == 1

    con = sqlite3.connect(os.environ["DB_PATH"])
    con.execute("UPDATE jobs SET updated_at = '2000-01-01T00:00:00+00:00'")
    con.commit()
    con.close()
    assert db.reap_stale_jobs(30) == 1
    assert db.get_job("TEST-cut-fila")["status"] == "error"


# ─── Rota ───────────────────────────────────────────────────────────────────

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
        con = sqlite3.connect(os.environ["DB_PATH"])
        con.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
        con.commit()
        con.close()
    r = client.post("/api/login", json={"email": email, "password": "senha-de-teste"})
    assert r.status_code == 200, r.get_json()


def test_rota_404_com_flag_desligada(client, monkeypatch):
    monkeypatch.setattr(config, "PODCAST_ENABLED", False)
    _login(client, "t@t")
    r = client.post("/api/podcast/qualquer/cut-intro", json={"cut_at": 1.0})
    assert r.status_code == 404


def test_rota_400_cut_at_invalido(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-rota-validacao"
    _job_pronto_para_corte(tmp_path, jid)
    _login(client, "t@t")

    for payload in ({}, {"cut_at": "abc"}, {"cut_at": -1}, {"cut_at": True}, {"cut_at": False}):
        r = client.post(f"/api/podcast/{jid}/cut-intro", json=payload)
        assert r.status_code == 400, (payload, r.get_json())


def test_rota_409_local_path_sumiu(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = "TEST-cut-rota-sumiu"
    src = _job_pronto_para_corte(tmp_path, jid)
    os.remove(src)
    _login(client, "t@t")
    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 1.0})
    assert r.status_code == 409


def test_rota_400_sobra_insuficiente(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 300)
    jid = "TEST-cut-rota-sobra"
    _job_pronto_para_corte(tmp_path, jid, dur=10)
    _login(client, "t@t")
    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 5.0})
    assert r.status_code == 400
    assert "300" in r.get_json()["error"]


def test_rota_409_status_errado(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-rota-status"
    _job_pronto_para_corte(tmp_path, jid)
    db.update_job(jid, status="transcribing")
    _login(client, "t@t")
    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 1.0})
    assert r.status_code == 409


def test_rota_409_corte_ja_feito(client, monkeypatch, tmp_path):
    """Corte-sobre-corte (bug real de produção, job Thiago Medeiros): com intro_cut_at
    JÁ gravado e status 'uploaded' (um corte anterior teve sucesso, é esse par de valores
    que _cortar_inicio grava junto no sucesso), a rota recusa um novo corte em vez de
    rodar a thread de novo em cima do arquivo já cortado."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-ja-feito"
    _job_pronto_para_corte(tmp_path, jid)
    db.update_job(jid, intro_cut_at=1.0)  # simula corte anterior bem-sucedido (status segue 'uploaded')
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 2.0})
    assert r.status_code == 409
    job = db.get_job(jid)
    assert job["status"] == "uploaded"
    assert job["intro_cut_at"] == 1.0  # não foi sobrescrito por uma tentativa nova


def test_corte_falho_retenta(client, monkeypatch, tmp_path):
    """Com intro_cut_at gravado, status error aceita corte de novo (202, thread real
    até 'uploaded') e limpa o erro anterior."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-retry"
    _job_pronto_para_corte(tmp_path, jid, dur=10, gop=30)
    db.update_job(jid, status="error", error="corte de início falhou: teste", intro_cut_at=1.0)
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 2.0})
    assert r.status_code == 202, r.get_json()
    assert db.get_job(jid)["error"] is None

    assert _espera(lambda: db.get_job(jid)["status"] in ("uploaded", "error"))
    job = db.get_job(jid)
    assert job["status"] == "uploaded", job.get("error")


def test_corte_sem_ponto_rejeita(client, monkeypatch, tmp_path):
    """Sem intro_cut_at gravado, /cut-intro devolve 409 e não trata como retry."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-error-sem-intro"
    _job_pronto_para_corte(tmp_path, jid)
    db.update_job(jid, status="error", error="falhou em outra etapa do pipeline")
    _login(client, "t@t")
    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 1.0})
    assert r.status_code == 409


def test_rota_403_nao_e_dono_nem_admin(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    jid = "TEST-cut-rota-dono"
    _job_pronto_para_corte(tmp_path, jid, dono="dono@test")
    _login(client, "outro@test")
    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 1.0})
    assert r.status_code == 403


def test_rota_429_fila_cheia(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-rota-fila"
    _job_pronto_para_corte(tmp_path, jid)
    db.create_job("TEST-cut-outro-job", "c.mp4", 30, 90, "/tmp/TEST-cut-outro-job.mp4")
    db.update_job("TEST-cut-outro-job", status="cutting")  # ocupa a única vaga
    _login(client, "t@t")
    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 1.0})
    assert r.status_code == 429


def test_rota_feliz_e_impede_disparo_duplo(client, monkeypatch, tmp_path, mocker):
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-rota-feliz"
    _job_pronto_para_corte(tmp_path, jid)
    mocker.patch("podcast_cut.run_podcast_cut_intro")  # não deixa a thread real rodar
    _login(client, "t@t")

    r1 = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 1.0})
    assert r1.status_code == 202
    assert r1.get_json()["status"] == "cortando_inicio"
    assert db.get_job(jid)["status"] == "cortando_inicio"

    # disparo duplo (aqui, sequencial e rápido): o job já não está mais em 'uploaded',
    # a re-checagem DENTRO do lock tem que recusar
    r2 = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 1.0})
    assert r2.status_code == 409


def test_rota_feliz_corte_de_verdade_ate_uploaded(client, monkeypatch, tmp_path):
    """Sem mock: sobe a thread real (mesmo padrão de test_compose_route_feliz_e_final)."""
    monkeypatch.setattr(config, "PODCAST_ENABLED", True)
    monkeypatch.setattr(config, "PODCAST_CUT_MIN_REMAINING_SECONDS", 1.0)
    jid = "TEST-cut-rota-real"
    src = _job_pronto_para_corte(tmp_path, jid, dur=10, gop=30)
    _login(client, "t@t")

    r = client.post(f"/api/podcast/{jid}/cut-intro", json={"cut_at": 2.0})
    assert r.status_code == 202
    assert _espera(lambda: db.get_job(jid)["status"] in ("uploaded", "error"))

    job = db.get_job(jid)
    assert job["status"] == "uploaded", job.get("error")
    assert job["local_path"] != src
    assert os.path.exists(job["local_path"])
    assert not os.path.exists(src)
