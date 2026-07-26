"""em_voo.py + o gate pode_reiniciar do /health.

O gate de deploy já mentiu TRÊS vezes (extração de MP3, upload em voo, e mais seis coisas que
nunca tocam o banco). Na terceira custou um upload de 10GB e 75 minutos. Estes testes travam
as duas propriedades que fazem o conserto valer: o gate enxerga o que está em voo, E um
vazamento não trava o deploy pra sempre.
"""
import time

import pytest

from werkzeug.security import generate_password_hash

import config
import db
import em_voo


@pytest.fixture(autouse=True)
def registro_limpo():
    """Cada teste começa com o registro vazio: é estado de módulo, vaza entre testes."""
    with em_voo._TRAVA:
        em_voo._REG.clear()
    yield
    with em_voo._TRAVA:
        em_voo._REG.clear()


@pytest.fixture
def client():
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _login(client, email="dono@test"):
    db.upsert_user(email, generate_password_hash("senha-de-teste"), must_change=False)
    r = client.post("/api/login", json={"email": email, "password": "senha-de-teste"})
    assert r.status_code == 200, r.get_json()


# ─── o registro em si ─────────────────────────────────────────────────────────

def test_entrar_e_sair_sao_simetricos():
    tok = em_voo.entrar("upload", "job-x")
    assert em_voo.snapshot()["em_voo"]["upload"]["n"] == 1
    em_voo.sair(tok)
    assert em_voo.snapshot()["em_voo"] == {}


def test_sair_duplicado_nao_explode():
    """sair() duas vezes (ou com token inventado) nunca pode derrubar o processo: ele roda em
    finally, e um erro ali mascararia a exceção original."""
    tok = em_voo.entrar("burn", "x")
    em_voo.sair(tok)
    em_voo.sair(tok)
    em_voo.sair(999999)


def test_categoria_bloqueante_x_informativa():
    """Nem tudo que roda deve segurar deploy: queima é cache idempotente com fallback, upload
    é 75min do usuário sem retomada."""
    t = em_voo.entrar("burn", "x")
    assert em_voo.snapshot()["bloqueia_deploy"] is False
    em_voo.sair(t)
    t = em_voo.entrar("upload", "x")
    assert em_voo.snapshot()["bloqueia_deploy"] is True


def test_categoria_desconhecida_bloqueia_por_seguranca():
    """Fail-safe: quem adicionar operação nova e esquecer de decidir a política segura o
    deploy (e descobre), em vez de liberar silenciosamente."""
    em_voo.entrar("categoria_que_ninguem_declarou", "x")
    assert em_voo.snapshot()["bloqueia_deploy"] is True


# ─── o antídoto: vazamento vira suspeito, não trava eterna ───────────────────

def test_vazamento_vira_suspeito_e_LIBERA_o_deploy(monkeypatch):
    """O coração do desenho. Uma operação que vaze (nunca chame sair) travaria o deploy pra
    sempre. Passado o teto da categoria, ela sai da conta e vira alerta pra um humano."""
    monkeypatch.setitem(config.EM_VOO_SUSPEITO_S, "upload", 0.2)
    em_voo.entrar("upload", "job-vazado")          # de propósito: nunca sai

    assert em_voo.snapshot()["bloqueia_deploy"] is True      # ainda fresco: bloqueia
    time.sleep(0.3)
    s = em_voo.snapshot()
    assert s["bloqueia_deploy"] is False, "vazamento travaria o deploy pra sempre"
    assert s["em_voo"] == {}
    assert s["suspeitos"]["upload"]["n"] == 1


def test_teto_e_POR_CATEGORIA(monkeypatch):
    """Teto único erra dos dois lados: upload legítimo de 10GB em internet ruim viraria
    suspeito (e alguém reiniciaria em cima), enquanto thumb parado há horas seguiria
    bloqueando. Aqui: mesma idade, veredito diferente."""
    monkeypatch.setitem(config.EM_VOO_SUSPEITO_S, "upload", 100)
    monkeypatch.setitem(config.EM_VOO_SUSPEITO_S, "midia", 0.2)
    em_voo.entrar("upload", "legitimo")
    em_voo.entrar("midia", "vazado")
    time.sleep(0.3)

    s = em_voo.snapshot()
    assert "upload" in s["em_voo"], "upload legítimo não pode virar suspeito cedo"
    assert "midia" in s["suspeitos"], "mídia parada muito além do teto é vazamento"
    assert s["bloqueia_deploy"] is True   # o upload legítimo ainda segura, e deve segurar


def test_suspeito_e_avisado_uma_vez_so(monkeypatch):
    """A monitoração bate no /health de 30 em 30s. Sem marcar como avisado, o journal levaria
    um WARNING a cada batida durante as horas todas do vazamento."""
    monkeypatch.setitem(config.EM_VOO_SUSPEITO_S, "burn", 0.1)
    em_voo.entrar("burn", "x")
    time.sleep(0.2)
    assert len(em_voo.novos_suspeitos()) == 1
    assert em_voo.novos_suspeitos() == []


def test_snapshot_nao_segura_a_trava_durante_a_operacao():
    """A trava é folha e curta: se ficasse segurada durante o trabalho registrado, o primeiro
    /health durante uma queima de 180s penduraria a monitoração."""
    import threading
    with em_voo.registrar("burn", "x"):
        # Numa thread com join(timeout): se a regressão for "trava segurada durante o bloco",
        # o snapshot BLOQUEIA pra sempre. Medindo tempo no fluxo principal, a suíte inteira
        # penduraria (não há pytest-timeout) em vez de reportar falha legível.
        ok = threading.Event()
        threading.Thread(target=lambda: (em_voo.snapshot(), ok.set()), daemon=True).start()
        assert ok.wait(timeout=2), "snapshot() bloqueou: a trava ficou segurada durante o bloco"


# ─── o gate de deploy (rota INTERNA) e o /health publico ─────────────────────

def test_gate_expoe_pode_reiniciar_e_nao_vaza_identificador(client):
    """O detalhe vive em /api/internal/deploy_gate (nginx 404a de fora). Nem lá o rótulo
    aparece: job_id, clip_id e caminho ficam só no log."""
    em_voo.entrar("upload", "/api/podcast/upload job-secreto-123")
    r = client.get("/api/internal/deploy_gate")

    assert r.status_code == 200
    d = r.get_json()
    assert d["pode_reiniciar"] is False
    assert d["em_voo"]["upload"]["n"] == 1
    assert "job-secreto-123" not in r.get_data(as_text=True)


def test_health_PUBLICO_nao_expoe_telemetria(client):
    """/health é a única rota pública junto do /login. Publicar ali quais operações rodam,
    quantas e há quanto tempo entregaria a um anônimo o perfil operacional da empresa
    (horário de upload, duração, quando o sistema fica ocioso, quando o deploy vem)."""
    em_voo.entrar("upload", "x")
    d = client.get("/health").get_json()

    assert d["status"] == "ok"
    assert "em_voo" not in d and "suspeitos" not in d and "pode_reiniciar" not in d
    assert set(d) == {"status", "db", "active_jobs"}   # contrato antigo, preservado


def test_gate_true_com_tudo_parado(client):
    d = client.get("/api/internal/deploy_gate").get_json()
    assert d["pode_reiniciar"] is True
    assert d["em_voo"] == {} and d["suspeitos"] == {}


def test_gate_nao_mistura_as_duas_metades(client):
    """Banco e memória em campos SEPARADOS: o active_jobs não pode absorver o em_voo, senão
    some a distinção entre estado durável e trabalho deste processo."""
    em_voo.entrar("upload", "x")
    d = client.get("/api/internal/deploy_gate").get_json()

    assert d["active_jobs"] == 0, "o em_voo vazou pra dentro do contador do banco"
    assert d["em_voo"]["upload"]["n"] == 1
    assert d["pode_reiniciar"] is False


# ─── o upload, que foi o incidente ───────────────────────────────────────────

def test_upload_grande_marca_em_voo(client, monkeypatch):
    """POST com corpo acima do teto é marcado ANTES da view rodar: é lá que moram os 75min."""
    monkeypatch.setattr(config, "EM_VOO_UPLOAD_MIN_BYTES", 1024)
    visto = {}

    import app as app_module

    # A rota tem que ser registrada ANTES do primeiro request: o Flask bloqueia setup depois
    # que o app começa a servir, e o _login abaixo já é um request.
    @app_module.app.route("/__espia_em_voo", methods=["POST"])
    def _espia():
        visto["snapshot"] = em_voo.snapshot()
        return {"ok": True}

    _login(client)

    client.post("/__espia_em_voo", data=b"x" * 4096,
                content_type="application/octet-stream")
    assert visto["snapshot"]["em_voo"].get("upload", {}).get("n") == 1, visto
    assert em_voo.snapshot()["em_voo"] == {}, "teardown não liberou"


def test_multipart_SEM_content_length_tambem_marca(client, monkeypatch):
    """Requisição chunked não manda Content-Length. Sem esta regra, um cliente assim passaria
    despercebido e o gate voltaria a mentir, que é o problema inteiro."""
    monkeypatch.setattr(config, "EM_VOO_UPLOAD_MIN_BYTES", 10**12)   # teto inalcançável
    visto = {}

    import app as app_module

    @app_module.app.route("/__espia_chunked", methods=["POST"])
    def _espia2():
        visto["snapshot"] = em_voo.snapshot()
        return {"ok": True}

    _login(client)

    env = {"Transfer-Encoding": "chunked"}
    client.post("/__espia_chunked", data=b"x" * 32,
                content_type="multipart/form-data; boundary=xyz", headers=env)
    assert visto["snapshot"]["em_voo"].get("upload", {}).get("n") == 1, visto


def test_requisicao_pequena_nao_marca(client, monkeypatch):
    """JSON comum não pode entupir o registro. Observado DURANTE a request: medir depois
    passaria mesmo se o hook marcasse indevidamente, porque o teardown já teria limpado."""
    monkeypatch.setattr(config, "EM_VOO_UPLOAD_MIN_BYTES", 32 * 1024 * 1024)
    visto = {}

    import app as app_module

    @app_module.app.route("/__espia_pequena", methods=["POST"])
    def _espia3():
        visto["snapshot"] = em_voo.snapshot()
        return {"ok": True}

    _login(client)
    client.post("/__espia_pequena", json={"campo": "valor curto"})
    assert visto["snapshot"]["em_voo"] == {}, visto


def test_anonimo_NAO_consegue_prender_o_gate(client, monkeypatch):
    """O before_request é hook GLOBAL e roda antes do login_required. Sem exigir sessão,
    qualquer anônimo na internet prenderia pode_reiniciar em false abrindo um POST com
    Content-Length grande e gotejando bytes, e o time ficaria sem conseguir deployar
    (inclusive correção de segurança) por causa de um estranho."""
    monkeypatch.setattr(config, "EM_VOO_UPLOAD_MIN_BYTES", 1024)

    client.post("/api/podcast/upload", data=b"x" * 4096,
                content_type="multipart/form-data; boundary=xyz")   # SEM login

    assert em_voo.snapshot()["em_voo"] == {}, "anônimo marcou trabalho em voo"
    assert client.get("/api/internal/deploy_gate").get_json()["pode_reiniciar"] is True
