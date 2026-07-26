"""Registro EM MEMÓRIA do trabalho que este processo está fazendo agora, para o /health
responder de verdade a pergunta "dá pra reiniciar?".

POR QUE EXISTE: o ritual de deploy é "restart só com active_jobs=0", e esse gate já mentiu
três vezes. Ele lê a tabela `jobs`, e boa parte do trabalho pesado não deixa linha nenhuma
lá: upload em voo (o pior caso, até 75min, e sem linha até TERMINAR), queima de legenda,
thumb, re-render, extração de MP3, revisor de legendas, probe de YouTube. Em 2026-07-26 um
restart matou um upload de 10GB no meio com o /health respondendo active_jobs=0, porque era
verdade: não havia job nenhum, havia um request de 75 minutos.

O banco continua respondendo a metade DURÁVEL da pergunta (db.count_trabalho_em_andamento:
"existe estado esperando trabalho?", sobrevive ao processo). Este módulo responde a metade
EFÊMERA ("este processo está ocupado agora?"), que é a única com IDADE e a única que enxerga
as seis operações que nunca tocam o banco. Os dois números NÃO se somam: cada um vai num
campo do /health e o pode_reiniciar é a conjunção.

ANTÍDOTO, e é o coração do desenho: toda entrada carrega o instante em que começou. Uma
operação que vaze (não decremente no finally) travaria o deploy PARA SEMPRE, que é a mesma
armadilha que a trava do delete de projeto teve e que precisou do reap pra não ser
permanente. Então uma entrada mais velha que o teto da CATEGORIA dela vira SUSPEITA: sai da
conta do pode_reiniciar e aparece numa lista à parte. Um humano lendo "upload em voo há 6
horas, provavelmente vazou" resolve; um gate travado sem explicação, não.

LIMITAÇÃO CONHECIDA E ACEITA: isto é memória de UM processo. Não coordena entre workers do
gunicorn. Hoje a unit roda com `--workers 1` e está correto; no dia em que subir o número de
workers, cada um enxergará só o próprio trabalho e o gate volta a mentir. Não resolver agora
foi decisão consciente (a alternativa seria estado compartilhado, que é desproporcional).

REGRA DE OURO PRA QUEM MEXER: a trava deste módulo é a MAIS INTERNA do app. É esperado e
correto chamar entrar()/sair() já segurando um semáforo ou um lock por clipe (é assim que os
11 pontos instrumentados funcionam); o proibido é o contrário: nunca adquira outro lock, nem
faça I/O, nem chame código do projeto (log, formatação) COM ela na mão. É isso que garante
que ela não participa de ciclo de deadlock.

E ela NUNCA fica segurada durante a operação registrada: o registrar() pega a trava, solta,
roda a queima de 180s sem trava nenhuma, e pega de novo pra sair. Se alguém implementar como
"trava segurada durante o bloco", o primeiro /health durante uma queima pendura tudo.
"""
import itertools
import threading
import time

import config

# id -> (categoria, t0 monotônico, rótulo pro log, já_avisado)
_REG: dict = {}
_TRAVA = threading.Lock()
_SEQ = itertools.count(1)

# Categoria -> bloqueia o deploy? É ao mesmo tempo o vocabulário válido e a política.
# Critério: bloqueia quem, se morrer agora, causa dano que o sistema NÃO conserta sozinho
# (perde trabalho humano ou dinheiro já gasto, deixa sujeira que nada ceifa, ou o cliente não
# re-dispara). Quem não passa em nenhum dos três é best-effort por design e só informa.
BLOQUEIA_DEPLOY = {
    "upload":   True,   # 75min do usuário, sem retomada. Foi o incidente que motivou tudo.
    "rerender": True,   # encode de minutos, key .rerender.mp4 órfã no S3, custo de GPU.
    "audio":    True,   # extração de MP3; só se cura pelo stale de 30min.
    "revisor":  True,   # dinheiro já gasto na API da Anthropic, e nada ceifa caption_reviews.
    "burn":     False,  # cache idempotente, teto de 180s, a rota já cai pra base sem legenda.
    "midia":    False,  # thumb/waveform/filmstrip: fila cheia já devolve 404 / peaks:[] / null.
    "ytprobe":  False,  # segundos, e o acquire non-blocking já devolve 429.
}


def entrar(categoria: str, rotulo: str = "") -> int:
    """Marca uma operação como em voo e devolve o token que sair() consome.

    Categoria fora do vocabulário é tratada como BLOQUEANTE (fail-safe): quem adicionar uma
    operação nova é obrigado a decidir a política em BLOQUEIA_DEPLOY, e enquanto não decidir
    o comportamento seguro é segurar o deploy, não liberar."""
    tok = next(_SEQ)
    with _TRAVA:
        _REG[tok] = (categoria, time.monotonic(), rotulo, False)
    return tok


def sair(token: int) -> None:
    """Tira a operação do registro. Idempotente: token desconhecido (ou já retirado) é no-op,
    porque um sair() duplicado nunca pode derrubar o processo."""
    with _TRAVA:
        _REG.pop(token, None)


class registrar:
    """Context manager pros pontos que já são `with`. Nos que usam acquire(timeout=...) com
    degradação no `if not`, use entrar/sair nas linhas que já têm o acquire/release: envolver
    aqueles num CM obrigaria a converter cinco `if` legíveis em cinco try/except e reindentar
    o fallback que segura o site de pé."""

    def __init__(self, categoria: str, rotulo: str = ""):
        self._cat, self._rot, self._tok = categoria, rotulo, None

    def __enter__(self):
        self._tok = entrar(self._cat, self._rot)
        return self

    def __exit__(self, *_exc):
        sair(self._tok)
        return False


# Cache dos tetos pra não ler config com a trava na mão no caminho de descarte.
_TETOS_CACHE = {}


def _teto(categoria: str) -> float:
    v = config.EM_VOO_SUSPEITO_S.get(categoria, config.EM_VOO_SUSPEITO_PADRAO_S)
    _TETOS_CACHE[categoria] = v
    return v


def snapshot() -> dict:
    """Fotografia pro /health. Devolve SÓ categoria, contagem e idade: esta rota é PÚBLICA
    (nginx serve /health sem auth) e o docstring dela promete "sem segredos", então job_id,
    clip_id e nome de arquivo ficam de fora e vão só pro log.

    Uma entrada mais velha que o teto da categoria dela sai de `em_voo` e entra em
    `suspeitos`: não conta pro pode_reiniciar. É o antídoto contra vazamento virar trava
    eterna. O teto é POR CATEGORIA de propósito: teto único erraria dos dois lados, deixando
    um upload legítimo de 10GB em internet ruim virar suspeito (e alguém reiniciar em cima
    dele, repetindo o incidente) enquanto um thumb parado há 3h seguiria bloqueando."""
    agora = time.monotonic()
    with _TRAVA:
        # Descarta o que passou MUITO do teto (4x): o antídoto tira o vazamento do gate, mas
        # sem isto a entrada fica no dict pra sempre e o /health paga por ela em toda batida.
        # Hoje nada vaza por requisição (o teardown cobre o upload e os outros 10 estão em
        # try/finally), então é rede de segurança, não caminho quente.
        for tok in [t for t, (c, t0, _r, _a) in _REG.items()
                    if agora - t0 > 4 * _TETOS_CACHE.get(c, 900)]:
            _REG.pop(tok, None)
        itens = [(cat, agora - t0, rot, avisado) for (cat, t0, rot, avisado) in _REG.values()]

    em_voo, suspeitos, bloqueia = {}, {}, False
    for cat, idade, _rot, _av in itens:
        alvo = suspeitos if idade > _teto(cat) else em_voo
        d = alvo.setdefault(cat, {"n": 0, "mais_antigo_s": 0})
        d["n"] += 1
        d["mais_antigo_s"] = max(d["mais_antigo_s"], int(idade))
        if alvo is em_voo and BLOQUEIA_DEPLOY.get(cat, True):
            bloqueia = True
    return {"em_voo": em_voo, "suspeitos": suspeitos, "bloqueia_deploy": bloqueia}


def novos_suspeitos() -> list:
    """Suspeitos que ainda não foram logados, já marcando como avisados. Existe pra o
    /health poder logar UMA vez por vazamento: sem isto, a monitoração batendo de 30 em 30s
    escreveria um WARNING a cada 30s durante as horas todas do vazamento.

    Devolve tuplas (categoria, idade_s, rótulo). O rótulo só aparece AQUI, no log, nunca na
    resposta HTTP."""
    agora = time.monotonic()
    saida = []
    # Tetos lidos ANTES de pegar a trava: leitura de config não pode acontecer com ela na mão
    # (ver a regra de ouro no topo). O snapshot() já fazia assim.
    tetos = {c: _teto(c) for c in set(BLOQUEIA_DEPLOY) | {"_"}}
    with _TRAVA:
        for tok, (cat, t0, rot, avisado) in list(_REG.items()):
            idade = agora - t0
            if idade > tetos.get(cat, config.EM_VOO_SUSPEITO_PADRAO_S) and not avisado:
                _REG[tok] = (cat, t0, rot, True)
                saida.append((cat, int(idade), rot))
    return saida
