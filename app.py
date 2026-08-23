import os
import sys
import re
import math
import array
import time
import unicodedata
import uuid
import json
import base64
import sqlite3
import secrets
import logging
import ipaddress
import functools
import threading
import subprocess
import tempfile
import shutil
import urllib.parse
from datetime import datetime, timezone, timedelta

import requests
from botocore.exceptions import ClientError
from flask import Flask, request, jsonify, abort, session, redirect, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException

import config
import db
import em_voo
import s3_client
import processor
import captions
import caption_reviewer
import hooks
import podcast_compose
import podcast_cut
import podcast_ingest
import podcast_audio

# Logging pra stderr: print() no worker do gunicorn fica com buffer e some do
# journalctl. Um handler próprio no logger "opussb" (sem propagar) garante log
# imediato, com nível e timestamp, pros módulos (app/processor/s3).
_opussb_logger = logging.getLogger("opussb")
if not _opussb_logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    _opussb_logger.addHandler(_h)
    _opussb_logger.setLevel(logging.INFO)
    _opussb_logger.propagate = False
log = logging.getLogger("opussb.app")

app = Flask(__name__, static_folder="static", static_url_path="")
app.secret_key = config.FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # SameSite=Lax (não Strict) de propósito: o cookie precisa viajar no retorno
    # top-level do OAuth do Zoho (accounts.zoho.com -> nosso /callback) p/ validar o state.
    SESSION_COOKIE_SAMESITE="Lax",
    # HTTPS ativo (Let's Encrypt em sbclips.space) -> cookie só por TLS.
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=config.SESSION_DAYS),
    # Teto de upload. Secundário ao Nginx (client_max_body_size 15G), protege
    # o caminho direto :5001. 16 GiB fica acima do Nginx pra ele ser o 1o gate.
    MAX_CONTENT_LENGTH=16 * 1024 * 1024 * 1024,
)

# Serializa a admissão de upload (check de vaga + reserva). Gunicorn roda 1 worker
# com 4 threads, então um lock de processo fecha a race de uploads simultâneos.
_upload_lock = threading.Lock()

# Serializa a admissão de revisões de legenda (check de vaga + reserva). Lane PRÓPRIA,
# separada do _upload_lock: uma revisão nunca disputa o slot da geração de clipes.
_review_lock = threading.Lock()

# Serializa a admissão da extração de áudio do podcast (check de status + reserva). Lane
# PRÓPRIA, separada do _upload_lock: extrair o MP3 de um episódio já pronto não disputa
# a vaga com a geração de clipes nem com a montagem do podcast. Lock POR JOB (não global,
# mesmo padrão de _clip_burn_lock): o ramo de cache-hit dentro do lock faz uma chamada de
# rede ao S3 (s3_client.exists), e um lock global faria a admissão de um job esperar à
# toa atrás da checagem de outro job sem relação nenhuma.
_podcast_audio_locks = {}
_podcast_audio_locks_guard = threading.Lock()


def _podcast_audio_lock(job_id: str) -> threading.Lock:
    with _podcast_audio_locks_guard:
        lk = _podcast_audio_locks.get(job_id)
        if lk is None:
            lk = threading.Lock()
            _podcast_audio_locks[job_id] = lk
        return lk


# Limita probes simultâneos do yt-dlp (cada um é um subprocess de até 45s). Com 1 worker /
# 4 threads, sem isto um usuário poderia prender as 4 threads em probes e travar TODAS as
# rotas (login, status, /health). 2 deixa metade das threads livres pro resto do app.
_yt_probe_sem = threading.BoundedSemaphore(2)

db.init_db()
db.init_users()
db.init_favorites()
db.init_preferences()
db.init_clip_captions()
db.init_clip_music()
db.init_sessions()
db.init_end_screens()
db.init_vinhetas()
db.init_glossary()
db.init_caption_reviews()

# Semeia o glossário do revisor de legendas (nomes canônicos de marcas/produtos/pessoas). A partir
# daqui a fonte de verdade é a tabela glossary (o usuário adiciona/edita pelo sistema); ensure só
# semeia se o termo ainda não existir. id = slug ascii estável do termo (idempotente no boot).
_GLOSSARY_SEED = [
    ("ONEVO Energia", "marca"), ("Carbone Educação", "marca"), ("Weevo", "marca"),
    ("SeuBoné", "marca"), ("SeuBoné Personalizados", "produto"), ("Carbone Academy", "produto"),
    ("Carbone Club", "produto"), ("Carbone Class", "produto"), ("Academy Class", "produto"),
    ("Club Class", "produto"), ("MasterClass", "produto"), ("Igor Freire", "pessoa"),
    ("Raí Firmo", "pessoa"), ("Tuanne Mariz", "pessoa"),
]
for _term, _cat in _GLOSSARY_SEED:
    try:
        _slug = re.sub(r"[^a-z0-9]+", "-",
                       unicodedata.normalize("NFKD", _term).encode("ascii", "ignore").decode().lower()).strip("-")
        db.ensure_glossary(_slug, _term, _cat)
    except Exception as e:
        log.warning("falha ao semear termo do glossário %s (%s)", _term, e)

# Semeia a biblioteca de telas finais (config.END_SCREENS) no DB se ainda nao existir. A partir
# daqui a fonte de verdade e o DB (o usuario sobe/troca/exclui pelo sistema); o config so semeia.
for _es_id, _es in config.END_SCREENS.items():
    try:
        db.ensure_end_screen(_es_id, _es["label"], _es["key"], _es.get("duration"))
    except Exception as e:
        log.warning("falha ao semear tela final %s (%s)", _es_id, e)

# Lifecycle do 16:9 retido (crop por keyframe, BACKEND §33): expira o prefixo dedicado em
# SOURCE16X9_TTL_DAYS. Gated por SOURCE16X9_LIFECYCLE_ENSURE (default off) pra NAO mutar a config do
# bucket sem decisao; idempotente (so PUTa quando a regra falta/difere). Best-effort: nao trava o boot.
if config.SOURCE16X9_LIFECYCLE_ENSURE:
    def _ensure_lifecycle_bg():
        try:
            # rule_id namespaceado por ambiente: staging e prod dividem o MESMO bucket; um id comum faria
            # cada boot sobrescrever a regra do outro (o merge por id derruba a regra alheia). Sufixo do
            # S3_KEY_PREFIX (prod = "prod") mantem as duas regras coexistindo no bucket.
            _lc_env = config.S3_KEY_PREFIX.strip("/").replace("/", "-") or "prod"
            s3_client.ensure_lifecycle_rule(config.SOURCE16X9_PREFIX, config.SOURCE16X9_TTL_DAYS,
                                            f"opussb-source16x9-ttl-{_lc_env}")
        except Exception as e:
            log.warning("[lifecycle] falha ao garantir a regra do 16:9 retido: %s", e)

    # Em thread de fundo, NAO no boot sincrono: em 2026-07-24 essa chamada pendurou o worker do
    # gunicorn por 6min20s entre "Booting worker" e a primeira request (site fora do ar em TODO
    # restart de deploy que pegasse o S3 ruim). O s3_client agora tem teto proprio (_api), mas
    # housekeeping idempotente e best-effort nao tem por que estar no caminho critico do boot:
    # o app sobe e serve na hora, e a regra e garantida em paralelo.
    threading.Thread(target=_ensure_lifecycle_bg, daemon=True,
                     name="lifecycle-16x9").start()

# Semeia admins iniciais (config ADMIN_EMAILS) como is_admin + active. Idempotente:
# cria a linha se não existir (sem senha utilizável; ele entra via Zoho) ou só promove.
for _admin_email in config.ADMIN_EMAILS:
    try:
        db.ensure_admin(_admin_email)
        log.info("admin semeado: %s", _admin_email)
    except Exception as e:
        log.warning("falha ao semear admin %s (%s)", _admin_email, e)

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MIN_PASSWORD_LEN = 8

# Limites sãos pra duração de clipe pedida no upload. min < max já era exigido;
# aqui evitamos valores absurdos (0, negativo, horas) que o scoring não trata bem.
_MIN_CLIP_SECONDS = 1
_MAX_CLIP_SECONDS = 3600


def _allowed(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


# ─── Trabalho em voo: o gate de deploy (ver em_voo.py) ───────────────────────

@app.before_request
def _marca_upload_em_voo():
    """Marca requests de corpo grande como UPLOAD em voo, pro /health saber que não dá pra
    reiniciar. É o item que motivou o em_voo.py: um upload não cria linha em `jobs` até
    TERMINAR (o de podcast só em app.py, bem depois do file.save e do probe de CFR), então
    o gate antigo via active_jobs=0 e liberava o restart em cima de 75 minutos de trabalho.

    Aqui e não na view: o nginx tem proxy_request_buffering off no bloco /api, então o corpo
    chega em streaming e a thread do gunicorn fica ocupada desde o primeiro byte; a view só
    roda quando o upload JÁ acabou. Marcando no before_request, uma entrada só cobre o corpo
    inteiro, o file.save e o probe.

    Por TAMANHO e não por lista de rotas: já são seis rotas multipart e uma lista dessas
    apodrece na sétima. Multipart SEM Content-Length (chunked) também entra, independente de
    tamanho: sem isso um cliente que não mande o header passaria despercebido e o gate
    voltaria a mentir, que é o problema inteiro."""
    if request.method not in ("POST", "PUT"):
        return None
    # SÓ com sessão. O before_request é hook GLOBAL e roda antes do login_required, então sem
    # esta linha qualquer anônimo na internet prende o pode_reiniciar em false: bastaria abrir
    # um POST anunciando Content-Length grande e gotejar bytes (o nginx tem
    # proxy_request_buffering off no bloco /api, então a request chega ao Flask na hora). O
    # time ficaria impedido de subir correção, inclusive de segurança, por causa de um
    # estranho. Todo upload real é autenticado, então não se perde nada.
    if not session.get("sid"):
        return None
    tam = request.content_length or 0
    multipart_sem_tamanho = (not request.content_length
                             and "multipart/form-data" in (request.content_type or ""))
    if tam >= config.EM_VOO_UPLOAD_MIN_BYTES or multipart_sem_tamanho:
        request.environ["opussb.em_voo_token"] = em_voo.entrar(
            "upload", f"{request.path} {tam or 'chunked'}")
    return None


@app.teardown_request
def _libera_upload_em_voo(exc):
    """Contrapartida do _marca_upload_em_voo. teardown_request e NÃO after_request de
    propósito: o after não roda quando a view levanta, e ler corpo truncado levanta. Um F5 no
    meio do upload (que é como o dono cancela hoje) vazaria o registro exatamente no caso que
    o antídoto de idade existe pra cobrir. O teardown roda sempre, com ou sem exceção."""
    tok = request.environ.pop("opussb.em_voo_token", None) if request else None
    if tok is not None:
        em_voo.sair(tok)
        if exc is not None:
            log.info("[em_voo] upload em %s encerrado por exceção (%s): cliente abortou?",
                     request.path, type(exc).__name__)


# ─── Cabeçalhos de segurança + erros em JSON ─────────────────────────────────

@app.after_request
def _security_headers(resp):
    """Defesa em profundidade barata. O app é interno e atrás de login; estes
    cabeçalhos evitam sniffing de MIME e clickjacking sem custo."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


@app.errorhandler(HTTPException)
def _json_http_error(e):
    """Rotas /api respondem erro em JSON (o front sempre espera JSON). Fora de /api
    mantém o comportamento padrão do Flask (HTML), pra não mexer no SPA estático."""
    if request.path.startswith("/api/"):
        return jsonify({"error": e.description, "status": e.code}), e.code
    return e


@app.errorhandler(Exception)
def _json_unhandled_error(e):
    if isinstance(e, HTTPException):
        return e
    log.exception("erro não tratado em %s", request.path)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Erro interno.", "status": 500}), 500
    raise e


# ─── Autenticação ────────────────────────────────────────────────────────────

# Throttle de login em memória (sem dependência externa): trava tentativas repetidas
# por (IP, email) numa janela curta, freando força bruta. Reseta no restart e libera
# sozinho ao expirar a janela; o teto é folgado pra não atrapalhar quem só errou a senha.
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_MAX_FAILS = 10
_login_fails: dict = {}
_login_fails_lock = threading.Lock()


def _login_key() -> str:
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    return f"{request.remote_addr}|{email}"


def _login_blocked(key: str) -> bool:
    now = time.time()
    with _login_fails_lock:
        hits = [t for t in _login_fails.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
        _login_fails[key] = hits
        return len(hits) >= _LOGIN_MAX_FAILS


def _login_record_failure(key: str) -> None:
    now = time.time()
    with _login_fails_lock:
        hits = [t for t in _login_fails.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
        hits.append(now)
        _login_fails[key] = hits
        # Poda oportunista pra o dicionário não crescer sem limite.
        if len(_login_fails) > 4096:
            for k in [k for k, v in _login_fails.items() if not v]:
                _login_fails.pop(k, None)


def _login_clear(key: str) -> None:
    with _login_fails_lock:
        _login_fails.pop(key, None)


# ─── Sessões server-side (página de Segurança) ────────────────────────────────
# O cookie assinado guarda session["sid"]; a linha em `sessions` é a fonte de verdade.
# login_required valida o sid no banco a CADA request, então DELETE/revoke_all cortam
# o acesso no request seguinte. O IP guardado é só pra exibição (best-effort).
_MAX_UA_LEN = 400               # corta user-agent gigante antes de gravar
_SESSION_TOUCH_THROTTLE = 60    # s: no máximo 1 escrita de last_active por sessão/minuto


def _client_ip():
    """IP do cliente pra EXIBIR na lista de sessões. Best-effort e influenciado pelo
    cliente: NUNCA usar pra auth/throttle. Só confiamos no X-Forwarded-For quando o peer
    imediato é o loopback do Nginx; o Nginx ($proxy_add_x_forwarded_for) anexa o IP que
    observou como ÚLTIMO elemento, então pegamos o último hop. Loopback/bridge viram None
    (não faz sentido mostrar IP interno)."""
    peer = request.remote_addr
    if peer in ("127.0.0.1", "::1"):
        xff = request.headers.get("X-Forwarded-For", "")
        cand = xff.split(",")[-1].strip() if xff else ""
        ip = cand or peer
    else:
        ip = peer
    try:
        ip_obj = ipaddress.ip_address(ip)
    except (ValueError, TypeError):
        return None
    # Loopback e a subnet da bridge Docker não dizem nada pro usuário: viram None
    # (o front omite a linha de location quando vem vazia). _DOCKER_BRIDGE definido
    # mais abaixo no módulo (disponível no momento da chamada).
    if ip_obj.is_loopback or ip_obj in _DOCKER_BRIDGE:
        return None
    return ip


def _parse_ua(ua):
    """(browser, os) a partir do user-agent. Total (nunca levanta). Ordem importa:
    Edge/Opera/Samsung mandam 'Chrome' no UA, e o Chrome manda 'Safari', então o que
    se disfarça vem antes. browser cai em 'Navegador' (igual ao parseUA do front) e os
    em None (o front omite a parte do SO quando vazia)."""
    u = (ua or "").lower()
    if "edg/" in u or "edga/" in u or "edgios/" in u:
        browser = "Edge"
    elif "opr/" in u or "opera" in u:
        browser = "Opera"
    elif "samsungbrowser" in u:
        browser = "Samsung Internet"
    elif "firefox/" in u or "fxios/" in u:
        browser = "Firefox"
    elif "chrome/" in u or "crios/" in u or "chromium" in u:
        browser = "Chrome"
    elif "safari/" in u and "version/" in u:
        browser = "Safari"
    else:
        browser = "Navegador"
    if "windows" in u:
        os_name = "Windows"
    elif "iphone" in u or "ipad" in u or "ipod" in u:
        os_name = "iOS"
    elif "mac os x" in u or "macintosh" in u:
        os_name = "macOS"
    elif "android" in u:
        os_name = "Android"
    elif "cros" in u:
        os_name = "ChromeOS"
    elif "linux" in u:
        os_name = "Linux"
    else:
        os_name = None
    return browser, os_name


def _humanize_pt(iso_ts):
    """String pronta de last_active, espelhando o ui.timeAgo do front (mesmas faixas:
    agora / há N min / há N h / há N d até 30 dias / depois a data). Tolerante a
    None/lixo (devolve None). Compara aware-com-aware (last_active é UTC isoformat)."""
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 0:
        secs = 0
    if secs < 60:
        return "agora"
    if secs < 3600:
        return f"há {int(secs // 60)} min"
    if secs < 86400:
        return f"há {int(secs // 3600)} h"
    if secs < 2592000:                       # 30 dias, igual ao ui.timeAgo
        return f"há {int(secs // 86400)} d"
    return dt.astimezone(timezone.utc).strftime("%d/%m/%Y")


def _maybe_touch_session(sid, last_active_iso):
    """Atualiza last_active no máximo 1x por _SESSION_TOUCH_THROTTLE por sessão. O
    throttle usa o próprio last_active gravado (sem dict em memória: nada de lock, de
    poda, nem de vazamento; sobrevive a restart)."""
    stale = True
    if last_active_iso:
        try:
            dt = datetime.fromisoformat(last_active_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            stale = (datetime.now(timezone.utc) - dt).total_seconds() >= _SESSION_TOUCH_THROTTLE
        except (TypeError, ValueError):
            stale = True
    if stale:
        try:
            db.touch_session(sid)
        except Exception as e:
            log.warning("touch session falhou (%s)", e)


def _session_email():
    """Email do usuário SE a sessão é válida: cookie tem user + sid E o sid ainda existe
    na tabela (não revogado/expirado) e pertence a esse user. A validação roda a CADA
    request (sem cache), pra revogação valer no request seguinte. Devolve None se
    inválida (o chamador limpa a sessão e devolve 401)."""
    email = session.get("user")
    sid = session.get("sid")
    if not email or not sid:
        return None
    row = db.get_session(sid)
    if not row or row.get("user_email") != email:
        return None
    _maybe_touch_session(sid, row.get("last_active"))
    return email


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        email = _session_email()
        if not email:
            # Sessão ausente/revogada/legada (cookie sem sid): limpa o cookie e 401 no
            # MESMO formato de sempre, pro front cair na tela de login sem regressão.
            session.clear()
            return jsonify({"error": "Não autenticado.", "auth": False}), 401
        # Status conferido a CADA request: se um admin desativar/recusar a conta, o acesso
        # cai no próximo request (não espera a sessão ser revogada/expirar).
        user = db.get_user(email)
        if not user or (user.get("status") or "active") != "active":
            session.clear()
            return jsonify({"error": "Sua conta não está ativa.", "auth": False}), 401
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    """Como login_required, mas exige is_admin + status active no banco (nunca confia
    só no cliente). Protege todos os endpoints /api/admin/*."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        email = _session_email()
        if not email:
            session.clear()
            return jsonify({"error": "Não autenticado.", "auth": False}), 401
        user = db.get_user(email)
        if not user or not user.get("is_admin") or (user.get("status") or "active") != "active":
            return jsonify({"error": "Acesso restrito a administradores."}), 403
        return fn(*args, **kwargs)
    return wrapper


def _is_last_active_admin(user: dict) -> bool:
    """True se `user` for admin ativo E o único admin ativo. Guarda contra lockout:
    impede recusar/desativar/rebaixar o último administrador."""
    if not user or not user.get("is_admin") or (user.get("status") or "active") != "active":
        return False
    return db.count_active_admins() <= 1


def _open_session(email: str) -> str:
    """Abre (ou reaproveita) a sessão server-side pro email e grava no cookie assinado.
    Compartilhado por /api/login (senha) e /api/auth/zoho/callback (SSO) — as duas vias
    produzem exatamente a mesma sessão, então login_required/Segurança funcionam igual."""
    # Limpa sessões expiradas oportunisticamente (best-effort, não bloqueia o login).
    try:
        db.reap_sessions(config.SESSION_DAYS)
    except Exception as e:
        log.warning("reap sessions falhou (%s)", e)
    # Reusa o sid se o cookie já carrega um válido pra ESTE user (re-login no mesmo
    # navegador não deixa linha órfã); senão gera um novo.
    ip = _client_ip()
    ua = (request.headers.get("User-Agent") or "")[:_MAX_UA_LEN]
    sid = session.get("sid")
    existing = db.get_session(sid) if sid else None
    if existing and existing.get("user_email") == email:
        db.refresh_session(sid, ip, ua)
    else:
        # Cookie carrega sid de OUTRO usuário (ou inexistente): remove a linha antiga pra
        # não deixar sessão órfã e gera um sid novo.
        if existing and sid:
            try:
                db.delete_session(sid, existing.get("user_email"))
            except Exception as e:
                log.warning("open_session: limpar sessão antiga falhou (%s)", e)
        sid = str(uuid.uuid4())
        db.create_session(sid, email, ip, ua)
    session.permanent = True
    session["user"] = email
    session["sid"] = sid
    return sid


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    key = _login_key()
    if _login_blocked(key):
        return jsonify({
            "error": "Tentativas demais. Espere alguns minutos e tente de novo."
        }), 429

    user = db.get_user(email)
    # Mensagem genérica de propósito: não revela se o email existe.
    if not user or not check_password_hash(user["password_hash"], password):
        _login_record_failure(key)
        return jsonify({"error": "Email ou senha inválidos."}), 401

    # Conta precisa estar ATIVA. pending/rejected/inactive não logam (a senha até
    # confere, mas o acesso é barrado com mensagem específica).
    st = user.get("status") or "active"
    if st != "active":
        _login_clear(key)
        msg = {
            "pending": "Sua conta está aguardando aprovação de um administrador.",
            "rejected": "Seu cadastro não foi aprovado. Fale com um administrador.",
            "inactive": "Sua conta está desativada. Fale com um administrador.",
        }.get(st, "Sua conta não está ativa.")
        return jsonify({"error": msg}), 403

    _login_clear(key)
    _open_session(email)
    return jsonify({
        "email": email,
        "must_change_password": bool(user["must_change_password"]),
        "is_admin": bool(user.get("is_admin")),
        "status": st,
    })


@app.route("/api/logout", methods=["POST"])
def logout():
    # Captura o sid ANTES de limpar e remove a linha da sessão atual.
    sid = session.get("sid")
    email = session.get("user")
    session.clear()
    if sid and email:
        try:
            db.delete_session(sid, email)
        except Exception as e:
            log.warning("logout: delete session falhou (%s)", e)
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    email = _session_email()
    if not email:
        session.clear()
        return jsonify({"error": "Não autenticado.", "auth": False}), 401
    user = db.get_user(email)
    if not user or (user.get("status") or "active") != "active":
        session.clear()
        return jsonify({"error": "Não autenticado.", "auth": False}), 401
    return jsonify({
        "email": email,
        "must_change_password": bool(user["must_change_password"]),
        "is_admin": bool(user.get("is_admin")),
        "status": user.get("status") or "active",
    })


@app.route("/api/change_password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    email = session["user"]

    user = db.get_user(email)
    if not user or not check_password_hash(user["password_hash"], current):
        return jsonify({"error": "Senha atual incorreta."}), 400
    if len(new) < MIN_PASSWORD_LEN:
        return jsonify({"error": f"A nova senha precisa ter ao menos {MIN_PASSWORD_LEN} caracteres."}), 400
    if check_password_hash(user["password_hash"], new):
        return jsonify({"error": "A nova senha não pode ser igual à atual."}), 400

    db.set_user_password(email, generate_password_hash(new))
    # Trocar a senha encerra as OUTRAS sessões (mantém a atual): se a senha vazou, o
    # dono recupera o controle deslogando os demais dispositivos.
    try:
        db.delete_sessions_except(email, session.get("sid"))
    except Exception as e:
        log.warning("change_password: revogar outras sessões falhou (%s)", e)
    return jsonify({"ok": True})


# ─── Cadastro manual (com aprovação por admin) ───────────────────────────────

@app.route("/api/signup", methods=["POST"])
def signup():
    """Auto-cadastro: cria a conta como 'pending'. Só loga depois que um admin aprovar
    no painel. Qualquer e-mail é aceito — a aprovação é o filtro."""
    key = _login_key()
    if _login_blocked(key):
        return jsonify({"error": "Tentativas demais. Espere alguns minutos e tente de novo."}), 429
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    email_ok = "@" in email and "." in email.rsplit("@", 1)[-1] and 3 <= len(email) <= 254
    if not email_ok:
        return jsonify({"error": "Informe um e-mail válido."}), 400
    if len(password) < MIN_PASSWORD_LEN:
        return jsonify({"error": f"A senha precisa ter ao menos {MIN_PASSWORD_LEN} caracteres."}), 400
    _login_record_failure(key)  # conta a tentativa só pra limitar volume de spam
    # Resposta NEUTRA (mesma mensagem se o e-mail é novo ou já existe) pra não permitir
    # enumeração de contas. create_pending_user usa ON CONFLICT DO NOTHING, então nunca
    # sobrescreve conta existente.
    if db.get_user(email):
        log.info("signup ignorado (e-mail já cadastrado): %s", email)
    else:
        db.create_pending_user(email, generate_password_hash(password))
        log.info("signup pendente criado: %s", email)
    return jsonify({
        "ok": True,
        "message": "Cadastro recebido. Se o e-mail ainda não tiver conta, ela será liberada após a aprovação de um administrador.",
    })


# ─── Login com Zoho (OAuth2 / OpenID Connect) ────────────────────────────────
# SSO restrito por domínio (config.ALLOWED_EMAIL_DOMAINS): contas @seubone.com /
# @grupoquatro5.com entram direto (auto-aprovadas, active). O callback abre a MESMA
# sessão do login por senha, então login_required e a página de Segurança valem igual.
# A rota é /api/auth/zoho/callback (não /api/callback) de propósito — o Nginx bloqueia
# ^/api/(callback|internal); /auth/zoho/ no meio não casa com esse bloco.

def _decode_jwt_payload(token: str) -> dict:
    """Decodifica o payload de um JWT SEM validar a assinatura. Seguro aqui porque o
    id_token vem direto do endpoint de token do Zoho via TLS (back-channel autenticado
    com o client_secret) — não passa pelo navegador, não dá pra forjar de fora."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # restaura padding base64url
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception as e:
        log.warning("zoho: falha ao decodificar id_token (%s)", e)
        return {}


@app.route("/api/auth/providers")
def auth_providers():
    """Público: o front decide se mostra o botão 'Entrar com Zoho'."""
    return jsonify({"zoho": config.ZOHO_ENABLED})


@app.route("/api/auth/zoho/login")
def zoho_login():
    if not config.ZOHO_ENABLED:
        return redirect("/login?error=zoho_off")
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    session.permanent = True
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": config.ZOHO_CLIENT_ID,
        "scope": "openid email profile",  # OIDC, delimitado por espaço (verificado na doc Zoho)
        "redirect_uri": config.ZOHO_REDIRECT_URI,
        "state": state,
        "access_type": "online",  # só identidade; não precisamos de refresh_token
    })
    return redirect(f"{config.ZOHO_ACCOUNTS_BASE}/oauth/v2/auth?{params}")


@app.route("/api/auth/zoho/callback")
def zoho_callback():
    if not config.ZOHO_ENABLED:
        return redirect("/login?error=zoho_off")
    if request.args.get("error"):
        log.info("zoho callback erro do provedor: %s", request.args.get("error"))
        return redirect("/login?error=zoho_denied")
    # CSRF: o state do retorno tem que bater com o guardado na sessão (compare_digest).
    state = request.args.get("state") or ""
    saved = session.pop("oauth_state", None)
    if not saved or not secrets.compare_digest(state, saved):
        return redirect("/login?error=state")
    code = request.args.get("code") or ""
    if not code:
        return redirect("/login?error=nocode")

    # Troca o code por token (back-channel server-to-server, form-urlencoded).
    try:
        resp = requests.post(
            f"{config.ZOHO_ACCOUNTS_BASE}/oauth/v2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": config.ZOHO_CLIENT_ID,
                "client_secret": config.ZOHO_CLIENT_SECRET,
                "redirect_uri": config.ZOHO_REDIRECT_URI,
            },
            timeout=15,
        )
        resp.raise_for_status()
        token_data = resp.json()
    except Exception as e:
        log.warning("zoho token exchange falhou (%s)", e)
        return redirect("/login?error=zoho_token")

    # Email do id_token (OIDC traz email + email_verified). Fallback no userinfo se faltar.
    claims = _decode_jwt_payload(token_data.get("id_token") or "")
    email = (claims.get("email") or "").strip().lower()
    email_verified = claims.get("email_verified")
    access_token = token_data.get("access_token") or ""
    if not email and access_token:
        try:
            ui = requests.get(
                f"{config.ZOHO_ACCOUNTS_BASE}/oauth/v2/userinfo",
                headers={"Authorization": f"Zoho-oauthtoken {access_token}"},  # esquema Zoho, não Bearer
                timeout=15,
            )
            if ui.ok:
                info = ui.json()
                email = (info.get("email") or "").strip().lower()
                if email_verified is None:
                    email_verified = info.get("email_verified")
        except Exception as e:
            log.warning("zoho userinfo fallback falhou (%s)", e)

    if not email or "@" not in email:
        return redirect("/login?error=zoho_email")
    # email_verified existe e é boolean no Zoho: fail-closed se vier explicitamente
    # falso; se ausente, confia no gate de domínio (conta corporativa criada por admin).
    if email_verified is not None and not (email_verified is True or str(email_verified).lower() == "true"):
        log.info("zoho login negado (e-mail não verificado): %s", email)
        return redirect("/login?error=naoverificado")
    # Defesa extra: confere o emissor do id_token (hostname EXATO == host do DC config),
    # não substring (substring deixaria passar "accounts.zoho.com.evil.com").
    if claims:
        expected_host = urllib.parse.urlparse(config.ZOHO_ACCOUNTS_BASE).hostname
        iss_host = urllib.parse.urlparse(str(claims.get("iss", ""))).hostname
        if iss_host != expected_host:
            log.warning("zoho id_token com iss suspeito: %s", claims.get("iss"))
            return redirect("/login?error=zoho_token")
    domain = email.rsplit("@", 1)[-1]
    if domain not in config.ALLOWED_EMAIL_DOMAINS:
        log.info("zoho login negado (domínio fora da lista): %s", email)
        return redirect("/login?error=dominio")

    db.upsert_user_oauth(email, "zoho")
    # Respeita decisão de admin: rejeitado/desativado não entra nem via Zoho.
    u = db.get_user(email)
    st = (u.get("status") if u else "active") or "active"
    if st != "active":
        log.info("zoho login bloqueado (status=%s): %s", st, email)
        return redirect("/login?error=" + ("rejeitado" if st == "rejected" else "inativo"))
    _open_session(email)
    log.info("zoho login ok: %s", email)
    return redirect("/")


# ─── Admin: aprovação de cadastros e gestão de usuários ───────────────────────

@app.route("/api/admin/users")
@admin_required
def admin_users():
    return jsonify({"users": db.list_all_users()})


@app.route("/api/admin/approve", methods=["POST"])
@admin_required
def admin_approve():
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not db.get_user(email):
        return jsonify({"error": "Usuário não encontrado."}), 404
    db.set_user_status(email, "active")
    log.info("admin %s aprovou %s", session["user"], email)
    return jsonify({"ok": True})


@app.route("/api/admin/reject", methods=["POST"])
@admin_required
def admin_reject():
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    target = db.get_user(email)
    if not target:
        return jsonify({"error": "Usuário não encontrado."}), 404
    if email == session["user"]:
        return jsonify({"error": "Você não pode recusar a si mesmo."}), 400
    if _is_last_active_admin(target):
        return jsonify({"error": "Não é possível recusar o último administrador ativo."}), 400
    db.set_user_status(email, "rejected")
    db.delete_user_sessions(email)  # corta sessões ativas do recusado na hora
    log.info("admin %s recusou %s", session["user"], email)
    return jsonify({"ok": True})


@app.route("/api/admin/set_active", methods=["POST"])
@admin_required
def admin_set_active():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    active = bool(data.get("active"))
    target = db.get_user(email)
    if not target:
        return jsonify({"error": "Usuário não encontrado."}), 404
    if not active and email == session["user"]:
        return jsonify({"error": "Você não pode desativar a si mesmo."}), 400
    if not active and _is_last_active_admin(target):
        return jsonify({"error": "Não é possível desativar o último administrador ativo."}), 400
    db.set_user_status(email, "active" if active else "inactive")
    if not active:
        db.delete_user_sessions(email)
    log.info("admin %s set_active(%s) %s", session["user"], active, email)
    return jsonify({"ok": True})


@app.route("/api/admin/set_admin", methods=["POST"])
@admin_required
def admin_set_admin():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    make_admin = bool(data.get("is_admin"))
    target = db.get_user(email)
    if not target:
        return jsonify({"error": "Usuário não encontrado."}), 404
    if not make_admin and email == session["user"]:
        return jsonify({"error": "Você não pode remover seu próprio acesso de admin."}), 400
    if not make_admin and _is_last_active_admin(target):
        return jsonify({"error": "Não é possível remover o último administrador ativo."}), 400
    db.set_user_admin(email, make_admin)
    log.info("admin %s set_admin(%s) %s", session["user"], make_admin, email)
    return jsonify({"ok": True})


@app.route("/api/admin/delete", methods=["POST"])
@admin_required
def admin_delete():
    """Exclui um usuário em definitivo. Regra (reforçada no servidor, não só na UI): só pode
    excluir conta 'rejected' ou 'inactive' — nunca um usuário ativo/pendente. Não exclui a si
    mesmo. Limpa os dados pessoais (db.delete_user); jobs/projetos compartilhados ficam."""
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    target = db.get_user(email)
    if not target:
        return jsonify({"error": "Usuário não encontrado."}), 404
    if email == session["user"]:
        return jsonify({"error": "Você não pode excluir a si mesmo."}), 400
    status = (target.get("status") or "active")
    if status not in ("rejected", "inactive"):
        return jsonify({"error": "Só é possível excluir usuários recusados ou inativos."}), 400
    db.delete_user(email)
    log.info("admin %s excluiu %s", session["user"], email)
    return jsonify({"ok": True})


# ─── Modo manutenção (flag file lido pelo nginx) — só sessão de admin ────────
# A EXISTÊNCIA de config.MAINTENANCE_FLAG é o interruptor: o nginx testa o
# arquivo a cada request (if -f) e, se ligado, só deixa passar quem tem sessão
# de admin ativa (auth_request -> GET /api/internal/maintenance_gate, rotas
# internas mais abaixo); /health e a tela/API de login continuam sempre
# acessíveis (BACKEND §43). O botão do painel (estas rotas) e o break-glass por
# SSH (`maintenance on|off`) operam o MESMO arquivo — criar/remover tem efeito
# imediato, sem reload do nginx.

def _write_maintenance_flag(flag: str) -> None:
    """Cria/atualiza o flag. O conteúdo é só auditoria (quem/quando); o nginx olha
    a EXISTÊNCIA. Se o flag já existe com dono root (ex.: `sudo touch` ou break-glass
    SSH), open('w') daria EACCES — então remove (o unlink só exige escrita no
    DIRETÓRIO /var/lib/opussb, que é do www-data) e recria como www-data. Deixa o
    LIGAR idempotente e à prova de flag root-owned."""
    line = f"ligada por {session['user']} em {datetime.now(timezone.utc).isoformat()}\n"
    try:
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write(line)
    except PermissionError:
        os.unlink(flag)
        with open(flag, "w", encoding="utf-8") as fh:
            fh.write(line)


@app.route("/api/admin/maintenance")
@admin_required
def admin_maintenance_state():
    """Estado do modo manutenção. Desde a troca do gate por IP pra sessão de
    admin, qualquer admin ativo atravessa (não existe mais risco de
    auto-trancamento por IP pra reportar aqui). Estado = existência do flag,
    então mudanças feitas por fora (break-glass SSH, outro admin) aparecem
    aqui na hora."""
    return jsonify({"on": os.path.exists(config.MAINTENANCE_FLAG)})


@app.route("/api/admin/maintenance", methods=["POST"])
@admin_required
def admin_maintenance_set():
    """Liga/desliga criando/removendo o flag. Payload {"on": bool} é o estado
    DESEJADO (idempotente), não um toggle — repetir o request não inverte. O
    bool é ESTRITO de propósito: coerção tipo bool("false")=True ligaria a
    manutenção ao tentar desligar."""
    data = request.get_json(silent=True) or {}
    want = data.get("on")
    if not isinstance(want, bool):
        return jsonify({"error": 'Corpo esperado: {"on": true|false}.'}), 400
    flag = config.MAINTENANCE_FLAG
    try:
        if want:
            _write_maintenance_flag(flag)
        else:
            try:
                os.unlink(flag)
            except FileNotFoundError:
                pass  # já estava desligada — idempotente
    except OSError as e:
        log.error("manutenção: falha ao %s o flag %s (%s)", "criar" if want else "remover", flag, e)
        return jsonify({"error": "Não consegui alterar o flag de manutenção no servidor."}), 500
    log.info("admin %s %s o modo manutenção (ip %s)",
             session["user"], "LIGOU" if want else "desligou", _client_ip())
    return jsonify({"ok": True, "on": os.path.exists(flag)})


# ─── Painel de saldos (créditos) — só admin ──────────────────────────────────
_RUNPOD_GQL = "https://api.runpod.io/graphql"
_CREDITS_TTL = 300  # cache de 5 min: não bater nas APIs externas a cada abertura do painel
_credits_cache = {"data": None, "ts": 0.0}
_credits_lock = threading.Lock()


def _runpod_credits():
    """Saldo (USD) + gasto/hora da conta RunPod via GraphQL (validado: clientBalance/currentSpendPerHr).
    None em falha (best-effort: o card degrada, não quebra o painel)."""
    try:
        r = requests.post(
            _RUNPOD_GQL,
            json={"query": "query { myself { clientBalance currentSpendPerHr } }"},
            headers={"Authorization": f"Bearer {config.RUNPOD_API_KEY}", "Content-Type": "application/json"},
            timeout=15,
        )
        me = ((r.json() or {}).get("data") or {}).get("myself") or {}
        bal = me.get("clientBalance")
        if bal is None:
            return None
        return {"balance": round(float(bal), 2),
                "spend_per_hr": round(float(me.get("currentSpendPerHr") or 0), 4)}
    except Exception as e:
        log.warning("[credits] runpod: %s", e)
        return None


def _anthropic_credits():
    """Custo da org Anthropic. Exige a Admin API key (ANTHROPIC_ADMIN_KEY, diferente da normal). Sem
    ela, só sinaliza não-configurado (o saldo segue visível no Console). A integração do cost report
    entra quando o dono gerar a key (pra validar com dado real, não às cegas)."""
    return {"configured": bool(config.ANTHROPIC_ADMIN_KEY)}


@app.route("/api/admin/credits")
@admin_required
def admin_credits():
    """Saldos/custos pro painel admin. RunPod = saldo real; Anthropic = quando houver Admin key.
    Cacheado por _CREDITS_TTL; `?refresh=1` força atualizar."""
    refresh = request.args.get("refresh") in ("1", "true", "yes")
    with _credits_lock:
        now = time.monotonic()
        cached = _credits_cache["data"]
        if cached is not None and not refresh and (now - _credits_cache["ts"]) < _CREDITS_TTL:
            return jsonify({**cached, "cached": True})
        data = {"runpod": _runpod_credits(), "anthropic": _anthropic_credits()}
        _credits_cache["data"] = data
        _credits_cache["ts"] = now
        return jsonify({**data, "cached": False})


# ─── Sessões ativas (página de Segurança) ────────────────────────────────────

@app.route("/api/sessions")
@login_required
def sessions_list():
    """Sessões do usuário logado. current = a que fez esta request (sem botão de
    encerrar no front). last_active vem humanizado; location é o IP (ou null)."""
    cur = session.get("sid")
    rows = db.list_sessions(session["user"], max_age_days=config.SESSION_DAYS)
    out = []
    for r in rows:
        browser, os_name = _parse_ua(r.get("user_agent"))
        out.append({
            "id": r["id"],
            "current": r["id"] == cur,
            "browser": browser,
            "os": os_name,
            "location": r.get("ip") or None,
            "last_active": _humanize_pt(r.get("last_active")),
        })
    return jsonify({"sessions": out})


# Segmento estático: o Werkzeug pesa /revoke_all acima de /<sid>, então a ordem de
# registro não importa; DELETE /api/sessions/revoke_all (método errado) cairia em
# <sid>='revoke_all' e seria um no-op idempotente (uuid nunca é 'revoke_all').
@app.route("/api/sessions/revoke_all", methods=["POST"])
@login_required
def sessions_revoke_all():
    """Encerra todas as sessões do usuário MENOS a atual (num único DELETE)."""
    db.delete_sessions_except(session["user"], session.get("sid"))
    return jsonify({"ok": True})


@app.route("/api/sessions/<sid>", methods=["DELETE"])
@login_required
def sessions_revoke(sid):
    """Encerra UMA sessão (escopada ao dono). Idempotente: sempre 200 {ok:true}, mesmo
    se o id não existir ou for de outro usuário (não vira oráculo de enumeração).
    Encerrar a PRÓPRIA sessão atual é no-op (não derruba quem está pedindo)."""
    if sid != session.get("sid"):
        db.delete_session(sid, session["user"])
    return jsonify({"ok": True})


# ─── Rotas do app ────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Saúde do processo. Inclui um ping leve no banco e a contagem de trabalho em
    andamento, pra checagem de ops enxergar 'degraded' quando o SQLite trava. Sem segredos.

    Usa count_trabalho_em_andamento e NÃO count_active_jobs: este número é o que o ritual de
    deploy consulta ("restart do opussb só com active_jobs=0"), então precisa incluir a
    extração de MP3 da montagem, que roda com o job já 'done' e morreria feio num restart.
    O campo da resposta segue chamando 'active_jobs' pra não quebrar quem já consome."""
    db_ok, active = True, None
    try:
        active = db.count_trabalho_em_andamento()
    except Exception as e:
        db_ok = False
        log.warning("health: checagem do banco falhou (%s)", e)
    # As duas metades da pergunta "dá pra reiniciar?", que NÃO se somam: o banco responde a
    # parte DURÁVEL (estado esperando trabalho, sobrevive ao processo) e o em_voo responde a
    # EFÊMERA (o que este processo está fazendo agora, com idade). Cada uma num campo.
    return jsonify({
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "active_jobs": active,
    }), (200 if db_ok else 503)


@app.route("/api/status/<job_id>")
@login_required
def status(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404

    resp = {
        "status": job["status"],
        "progress": job["progress"],
        "filename": job["filename"],
        "captions": bool(job.get("captions", 1)),
    }

    if job["status"] == "done" and job["result_urls"]:
        resp["clips"] = _clip_objects(job_id, job["result_urls"], _thumb_url(job))

    if job["status"] == "error":
        resp["error"] = job["error"]
        if job["result_urls"]:
            resp["partial_clips"] = _clip_objects(job_id, job["result_urls"], _thumb_url(job))

    return jsonify(resp)


def _thumb_url(job):
    """Rota relativa (com sessão, devolve 302) do thumbnail do projeto, ou None se não
    houver frame. Jobs antigos sem thumbnail_key degradam pro fallback de onda no front."""
    return f"/api/projects/{job['job_id']}/thumb" if job.get("thumbnail_key") else None


def _clip_objects(job_id, result_urls_json, thumbnail_url=None):
    """Constrói a lista de Clip (score/title/reason + URL presignada) a partir do
    result_urls. Aceita o formato novo (objeto por clipe) e o antigo (só a chave),
    pra jobs criados antes desta mudança não quebrarem. thumbnail_url (opcional) é o
    frame do projeto, repassado a cada clipe pra o card do clipe também ter capa real."""
    try:
        items = json.loads(result_urls_json)
    except (TypeError, ValueError):
        return []
    clips = []
    for i, it in enumerate(items):
        meta = {} if isinstance(it, str) else (it or {})
        key = it if isinstance(it, str) else meta.get("key")
        if not key:
            continue
        clips.append({
            "id": f"{job_id}:{i}",
            # url agora aponta pra rota de vídeo (queima sob demanda + cache + 302),
            # não mais a URL presignada direta. Vale pra clipe novo e antigo.
            "url": f"/api/clips/{job_id}:{i}/video",
            "title": meta.get("title"),
            "reason": meta.get("reason"),
            "score": meta.get("score"),
            "duration": meta.get("duration"),
            # Poster PROPRIO de cada clipe (1 frame do clipe, gerado/cacheado sob demanda na rota),
            # pra o card nao ficar preto antes do play. 404 da rota -> o front degrada pro fundo.
            "thumbnail_url": f"/api/clips/{job_id}:{i}/thumb",
        })
    return clips


def _safe_filename(name, fallback="clip", maxlen=80):
    """Sanitiza um texto livre (título do clipe) num nome de arquivo seguro: tira acento
    (NFKD -> ASCII), remove o que não é [A-Za-z0-9 ._-], colapsa espaço e limita o tamanho.
    Mantém ASCII pra não quebrar o header Content-Disposition em navegador nenhum."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9 ._-]+", "", s)
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s[:maxlen].strip() or fallback


def _clip_download_name(job, idx):
    """Nome de arquivo amigável pro download de um clipe: 'NN titulo.mp4' (título do clips_json,
    se houver) ou 'clip_NN.mp4'. Usado pelo ?dl=1 da rota de vídeo e pelo download_all."""
    title = None
    try:
        items = json.loads(job.get("result_urls") or "[]")
        if 0 <= idx < len(items) and isinstance(items[idx], dict):
            title = items[idx].get("title")
    except (TypeError, ValueError):
        pass
    if title:
        return f"{idx + 1:02d} {_safe_filename(title)}.mp4"
    return f"clip_{idx + 1:02d}.mp4"


def _s3_redirect(key, download_name=None, expiry_seconds=None):
    """302 pra URL presignada de QUALQUER entregável no S3 (clipe, episódio, MP3, música). Com
    download_name, a presignada carrega Content-Disposition: attachment (força o download com
    nome amigável em vez de tocar inline).

    download_name também é o que distingue DOWNLOAD de streaming aqui: só o ?dl=1 (e o
    ?download=1 do episódio) passa nome, o player/editor/thumb passam None. Por isso o Transfer
    Acceleration entra exatamente neste ramo (ver config.S3_ACCELERATE_DOWNLOADS), sem tocar o
    caminho do player.

    PONTO ÚNICO de propósito: enquanto as rotas de episódio e MP3 chamavam get_presigned_url
    direto, ligar a flag não acelerava nada justamente nos arquivos maiores (o final.mp4 tem
    dezenas de GB contra dezenas de MB de um clipe), e falhava em silêncio. Rota nova que sirva
    arquivo do S3 deve passar por aqui, não montar presignada na mão."""
    # expiry_seconds: default None = o padrão do s3_client (24h), certo pros ENTREGÁVEIS (o
    # usuário abre o link, baixa depois, manda pra alguém). Quem serve material BRUTO passa um
    # teto curto: presignada é link ao portador e nenhuma revogação de sessão a alcança.
    extra = {} if expiry_seconds is None else {"expiry_seconds": expiry_seconds}
    if download_name:
        return redirect(s3_client.get_presigned_download_url(key, download_name, **extra),
                        code=302)
    return redirect(s3_client.get_presigned_url(key, **extra), code=302)


def _project_from_job(job):
    """Mapeia um job para o shape Project que o front consome (um job = um projeto).
    owner_email = quem subiu o vídeo (NULL = legado); a biblioteca é compartilhada, mas
    o front usa o dono pra exibir "Enviado por X" e liberar Excluir só pro dono/admin."""
    try:
        items = json.loads(job.get("result_urls") or "[]")
    except (TypeError, ValueError):
        items = []
    scores = [float(it["score"]) for it in items
              if isinstance(it, dict) and it.get("score") is not None]
    # Os estados estacionados do podcast (§39) passam CRUS pro front (o card linka pra
    # tela de aprovação e mostra o pill certo); job de clipes nunca os assume, então o
    # colapso pra "processing" segue idêntico no fluxo de clipes.
    passthrough = ("done", "error", "uploaded", "aguardando_aprovacao", "gancho_aprovado")
    status = job["status"] if job["status"] in passthrough else "processing"
    return {
        "id": job["job_id"],
        "title": job["filename"],
        "filename": job["filename"],
        "created_at": job.get("created_at"),
        "status": status,
        "thumbnail_url": _thumb_url(job),
        "clip_count": len(items) if job["status"] == "done" else 0,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "duration": None,
        "captions": bool(job.get("captions", 1)),
        "owner_email": job.get("user_email"),
        # Tela final padrão do projeto (id em config.END_SCREENS) ou null = nenhuma. Os clipes herdam.
        "end_screen": job.get("end_screen"),
        "job_type": job.get("job_type") or "clips",
    }


@app.route("/api/projects")
@login_required
def list_projects():
    # Uso interno: a biblioteca de projetos é COMPARTILHADA. Todo usuário ativo vê
    # todos os projetos (quem subiu fica em owner_email); só o dono e os admins podem
    # excluir (ver delete_project). list_jobs() sem argumento traz tudo, ordenado.
    jobs = db.list_jobs()
    return jsonify({"projects": [_project_from_job(j) for j in jobs], "total": len(jobs)})


@app.route("/api/projects/<job_id>")
@login_required
def get_project(job_id):
    # Biblioteca compartilhada: qualquer usuário ativo abre qualquer projeto.
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Projeto não encontrado."}), 404
    proj = _project_from_job(job)
    proj["clips"] = _clip_objects(job_id, job["result_urls"], _thumb_url(job)) if job["result_urls"] else []
    return jsonify(proj)


@app.route("/api/projects/<job_id>/end_screen", methods=["PUT"])
@login_required
def project_end_screen_put(job_id):
    """Define a tela final PADRÃO do projeto: body {end_screen: null|"<id>"}. null = nenhuma (default).
    Os clipes herdam (com override por clipe em /clips/<id>/end_screen). Invalida os finais (_es) de
    todos os clipes do projeto pra a próxima abertura refazer o anexo (o content cap/trim/base segue
    válido). Biblioteca compartilhada: qualquer usuário ativo configura."""
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Projeto não encontrado."}), 404
    data = request.get_json(silent=True) or {}
    val = data.get("end_screen")
    if val is not None and not db.get_end_screen(val):
        return jsonify({"error": "Tela final inválida."}), 400
    db.update_job(job_id, end_screen=val)
    try:
        items = json.loads(job.get("result_urls") or "[]")
    except (TypeError, ValueError):
        items = []
    for it in items:
        key = it if isinstance(it, str) else (it or {}).get("key")
        if key:
            _invalidate_endscreen(key)
    return jsonify({"ok": True, "end_screen": val})


@app.route("/api/projects/<job_id>/thumb")
@login_required
def project_thumb(job_id):
    """Thumbnail do projeto (frame real do melhor clipe) via 302 pra URL presignada.
    Biblioteca compartilhada: exige só sessão ativa. 404 se não houver frame."""
    job = db.get_job(job_id)
    if not job:
        abort(404)
    key = job.get("thumbnail_key")
    if not key:
        abort(404)
    return _s3_redirect(key)


@app.route("/api/projects/<job_id>/download_all")
@login_required
def download_all(job_id):
    """Lista os clipes do projeto pro front baixar um a um. Sem zip no servidor (custo de CPU/RAM
    e travaria o /health): cada item aponta pra rota de vídeo com ?dl=1, que entrega o clipe
    queimado sob demanda (serializado pelo semáforo de queima) já com Content-Disposition de
    download. Biblioteca compartilhada: qualquer usuário ativo. 404 se o job não existe; lista
    vazia se ainda não há clipes (job em processamento)."""
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Projeto não encontrado."}), 404
    try:
        items = json.loads(job.get("result_urls") or "[]")
    except (TypeError, ValueError):
        items = []
    clips = []
    for i, it in enumerate(items):
        key = it if isinstance(it, str) else (it or {}).get("key")
        if not key:
            continue
        cid = f"{job_id}:{i}"
        clips.append({
            "id": cid,
            "filename": _clip_download_name(job, i),
            "url": f"/api/clips/{cid}/video?dl=1",
        })
    return jsonify({"clips": clips, "count": len(clips)})


@app.route("/api/stats")
@login_required
def stats():
    jobs = db.list_jobs(session["user"])
    done = [j for j in jobs if j["status"] == "done" and (j.get("job_type") or "clips") != "podcast"]
    clips, all_scores = 0, []
    for j in done:
        try:
            items = json.loads(j.get("result_urls") or "[]")
        except (TypeError, ValueError):
            items = []
        clips += len(items)
        all_scores += [float(it["score"]) for it in items
                       if isinstance(it, dict) and it.get("score") is not None]
    return jsonify({
        "videos_processed": len(done),
        "clips_generated": clips,
        "avg_score": round(sum(all_scores) / len(all_scores), 1) if all_scores else None,
        "processing_count": sum(1 for j in jobs if j["status"] not in ("done", "error")
                                 and (j.get("job_type") or "clips") != "podcast"),
        "recent": [_project_from_job(j) for j in done[:4]],
    })


@app.route("/api/clips/favorites")
@login_required
def favorites_list():
    ids = db.list_favorites(session["user"])
    cache, clips = {}, []
    for cid in ids:
        jid, _, idx = cid.rpartition(":")          # clip_id = "job_id:index"
        if not jid:
            continue
        try:
            idx = int(idx)
        except ValueError:
            continue
        if jid not in cache:
            job = db.get_job(jid)
            cache[jid] = _clip_objects(jid, job["result_urls"], _thumb_url(job)) if job and job["result_urls"] else []
        arr = cache[jid]
        if 0 <= idx < len(arr):
            c = dict(arr[idx])
            c["favorite"] = True
            clips.append(c)
    return jsonify({"clips": clips})


# clip_id contém ":" (ex.: "uuid:3"), por isso <path:...>. O front manda encodeURIComponent.
@app.route("/api/clips/<path:clip_id>/favorite", methods=["POST"])
@login_required
def favorite_add(clip_id):
    db.add_favorite(session["user"], clip_id)
    return jsonify({"ok": True})


@app.route("/api/clips/<path:clip_id>/favorite", methods=["DELETE"])
@login_required
def favorite_remove(clip_id):
    db.remove_favorite(session["user"], clip_id)
    return jsonify({"ok": True})


# ─── Vídeo do clipe: queima da legenda sob demanda, com cache e lock ──────────
# A legenda não é queimada no processamento: o clipe sai como base SEM legenda
# (clip_NN_base.mp4) + os segmentos editáveis no banco. Aqui, quando o usuário
# pede o vídeo, a gente queima sob demanda e cacheia em clip_NN_cap.mp4.
_burn_locks = {}
_burn_locks_guard = threading.Lock()

# Semáforo GLOBAL de queima (não por clipe): limita quantos ffmpeg de queima rodam ao
# mesmo tempo no processo todo. O lock por clipe (_clip_burn_lock) só evita queimar o
# MESMO clipe em duplicata; clipes DIFERENTES tinham locks diferentes, então abrir vários
# clipes de um job recém-terminado disparava até 4 queimas em paralelo (uma por thread do
# gunicorn), cada ffmpeg usando os 3 núcleos -> load 10, RAM no swap, /health timava (a
# app parecia fora do ar). Com BURN_CONCURRENCY=1, uma queima por vez, rápida, e a VPS
# fica de pé. Quem não pega o slot dentro de _BURN_QUEUE_TIMEOUT serve a base (ver clip_video).
_burn_sem = threading.BoundedSemaphore(max(1, config.BURN_CONCURRENCY))
_BURN_QUEUE_TIMEOUT = config.BURN_QUEUE_TIMEOUT

# Poster (thumbnail) por clipe: semaforo PROPRIO (separado da queima). E leve (1 frame, nao um
# encode), mas varios cards podem pedir poster de uma vez, entao limita a concorrencia pra nao
# competir pela CPU com o web/queima. Lock por clipe evita gerar o mesmo poster em duplicata.
_thumb_sem = threading.BoundedSemaphore(max(1, config.THUMB_CONCURRENCY))
_thumb_locks = {}
_thumb_locks_guard = threading.Lock()


def _clip_thumb_lock(clip_id: str) -> threading.Lock:
    with _thumb_locks_guard:
        lk = _thumb_locks.get(clip_id)
        if lk is None:
            lk = threading.Lock()
            _thumb_locks[clip_id] = lk
        return lk


def _clip_burn_lock(clip_id: str) -> threading.Lock:
    """Lock por clipe: duas aberturas simultâneas do mesmo clipe não-cacheado não
    queimam em duplicata (a 2a espera e acha o cache pronto). Vale dentro do
    processo (gunicorn roda 1 worker), igual ao _upload_lock."""
    with _burn_locks_guard:
        lk = _burn_locks.get(clip_id)
        if lk is None:
            lk = threading.Lock()
            _burn_locks[clip_id] = lk
        return lk


def _filter_escape(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# Teto de tempo da queima. Se o ffmpeg travar, o subprocess.run mata o processo no
# estouro e levanta TimeoutExpired -> a rota cai no fallback (serve a base). Protege
# o worker do gunicorn de ficar pendurado. Clipe é <= max_duration (curto); 180s é
# folga larga mesmo com a VPS sob carga.
_BURN_TIMEOUT = 180


def _build_burn_cmd(base_path: str, ass_path, music_path, music_vol, out_path: str,
                    in_point=None, out_point=None) -> list:
    """Monta o comando ffmpeg da queima (função pura, sem efeito colateral, pra ser
    testável). Regras de mapeamento de stream:
      - vídeo: filtro de legenda (.ass) se houver, senão o vídeo da base direto;
      - áudio: se houver música, mixa fala+música; SENÃO mapeia o áudio original da
        base explicitamente (`0:a?`). Sem esse map, o `-map [v]` da legenda desliga
        o mapeamento automático do ffmpeg e o clipe sairia MUDO (era o bug). O `?`
        deixa o áudio opcional pra não quebrar caso a base não tenha trilha.

    Com handles, a base é a JANELA estendida; in_point/out_point (relativos à janela) cortam o
    corte oficial via OUTPUT seek (-ss/-t DEPOIS do -i): decodifica desde 0 e rezera o PTS, então
    o filtro subtitles vê a escala do corte (a legenda já vem deslocada por shift_segments_to_cut,
    casando frame a frame). O seek se aplica ao input 0 (janela); a música (input 1, em loop) é
    limitada ao corte pelo amix duration=first (que segue [0:a], a fala). Sem in/out válidos, não
    corta (comportamento antigo). `-c:a copy` no caso só-legenda corta em borda de pacote AAC
    (deslize <= ~1 frame de áudio, imperceptível; o vídeo é re-encodado e a legenda casa com ele)."""
    cmd = ["ffmpeg", "-y", "-i", base_path]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]   # loopa a música pra cobrir o clipe
    do_trim = False
    try:
        if in_point is not None and out_point is not None and float(out_point) > float(in_point):
            do_trim = True
    except (TypeError, ValueError):
        do_trim = False
    if do_trim:
        cmd += ["-ss", f"{float(in_point):.3f}", "-t", f"{float(out_point) - float(in_point):.3f}",
                "-avoid_negative_ts", "make_zero"]
    fc = []
    if ass_path:
        fc.append(f"[0:v]subtitles={_filter_escape(ass_path)}[v]")
        vmap = "[v]"
    else:
        vmap = "0:v"
    if music_path:
        try:
            vol = max(0.0, min(float(music_vol), 1.0))
        except (TypeError, ValueError):
            vol = _DEFAULT_MUSIC_VOL
        fc.append(f"[1:a]volume={vol}[m]")
        fc.append("[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]")
        amap = "[a]"
    else:
        amap = None
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]
    cmd += ["-map", vmap]
    if amap:
        cmd += ["-map", amap, "-c:a", "aac"]
    else:
        # Sem música: preserva o áudio original do clipe (copiando, sem recodificar).
        cmd += ["-map", "0:a?", "-c:a", "copy"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    cmd += ["-threads", str(config.BURN_FFMPEG_THREADS)]   # limita o encoder p/ não tomar todos os núcleos (deixa CPU pro web)
    cmd += ["-movflags", "+faststart"]   # moov no início: playback progressivo no navegador
    if music_path:
        cmd += ["-shortest"]
    cmd += [out_path]
    return cmd


def _burn_clip(base_path: str, ass_path, music_path, music_vol, out_path: str,
               in_point=None, out_point=None) -> None:
    """Renderiza o clipe final a partir da base (já 1080x1920): queima a legenda
    (.ass, se houver) no vídeo e mixa a música de fundo (se houver, em loop e com
    volume) no áudio. Com handles, corta a janela em [in_point, out_point]. Levanta se
    o ffmpeg falhar, sair vazio, ou estourar _BURN_TIMEOUT (o subprocess é morto). O
    chamador só sobe pro S3 DEPOIS que isto retorna ok, então uma render que falha no
    meio nunca vira um cache corrompido."""
    cmd = _build_burn_cmd(base_path, ass_path, music_path, music_vol, out_path, in_point, out_point)
    # nice: roda o ffmpeg da queima com prioridade baixa, pra ele CEDER CPU às requisições web e o
    # site não travar enquanto queima (preexec_fn nica o próprio ffmpeg, então o timeout ainda mata
    # o processo certo). Só faz a syscall os.nice no filho (seguro mesmo com o worker multithread).
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=_BURN_TIMEOUT,
                       preexec_fn=lambda: os.nice(config.BURN_NICE))
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(f"ffmpeg render falhou: {r.stderr[-400:]}")


def _invalidate_cap(base_key: str) -> None:
    """Apaga os derivados renderizados (_cap.mp4 COM legenda, _trim.mp4 do corte sem legenda, e os
    finais com tela final _*_es.mp4), idempotente. Chamado quando legenda, música, posição/estilo OU
    os pontos de corte (in/out) mudam, pra a próxima abertura re-renderizar com o conteúdo novo (e a
    tela final, que vem por cima do content, também precisa ser refeita)."""
    for suffix in ("_cap.mp4", "_trim.mp4", "_cap_es.mp4", "_trim_es.mp4", "_base_es.mp4"):
        derived = base_key.replace("_base.mp4", suffix)
        if derived == base_key:
            continue
        try:
            s3_client.delete_object(derived)
        except Exception as e:
            log.warning("[cache] falha ao invalidar %s: %s", derived, e)


def _invalidate_endscreen(base_key: str) -> None:
    """Apaga SÓ os finais com tela final (_*_es.mp4), idempotente. Chamado quando a escolha de tela
    final do clipe (ou do projeto) muda, mas o content (cap/trim/base) continua válido: assim o
    toggle de tela final NÃO re-queima a legenda, só refaz o anexo (barato). Cobre clipe novo
    (_cap/_trim/_base) e antigo (_9x16)."""
    keys = set()
    if base_key.endswith("_base.mp4"):
        for suffix in ("_cap.mp4", "_trim.mp4", "_base.mp4"):
            keys.add(_endscreen_key(base_key.replace("_base.mp4", suffix)))
    else:
        keys.add(_endscreen_key(base_key))               # clipe antigo (_9x16) só tem o _es direto
    for k in keys:
        try:
            s3_client.delete_object(k)
        except Exception as e:
            log.warning("[cache] falha ao invalidar %s: %s", k, e)


# ─── Tela final (outro) anexada ao fim do clipe ───────────────────────────────
# Escopo por projeto (jobs.end_screen) com override por clipe (clip_captions.end_screen). O anexo é
# concat-copy com o outro RE-ENCODADO pra casar os params do clipe (validado: concat-copy cru
# corrompe timestamps com fps diferente). Roda no mesmo semáforo da queima (ffmpeg) e degrada
# honestamente: se qualquer passo falha, serve o clipe SEM tela final (sempre toca).

def _resolve_end_screen_id(job, clip_id):
    """Tela final efetiva do clipe. Override do clipe tem prioridade: "none" = sem tela; "<id>" =
    força essa tela; NULL = herda o default do projeto (jobs.end_screen). Retorna um id que EXISTE na
    biblioteca (tabela end_screens) ou None (sem tela final ou id apagado)."""
    choice = db.get_clip_end_screen(clip_id)
    if choice == "none":
        return None
    es_id = choice or job.get("end_screen")
    if es_id and db.get_end_screen(es_id):
        return es_id
    return None


def _end_screen_options():
    """Biblioteca de telas finais pro front (id + rótulo + duração do asset). Lê do DB."""
    return [{"id": e["id"], "label": e.get("label", e["id"]), "duration": e.get("duration")}
            for e in db.list_end_screens()]


def _endscreen_key(content_key: str) -> str:
    """Chave do entregável final (content + tela): clip_NN_cap.mp4 -> clip_NN_cap_es.mp4 (idem _trim
    e _base, e qualquer .mp4 -> _es.mp4 cobrindo clipe antigo)."""
    if content_key.endswith("_es.mp4"):
        return content_key
    if content_key.endswith(".mp4"):
        return content_key[:-4] + "_es.mp4"
    return content_key + "_es.mp4"


# Movida pro podcast_compose (§39 Parte 5) e estendida (profile/level/SAR/dimensões); o
# alias preserva os call sites e o contrato daqui: sem áudio devolve None (require_audio
# default True), e a chave de cache do _matched/ segue usando só os 4 campos históricos.
_probe_av_signature = podcast_compose.probe_av_signature


def _ensure_matched_outro(es_id: str, outro_src_key: str, sig: dict, tmp: str, dur=None):
    """Re-encoda o outro pros params do clipe (fps/timescale/áudio + escala 1080x1920 pra aceitar
    upload de qualquer resolução) e cacheia no S3 por assinatura. Com `dur`, corta nos primeiros N
    segundos (encurtar a tela final). Devolve o path local do outro casado (baixado), ou None em
    falha. Cacheado por assinatura+duração: instantâneo nas próximas."""
    sig_str = f"{sig['fps_s']}_{sig['timescale']}_{sig['ar']}_{sig['ac']}".replace("/", "-")
    dur_tag = f"_{dur:.2f}" if (dur and dur > 0) else ""
    matched_key = config.END_SCREEN_PREFIX + f"_matched/{es_id}_{sig_str}{dur_tag}.mp4"
    matched_path = os.path.join(tmp, "matched.mp4")
    if s3_client.exists(matched_key):
        s3_client.download_file(matched_key, matched_path)
        return matched_path
    src_path = os.path.join(tmp, "outro_src.mp4")
    s3_client.download_file(outro_src_key, src_path)
    cmd = ["ffmpeg", "-y", "-i", src_path]
    if dur and dur > 0:
        cmd += ["-t", f"{float(dur):.3f}"]                # encurta a tela final (primeiros N s)
    cmd += ["-r", sig["fps_s"], "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-video_track_timescale", str(sig["timescale"]),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(sig["ar"]), "-ac", str(sig["ac"]),
            "-threads", str(config.BURN_FFMPEG_THREADS), "-movflags", "+faststart", matched_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=config.END_SCREEN_TIMEOUT,
                       preexec_fn=lambda: os.nice(config.BURN_NICE))
    if r.returncode != 0 or not os.path.exists(matched_path) or os.path.getsize(matched_path) < 1024:
        log.warning("[endscreen] casar o outro falhou: %s", r.stderr[-300:])
        return None
    try:
        s3_client.upload_file(matched_path, matched_key)   # cacheia pro próximo clipe de mesma assinatura
    except Exception as e:
        log.warning("[endscreen] cache do matched outro falhou: %s", e)
    return matched_path


def _append_end_screen(content_path: str, outro_path: str, out_path: str) -> bool:
    """Concatena content + outro (já casado) por concat-copy (NÃO re-encoda o content, o caro) +
    faststart. Só funciona porque o outro foi casado aos params do content. Delegado pro
    concat_copy genérico do podcast_compose (mesma linha de comando de sempre)."""
    return podcast_compose.concat_copy([content_path, outro_path], out_path,
                                       timeout=config.END_SCREEN_TIMEOUT, nice=config.BURN_NICE)


def _produce_endscreen(content_key: str, es_id: str, final_key: str, dur=None) -> bool:
    """Baixa o content, casa o outro (encurtado a `dur` se informado), concat-copy [content + outro]
    -> final_key no S3. Sob o semáforo GLOBAL de queima (ffmpeg pesado-ish; não pode ser chamado com o
    semáforo já segurado). Retorna True se subiu o final; False em qualquer falha (o chamador serve o
    content sem tela final)."""
    es = db.get_end_screen(es_id)
    if not es or not es.get("s3_key"):
        return False
    if not _burn_sem.acquire(timeout=_BURN_QUEUE_TIMEOUT):
        log.warning("[endscreen] fila cheia para %s; sem tela final", content_key)
        return False
    _tk_burn = em_voo.entrar("burn", f"endscreen {content_key}")
    tmp = None
    try:
        if s3_client.exists(final_key):                       # outra thread produziu enquanto esperávamos o slot
            return True
        tmp = tempfile.mkdtemp(prefix="es_")
        content_path = os.path.join(tmp, "content.mp4")
        s3_client.download_file(content_key, content_path)
        sig = _probe_av_signature(content_path)
        if not sig:
            log.warning("[endscreen] %s sem assinatura A/V (áudio?); sem tela final", content_key)
            return False
        matched_path = _ensure_matched_outro(es_id, es["s3_key"], sig, tmp, dur)
        if not matched_path:
            return False
        out_path = os.path.join(tmp, "final.mp4")
        if not _append_end_screen(content_path, matched_path, out_path):
            return False
        s3_client.upload_file(out_path, final_key)            # só após render local ok
        return True
    except Exception as e:
        log.warning("[endscreen] clip %s falhou (%s); sem tela final", content_key, e)
        return False
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        em_voo.sair(_tk_burn)
        _burn_sem.release()


def _cache_quente(key: str):
    """s3_client.exists pra CHECAGEM DE CACHE no S3, tolerante a estol. Devolve True (tem),
    False (nao tem) ou None (o HEAD estolou, S3StalledError: nao deu pra saber).

    Existe porque o watchdog do s3_client (2026-07-25) fez o exists() PROPAGAR um HEAD travado em
    vez de pendurar a thread pra sempre, e os caminhos daqui prometem degradar, nao dar 500: o
    clipe sempre toca, o card cai no fundo atual, a faixa fica neutra. Cada chamador trata None
    como "degrada AGORA, sem tentar gerar": com o S3 estolado, o caminho de geracao baixaria a
    base e ficaria preso ate o teto (S3_STALL_TIMEOUT), prendendo uma das 4 threads do gunicorn
    por minutos, que e exatamente o que o watchdog existe pra evitar."""
    try:
        return s3_client.exists(key)
    except s3_client.S3StalledError as e:
        log.warning("[s3] cache de %s indisponivel (%s); degradando sem tentar gerar", key, e)
        return None


def _serve_clip(content_key: str, es_id, dl_name, es_dur=None):
    """302 pro entregável do clipe, anexando a tela final (es_id, encurtada a es_dur se informado) se
    houver. Sem es_id, serve o content direto. Com es_id, garante <content>_es.mp4 e serve ele; se o
    anexo falhar, serve o content sem tela final (o clipe sempre toca). Só pode ser chamado FORA do
    semáforo de queima (o anexo o adquire)."""
    if not es_id:
        return _s3_redirect(content_key, dl_name)
    final_key = _endscreen_key(content_key)
    if final_key == content_key:                              # chave sem .mp4 (defensivo)
        return _s3_redirect(content_key, dl_name)
    quente = _cache_quente(final_key)                         # cache quente do final
    if quente:
        return _s3_redirect(final_key, dl_name)
    if quente is None:                                        # S3 estolado: nem tenta anexar
        return _s3_redirect(content_key, dl_name)
    if _produce_endscreen(content_key, es_id, final_key, es_dur):
        return _s3_redirect(final_key, dl_name)
    return _s3_redirect(content_key, dl_name)               # anexo falhou: degrada (clipe toca)


def _ensure_burn(clip_id, base_key, target_key, do_caps, do_music, segments_json,
                 caption_y, caption_style, music, trim_needed, in_pt, out_pt):
    """Garante o content_key (queima/trim sob demanda) no S3. Devolve a chave a SERVIR (target_key se
    renderizou/existe, ou base_key quando não há nada a aplicar), ou None em fallback (servir base SEM
    tela final). Gerencia o semáforo GLOBAL de queima inteiramente (sempre libera antes de retornar),
    pra o anexo da tela final (que readquire o semáforo) nunca dar deadlock."""
    quente = _cache_quente(target_key)                        # cache quente
    if quente:
        return target_key
    if quente is None:
        return None                                           # S3 estolado: base sem gastar o teto
    with _clip_burn_lock(clip_id):
        quente = _cache_quente(target_key)                    # outra thread renderizou
        if quente:
            return target_key
        if quente is None:
            return None
        if not _burn_sem.acquire(timeout=_BURN_QUEUE_TIMEOUT):
            log.warning("[burn] fila de queima cheia para %s; servindo base", clip_id)
            return None                                       # fallback: base sem tela final
        _tk_burn2 = em_voo.entrar("burn", f"clip {clip_id}")
        tmp = None
        try:
            tmp = tempfile.mkdtemp(prefix="burn_")
            base_path = os.path.join(tmp, "base.mp4")
            out_path = os.path.join(tmp, "out.mp4")
            s3_client.download_file(base_key, base_path)
            ass_path = None
            if do_caps:
                # A legenda fica na escala da JANELA (mesma do base.mp4). Com o output seek do trim, o
                # filtro subtitles usa o PTS da janela e o corte/reset do ffmpeg cuida do resto: a
                # legenda do trecho [in,out] casa frame a frame e a das sobras cai em frames descartados.
                ass = captions.build_ass_from_segments(json.loads(segments_json), caption_y, caption_style)
                if ass:
                    ass_path = os.path.join(tmp, "cap.ass")
                    with open(ass_path, "w", encoding="utf-8") as f:
                        f.write(ass)
            music_path = None
            if do_music:
                music_path = os.path.join(tmp, "music" + os.path.splitext(music["music_key"])[1])
                s3_client.download_file(music["music_key"], music_path)
            if not ass_path and not music_path and not trim_needed:   # nada a aplicar -> base (com tela final)
                return base_key
            burn_in, burn_out = (in_pt, out_pt) if trim_needed else (None, None)
            _burn_clip(base_path, ass_path, music_path, (music or {}).get("volume"), out_path,
                       burn_in, burn_out)
            s3_client.upload_file(out_path, target_key)        # só após render local ok
            return target_key
        except Exception as e:
            log.warning("[burn] clip %s falhou (%s); servindo base", clip_id, e)
            return None                                        # fallback: base sem tela final
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            em_voo.sair(_tk_burn2)
            _burn_sem.release()


@app.route("/api/clips/<path:clip_id>/video")
@login_required
def clip_video(clip_id):
    """Entrega o vídeo do clipe via 302 pra URL presignada do S3. Queima a legenda sob demanda
    (cacheada) e, com handles, CORTA a janela em [in_point, out_point]. `?nocap=1` entrega o
    corte SEM legenda (cache separado _trim.mp4), usado pelo "Exportar sem legenda" do editor.
    Se o clipe tiver tela final (por projeto ou override do clipe), ela é ANEXADA por cima do
    entregável (com ou sem legenda). Em qualquer falha, faz 302 pro melhor disponível (o clipe
    sempre toca) e nunca deixa cache corrompido no S3."""
    job, idx, base_key = _clip_lookup(clip_id)
    want_trim = request.args.get("nocap") in ("1", "true", "yes")
    # ?dl=1: força download (Content-Disposition: attachment, nome amigável) em vez de tocar inline.
    # Usado pelo "Baixar todos" do projeto. Sem dl, dl_name=None e o comportamento é o de antes.
    dl_name = _clip_download_name(job, idx) if request.args.get("dl") in ("1", "true", "yes") else None

    segments_json = db.get_clip_captions(clip_id)
    music = db.get_clip_music(clip_id)
    caption_y = db.get_clip_caption_y(clip_id)
    raw_style = db.get_clip_caption_style(clip_id)
    try:
        caption_style = json.loads(raw_style) if raw_style else None
    except (TypeError, ValueError):
        caption_style = None
    window = db.get_clip_window(clip_id)

    cap_key = base_key.replace("_base.mp4", "_cap.mp4")
    is_new = cap_key != base_key                         # _base (novo) vs _9x16 (antigo, já queimado)

    # Trim da janela: só pra clipe novo com janela persistida cujo corte NÃO cobre a janela
    # inteira (in > 0 ou out < window_dur). eps absorve arredondamento.
    in_pt = out_pt = None
    trim_needed = False
    if is_new and window and window.get("window_dur"):
        win_dur = float(window["window_dur"])
        in_pt = max(0.0, float(window.get("in_point") or 0.0))
        out_pt = float(window["out_point"]) if window.get("out_point") is not None else win_dur
        trim_needed = (in_pt > 0.05) or (out_pt < win_dur - 0.05)

    # Testa os segmentos DECODIFICADOS (nao a string): o auto-seed de keyframes (Fase 1b) pode criar
    # uma linha clip_captions com segments='[]' pra clipe SEM fala; '[]' e truthy como string e
    # dispararia uma queima vazia. So queima legenda quando ha segmento de verdade.
    try:
        _has_segments = bool(json.loads(segments_json)) if segments_json else False
    except (TypeError, ValueError):
        _has_segments = False
    use_caps = bool(job.get("captions", 1)) and _has_segments
    use_music = bool(music and music.get("music_key"))

    es_id = _resolve_end_screen_id(job, clip_id)         # tela final efetiva (id) ou None
    es_dur = db.get_clip_end_screen_dur(clip_id) if es_id else None   # encurtar a tela neste clipe

    if want_trim:
        # Export SEM legenda: corte cru reenquadrado, sem legenda MAS COM música (a música não some
        # só porque tirou a legenda). A tela final entra por cima (anexada por _serve_clip). Se não há
        # trim NEM música, a base já é o content (serve direto, sem re-render).
        do_caps = False
        do_music = use_music
        if not trim_needed and not do_music:
            return _serve_clip(base_key, es_id, dl_name, es_dur)
        target_key = base_key.replace("_base.mp4", "_trim.mp4")
    else:
        # Sem queima/trim (clipe antigo, ou novo sem legenda/música e sem trim): o content é a base
        # (a tela final ainda pode entrar por cima dela).
        if not (is_new and (use_caps or use_music or trim_needed)):
            return _serve_clip(base_key, es_id, dl_name, es_dur)
        target_key = cap_key
        do_caps, do_music = use_caps, use_music

    # Garante o content (queima/trim sob demanda) e SÓ DEPOIS anexa a tela final (fora do semáforo
    # de queima, que o anexo readquire). _ensure_burn devolve None em fallback de queima.
    content_key = _ensure_burn(clip_id, base_key, target_key, do_caps, do_music, segments_json,
                               caption_y, caption_style, music, trim_needed, in_pt, out_pt)
    if content_key is None:                              # fallback da queima: serve a base SEM tela final
        return _s3_redirect(base_key, dl_name)
    return _serve_clip(content_key, es_id, dl_name, es_dur)


@app.route("/api/clips/<path:clip_id>/base")
@login_required
def clip_base(clip_id):
    """Entrega o clipe SEM a legenda queimada (302 pra base no S3). Usado pelo editor
    de timeline do front: a legenda e sobreposta AO VIVO no preview (sem duplicar com a
    queimada), e a queima final continua acontecendo sob demanda em /video. So faz sentido
    pra clipe novo (_base.mp4); clipe antigo (_9x16.mp4) cai na propria base mesmo."""
    _job, _idx, base_key = _clip_lookup(clip_id)
    return _s3_redirect(base_key)


def _src169_key_for(job_id: str, idx: int) -> str:
    """Chave S3 do corte 16:9 RETIDO (fonte do crop por keyframe, BACKEND §33): prefixo DEDICADO
    (lifecycle/TTL), 1-based como as outras keys de clipe. Deriva de job_id + idx (NAO do base_key,
    que vive sob jobs/.../clips/)."""
    return f"{config.SOURCE16X9_PREFIX}{job_id}/clip_{idx+1:02d}.mp4"


@app.route("/api/clips/<path:clip_id>/src169")
@login_required
def clip_src169(clip_id):
    """Entrega o corte 16:9 RETIDO (fonte do crop por keyframe): 302 pra URL presignada. So existe
    pra clipe processado DEPOIS da retencao (BACKEND §33); senao 404 (o editor de crop degrada pra
    indisponivel). Pan-only: o front arrasta a caixa 9:16 sobre este 16:9."""
    job, idx, _base_key = _clip_lookup(clip_id)
    src_key = _src169_key_for(job["job_id"], idx)
    if _cache_quente(src_key) is not True:   # inclui S3 estolado (None): mesma degradacao do 404
        abort(404)
    return _s3_redirect(src_key)


# Teto de tempo da extracao do poster (range-seek + decode de 1 frame). Curto: e barato.
_THUMB_TIMEOUT = 30


def _thumb_key_for(base_key: str) -> str:
    """Chave S3 do poster derivada da base: clip_NN_base.mp4 -> clip_NN_thumb.jpg (e qualquer
    .mp4 -> _thumb.jpg, cobrindo clipe antigo _9x16.mp4)."""
    if base_key.endswith("_base.mp4"):
        return base_key[:-len("_base.mp4")] + "_thumb.jpg"
    return re.sub(r"\.mp4$", "_thumb.jpg", base_key)


@app.route("/api/clips/<path:clip_id>/thumb")
@login_required
def clip_thumb(clip_id):
    """Poster (1 frame real) do clipe pra o card nao ficar preto antes do play. NAO queima legenda
    (isso so acontece no play, em /video). 302 pro JPG cacheado no S3; na 1a vez extrai 1 frame da
    base (seek por range, ~2s, niced + 1 thread) sob semaforo + lock por clipe. Cobre clipe novo e
    antigo. Best-effort: sem slot ou falha na extracao -> 404 (o card degrada pro fundo atual).
    Biblioteca compartilhada: exige so sessao ativa."""
    job, idx, base_key = _clip_lookup(clip_id)
    thumb_key = _thumb_key_for(base_key)
    quente = _cache_quente(thumb_key)
    if quente:
        return _s3_redirect(thumb_key)
    if quente is None:
        abort(404)                                           # S3 estolado: card cai no fundo atual
    with _clip_thumb_lock(clip_id):
        quente = _cache_quente(thumb_key)                    # outra thread acabou de gerar
        if quente:
            return _s3_redirect(thumb_key)
        if quente is None:
            abort(404)
        if not _thumb_sem.acquire(timeout=config.THUMB_QUEUE_TIMEOUT):
            abort(404)                                       # fila cheia: card sem poster, re-tenta depois
        _tk_mid1 = em_voo.entrar("midia", f"thumb {clip_id}")
        tmp = None
        try:
            # Frame de DENTRO do corte (nao da sobra-cabeca): in_point + 1s. Sem janela (legado), 1s.
            ss = 1.0
            win = db.get_clip_window(clip_id)
            if win and win.get("in_point") is not None:
                ss = float(win["in_point"]) + 1.0
            url = s3_client.get_presigned_url(base_key)
            tmp = tempfile.mkdtemp()
            out = os.path.join(tmp, "thumb.jpg")
            vf = "scale=360:-2"
            base_cmd = ["-frames:v", "1", "-vf", vf, "-q:v", "4", "-threads", "1", out]
            r = subprocess.run(["ffmpeg", "-y", "-ss", f"{ss:.3f}", "-i", url] + base_cmd,
                               capture_output=True, text=True, timeout=_THUMB_TIMEOUT,
                               preexec_fn=lambda: os.nice(config.BURN_NICE))
            if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 256:
                # Fallback: 1o frame (clipe curtissimo ou seek alem do fim).
                r = subprocess.run(["ffmpeg", "-y", "-i", url] + base_cmd,
                                   capture_output=True, text=True, timeout=_THUMB_TIMEOUT,
                                   preexec_fn=lambda: os.nice(config.BURN_NICE))
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) >= 256:
                s3_client.upload_file(out, thumb_key)
                return _s3_redirect(thumb_key)
            log.warning("[thumb] clip %s: ffmpeg falhou (%s)", clip_id, r.stderr[-200:])
            abort(404)
        finally:
            em_voo.sair(_tk_mid1)
            _thumb_sem.release()
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)


_WAVE_TIMEOUT = 60
_WAVE_BUCKETS = 400


def _compute_peaks(url: str, buckets: int = _WAVE_BUCKETS) -> list:
    """Picos da forma de onda do audio (mono 8kHz) pra a faixa de audio do editor. Roda no
    SERVIDOR (o WebAudio do navegador e bloqueado pelo CORS do S3). Le o PCM via ffmpeg e reduz
    a `buckets` valores normalizados 0..1. Niced pra ceder CPU ao web."""
    cmd = ["ffmpeg", "-v", "quiet", "-i", url, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=_WAVE_TIMEOUT,
                       preexec_fn=lambda: os.nice(config.BURN_NICE))
    data = p.stdout or b""
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    n = len(samples)
    if n == 0:
        return []
    block = max(1, n // buckets)
    peaks, mx = [], 1
    for i in range(buckets):
        s = i * block
        if s >= n:
            break
        e = min(s + block, n)
        pk = 0
        for j in range(s, e):
            v = samples[j]
            av = v if v >= 0 else -v
            if av > pk:
                pk = av
        peaks.append(pk)
        if pk > mx:
            mx = pk
    return [round(x / mx, 3) for x in peaks]


@app.route("/api/clips/<path:clip_id>/waveform")
@login_required
def clip_waveform(clip_id):
    """Picos da forma de onda do clipe pra a faixa de audio do editor (JSON {peaks:[0..1]}).
    Gerado no servidor (o WebAudio do navegador e bloqueado pelo CORS do S3) e cacheado no banco
    (coluna aditiva). Reusa o semaforo leve dos posters. Best-effort: falha/fila cheia -> {peaks:[]}
    (a faixa fica neutra, sem quebrar o editor). NAO queima legenda. Biblioteca compartilhada."""
    _job, _idx, base_key = _clip_lookup(clip_id)
    cached = db.get_clip_waveform(clip_id)
    if cached:
        try:
            return jsonify({"peaks": json.loads(cached)})
        except (TypeError, ValueError):
            pass
    if not _thumb_sem.acquire(timeout=config.THUMB_QUEUE_TIMEOUT):
        return jsonify({"peaks": []})
    _tk_mid2 = em_voo.entrar("midia", f"waveform {clip_id}")
    try:
        peaks = _compute_peaks(s3_client.get_presigned_url(base_key))
        if peaks:
            db.set_clip_waveform(clip_id, json.dumps(peaks))
        return jsonify({"peaks": peaks})
    except Exception as e:
        log.warning("[waveform] clip %s: %s", clip_id, e)
        return jsonify({"peaks": []})
    finally:
        em_voo.sair(_tk_mid2)
        _thumb_sem.release()


_STRIP_TIMEOUT = 60
# nº de frames no sprite. O front repete o sprite na PROPORCAO certa (background-size:auto 100% +
# repeat-x), entao mais frames = mais variacao ao longo da trilha antes de repetir (no zoom normal
# nao se ve repeticao obvia; no zoom extremo repete um pouco, e caso de borda aceitavel).
_STRIP_COLS = 36


def _strip_key_for(base_key: str) -> str:
    """Chave S3 do sprite do filmstrip: clip_NN_base.mp4 -> clip_NN_strip.jpg (e qualquer .mp4)."""
    if base_key.endswith("_base.mp4"):
        return base_key[:-len("_base.mp4")] + "_strip.jpg"
    return re.sub(r"\.mp4$", "_strip.jpg", base_key)


@app.route("/api/clips/<path:clip_id>/filmstrip")
@login_required
def clip_filmstrip(clip_id):
    """Tira do video (sprite de N frames lado a lado) pra a CAMADA DE VIDEO do editor. Gerado no
    SERVIDOR (capturar frames no navegador e bloqueado pelo CORS do S3) e cacheado como sprite no S3.
    Retorna {url: presigned-do-sprite} ou {url: null}. O front estica o sprite na largura da trilha (o
    nº exato de frames nao importa). Reusa o semaforo leve dos posters; ffmpeg niced + 1 thread.
    Best-effort: falha/fila cheia -> {url: null} (a faixa fica neutra). NAO queima legenda."""
    _job, _idx, base_key = _clip_lookup(clip_id)
    strip_key = _strip_key_for(base_key)
    quente = _cache_quente(strip_key)
    if quente:
        return jsonify({"url": s3_client.get_presigned_url(strip_key)})
    if quente is None:
        return jsonify({"url": None})                        # S3 estolado: faixa neutra
    if not _thumb_sem.acquire(timeout=config.THUMB_QUEUE_TIMEOUT):
        return jsonify({"url": None})
    _tk_mid3 = em_voo.entrar("midia", f"filmstrip {clip_id}")
    tmp = None
    try:
        if s3_client.exists(strip_key):                      # outra thread gerou enquanto esperavamos
            return jsonify({"url": s3_client.get_presigned_url(strip_key)})
        url = s3_client.get_presigned_url(base_key)
        # duracao pra espacar N frames uniformes: a janela persistida (sem custo), senao ffprobe.
        dur = None
        win = db.get_clip_window(clip_id)
        if win and win.get("window_dur"):
            dur = float(win["window_dur"])
        if not dur or dur <= 0:
            pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=nk=1:nw=1", url],
                                capture_output=True, text=True, timeout=30)
            try:
                dur = float(pr.stdout.strip())
            except ValueError:
                dur = 0.0
        if not dur or dur <= 0:
            return jsonify({"url": None})
        tmp = tempfile.mkdtemp(prefix="strip_")
        out = os.path.join(tmp, "strip.jpg")
        vf = f"fps={_STRIP_COLS}/{dur:.3f},scale=80:-2,tile={_STRIP_COLS}x1"
        r = subprocess.run(["ffmpeg", "-y", "-i", url, "-frames:v", "1", "-vf", vf, "-q:v", "5",
                            "-threads", "1", out],
                           capture_output=True, text=True, timeout=_STRIP_TIMEOUT,
                           preexec_fn=lambda: os.nice(config.BURN_NICE))
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) >= 256:
            s3_client.upload_file(out, strip_key)
            return jsonify({"url": s3_client.get_presigned_url(strip_key)})
        log.warning("[filmstrip] clip %s: ffmpeg falhou (%s)", clip_id, r.stderr[-200:])
        return jsonify({"url": None})
    except Exception as e:
        log.warning("[filmstrip] clip %s: %s", clip_id, e)
        return jsonify({"url": None})
    finally:
        em_voo.sair(_tk_mid3)
        _thumb_sem.release()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


# ─── Legenda editável do clipe (correção pelo usuário; base do mini editor) ───
_MAX_SEGMENTS = 1000
_MAX_TEXT_LEN = 300
# Duração mínima do corte (in/out) ajustado no editor. Evita salvar um corte degenerado.
_MIN_CUT_SECONDS = 1.0


def _clip_lookup(clip_id):
    """Resolve clip_id "<job_id>:<idx>" -> (job, idx, base_key). Biblioteca compartilhada:
    qualquer usuário ativo acessa/edita qualquer clipe. Aborta 404 se o formato, o job ou
    o índice não baterem."""
    job_id, _, idx_s = clip_id.rpartition(":")
    try:
        idx = int(idx_s)
    except ValueError:
        abort(404)
    if not job_id:
        abort(404)
    job = db.get_job(job_id)
    if not job:
        abort(404)
    try:
        items = json.loads(job.get("result_urls") or "[]")
    except (TypeError, ValueError):
        items = []
    if not (0 <= idx < len(items)):
        abort(404)
    item = items[idx]
    base_key = item if isinstance(item, str) else item.get("key")
    if not base_key:
        abort(404)
    return job, idx, base_key


def _validate_segments(data):
    """Valida/normaliza os segmentos vindos do cliente. Levanta ValueError com
    mensagem amigável se algo estiver fora do formato."""
    if not isinstance(data, list):
        raise ValueError("segments deve ser uma lista.")
    if len(data) > _MAX_SEGMENTS:
        raise ValueError("Segmentos demais.")
    out = []
    for i, s in enumerate(data):
        if not isinstance(s, dict):
            raise ValueError(f"Item {i}: formato inválido.")
        try:
            st = float(s["start"])
            en = float(s["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Item {i}: start/end inválidos.")
        if not (math.isfinite(st) and math.isfinite(en)):
            raise ValueError(f"Item {i}: start/end inválidos.")
        txt = s.get("text", "")
        if not isinstance(txt, str):
            raise ValueError(f"Item {i}: text deve ser texto.")
        if en <= st:
            raise ValueError(f"Item {i}: end deve ser maior que start.")
        st = round(max(st, 0.0), 3)
        en = round(en, 3)
        # Arredondar pra 3 casas pode colapsar start/end muito próximos: rejeita pra
        # não gravar um evento de duração zero (que viraria legenda invisível/quebrada).
        if en <= st:
            raise ValueError(f"Item {i}: intervalo curto demais após arredondar.")
        out.append({"start": st, "end": en, "text": txt.strip()[:_MAX_TEXT_LEN]})
    return out


def _validate_caption_style(data):
    """Limpa/normaliza o estilo da fonte vindo do cliente (size, tracking, color, shadow).
    Retorna um dict só com as chaves válidas (faixas clampadas) ou None se vazio. Levanta
    ValueError com mensagem amigável se um valor presente vier num tipo/formato impossível."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("style inválido.")
    out = {}
    if data.get("size") is not None:
        try:
            v = float(data["size"])
        except (TypeError, ValueError):
            raise ValueError("size inválido.")
        if not math.isfinite(v):
            raise ValueError("size inválido.")
        out["size"] = int(min(160, max(24, v)))
    if data.get("tracking") is not None:
        try:
            v = float(data["tracking"])
        except (TypeError, ValueError):
            raise ValueError("tracking inválido.")
        if not math.isfinite(v):
            raise ValueError("tracking inválido.")
        out["tracking"] = round(min(40.0, max(-20.0, v)), 2)
    if data.get("color") is not None:
        if captions._hex_to_ass(data["color"]) is None:
            raise ValueError("color inválida (use #RRGGBB).")
        out["color"] = "#" + str(data["color"]).strip().lstrip("#").upper()
    if data.get("shadow") is not None:
        try:
            v = float(data["shadow"])
        except (TypeError, ValueError):
            raise ValueError("shadow inválido.")
        if not math.isfinite(v):
            raise ValueError("shadow inválido.")
        out["shadow"] = round(min(1.0, max(0.0, v)), 3)
    return out or None


@app.route("/api/clips/<path:clip_id>/captions", methods=["GET"])
@login_required
def clip_captions_get(clip_id):
    """Devolve os segmentos editáveis do clipe. editable=false pra clipe antigo
    (legenda já fixa, sem base re-queimável) ou sem fala."""
    job, idx, base_key = _clip_lookup(clip_id)
    raw = db.get_clip_captions(clip_id)
    try:
        segs = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        segs = []
    # Testa a lista DECODIFICADA (nao a string crua): o auto-seed de keyframes pode criar a linha com
    # segments='[]' pra clipe sem fala; '[]' e truthy como string e marcaria editavel por engano.
    editable = bool(segs) and "_base.mp4" in base_key
    music = db.get_clip_music(clip_id)
    music_info = None
    if music and music.get("music_key"):
        music_info = {"filename": os.path.basename(music["music_key"]), "volume": music.get("volume")}
    caption_y = db.get_clip_caption_y(clip_id)
    raw_style = db.get_clip_caption_style(clip_id)
    try:
        style_obj = json.loads(raw_style) if raw_style else None
    except (TypeError, ValueError):
        style_obj = None
    # bounds = janela/handles (window_dur + corte in/out, relativos à janela). null = clipe sem
    # handles (legado): o editor trata a base inteira como o clipe (só encurta, não estende).
    window = db.get_clip_window(clip_id)
    bounds = None
    if window and window.get("window_dur"):
        win_dur = float(window["window_dur"])
        bounds = {
            "window_dur": round(win_dur, 3),
            "in": round(max(0.0, float(window.get("in_point") or 0.0)), 3),
            "out": round(float(window["out_point"]) if window.get("out_point") is not None else win_dur, 3),
        }
    # Crop por keyframe: keyframes salvos (mode/kf) ou None (= auto, o base ja tem o enquadramento).
    # crop_editable = clipe novo COM o 16:9 fonte retido (so processado depois da retencao); o front
    # so habilita o editor de crop quando True. O exists() e 1 head_object (captions GET nao e hot).
    raw_kf = db.get_clip_keyframes(clip_id)
    try:
        keyframes = json.loads(raw_kf) if raw_kf else None
    except (TypeError, ValueError):
        keyframes = None
    # Tempo por PALAVRA (clip_words, §26): JSON [{start,end,word}] relativo a janela, gravado no
    # pipeline. SOMENTE LEITURA pro editor (karaoke por palavra no roteiro/overlay); nao entra no
    # save (PUT so aceita start/end/text). null = clipe legado/sem fala -> destaque cai pra segmento.
    raw_words = db.get_clip_words(clip_id)
    try:
        words = json.loads(raw_words) if raw_words else None
    except (TypeError, ValueError):
        words = None
    # crop_editable INDEPENDE de legenda (crop e so pan do 16:9 retido, §33): basta a base ser
    # re-renderizavel (_base.mp4) + o 16:9 existir. Nao acoplar a `editable` (que exige fala), senao
    # um clipe sem legenda mas com 16:9 retido + keyframes auto perderia o editor de crop.
    # `is True`: com o S3 estolado (_cache_quente devolve None) o editor de crop aparece como
    # indisponivel, em vez de a tela inteira de legendas morrer num 500 por causa de um HEAD.
    crop_editable = ("_base.mp4" in base_key) and \
        _cache_quente(_src169_key_for(job["job_id"], idx)) is True
    # Tela final: override do clipe (None/"none"/id), o efetivo após herança, o default do projeto,
    # e a biblioteca de opções (pro editor montar o toggle/seletor sem outra request).
    return jsonify({"segments": segs, "editable": editable, "music": music_info,
                    "caption_y": caption_y, "style": style_obj, "bounds": bounds,
                    "end_screen": db.get_clip_end_screen(clip_id),
                    "end_screen_effective": _resolve_end_screen_id(job, clip_id),
                    "end_screen_dur": db.get_clip_end_screen_dur(clip_id),
                    "project_end_screen": job.get("end_screen"),
                    "end_screens": _end_screen_options(),
                    "keyframes": keyframes, "crop_editable": crop_editable,
                    "words": words})


@app.route("/api/clips/<path:clip_id>/captions", methods=["PUT"])
@login_required
def clip_captions_put(clip_id):
    """Salva a legenda corrigida e invalida o cache (_cap.mp4) pra a próxima
    abertura re-queimar com o texto novo."""
    job, idx, base_key = _clip_lookup(clip_id)
    if "_base.mp4" not in base_key:
        return jsonify({"error": "Este clipe não é editável (legenda já fixa no vídeo)."}), 409
    data = request.get_json(silent=True) or {}
    try:
        segs = _validate_segments(data.get("segments"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    db.set_clip_captions(clip_id, json.dumps(segs, ensure_ascii=False))
    if "caption_y" in data:
        cy = data.get("caption_y")
        if cy is None:
            db.set_clip_caption_y(clip_id, None)   # volta ao padrão do estilo SB
        else:
            try:
                cyf = float(cy)
            except (TypeError, ValueError):
                return jsonify({"error": "caption_y inválido."}), 400
            if not math.isfinite(cyf):
                return jsonify({"error": "caption_y inválido."}), 400
            db.set_clip_caption_y(clip_id, max(0.05, min(0.95, cyf)))
    if "style" in data:
        try:
            st = _validate_caption_style(data.get("style"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        db.set_clip_caption_style(clip_id, json.dumps(st, ensure_ascii=False) if st else None)
    if "in" in data or "out" in data:
        # Reposiciona o corte (handles) dentro da janela. Só faz sentido pra clipe com janela
        # persistida; clampa a [0, window_dur] e exige uma duração mínima.
        window = db.get_clip_window(clip_id)
        if not window or not window.get("window_dur"):
            return jsonify({"error": "Este clipe não tem margem (handles) para reposicionar o corte."}), 409
        win_dur = float(window["window_dur"])
        try:
            in_pt = float(data.get("in"))
            out_pt = float(data.get("out"))
        except (TypeError, ValueError):
            return jsonify({"error": "in/out inválidos."}), 400
        if not (math.isfinite(in_pt) and math.isfinite(out_pt)):
            return jsonify({"error": "in/out inválidos."}), 400
        in_pt = max(0.0, round(in_pt, 3))
        out_pt = round(min(out_pt, win_dur), 3)
        if out_pt - in_pt < _MIN_CUT_SECONDS:
            return jsonify({"error": f"O corte precisa ter pelo menos {_MIN_CUT_SECONDS:g}s."}), 400
        db.set_clip_inout(clip_id, in_pt, out_pt)
    _invalidate_cap(base_key)   # próxima abertura re-renderiza com o texto/estilo/corte corrigido
    return jsonify({"ok": True, "segments": segs})


# ─── Re-render do crop por keyframe (BACKEND §33) ─────────────────────────────
# Regenera o clip_NN_base.mp4 a partir do 16:9 RETIDO (/src169) com a trajetoria dos keyframes do
# usuario. E um ENCODE completo (pesado): roda em thread DAEMON (nao prende a request) serializado por
# semaforo (1 por vez, protege a CPU como a queima). Status em memoria (gunicorn --workers 1 = 1
# processo; dict compartilhado entre as threads; restart no meio = o front re-dispara). Roda na GPU
# quando RERENDER_GPU_ENABLED (Fase 2b), com fallback CPU sempre ativo. So clipe NOVO com 16:9 retido.
_MAX_KEYFRAMES = 2000
_rerender_sem = threading.BoundedSemaphore(1)
_rerender_lock = threading.Lock()
_rerender_status = {}          # clip_id -> {"state": "rendering|done|error", "error": str|None}


def _validate_keyframes(data):
    """Valida/normaliza os keyframes do crop: {kf:[{t,cx}]}. t>=0 finito; cx em [0,1] (CENTRO
    horizontal, fracao). Ordena por t. Levanta ValueError com msg amigavel."""
    if not isinstance(data, dict):
        raise ValueError("keyframes deve ser um objeto.")
    kf = data.get("kf")
    if not isinstance(kf, list) or not kf:
        raise ValueError("keyframes.kf deve ser uma lista nao vazia.")
    if len(kf) > _MAX_KEYFRAMES:
        raise ValueError("Keyframes demais.")
    out = []
    for k in kf:
        try:
            t = float(k["t"])
            cx = float(k["cx"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Cada keyframe precisa de t e cx numericos.")
        if not (math.isfinite(t) and math.isfinite(cx)):
            raise ValueError("t/cx invalidos.")
        out.append({"t": max(0.0, round(t, 3)), "cx": max(0.0, min(1.0, round(cx, 4)))})
    out.sort(key=lambda p: p["t"])
    return out


def _set_rerender(clip_id, state, error=None):
    with _rerender_lock:
        _rerender_status[clip_id] = {"state": state, "error": error}


def _rerender_gpu(src_key, base_key, kf_list, out_local):
    """Produz o re-render NA GPU (BACKEND §33, Fase 2b): o worker baixa o 16:9 retido (presigned GET),
    re-renderiza com a trajetoria do usuario (sem YOLO) e sobe num key TEMPORARIO (presigned PUT). Baixa
    o temporario pro `out_local` e o apaga. Levanta em qualquer falha (o chamador cai pro CPU). Escreve
    num temporario, NAO na base: a promocao pra base acontece sob _clip_burn_lock no chamador (atomico
    contra a queima)."""
    import runpod_client   # lazy: requests/config fora do hot path do web
    ct = config.REFRAME_PUT_CONTENT_TYPE
    url_expiry = config.REFRAME_GPU_TIMEOUT + 600
    tmp_key = base_key.replace("_base.mp4", "_base.rerender.mp4")   # transitorio; limpo no fim/no delete do job
    get_url = s3_client.get_presigned_url(src_key, expiry_seconds=url_expiry)
    put_url = s3_client.get_presigned_put_url(tmp_key, ct, expiry_seconds=url_expiry)
    rp_id = runpod_client.submit_rerender(get_url, put_url, ct, kf_list, config.REFRAME_GPU_TIMEOUT)
    try:
        runpod_client.poll(rp_id, config.REFRAME_GPU_TIMEOUT)
        s3_client.download_file(tmp_key, out_local)
    finally:
        try:
            s3_client.delete_object(tmp_key)
        except Exception:
            pass


def _do_rerender(clip_id, base_key, src_key):
    """Worker do re-render (thread daemon). Sob _rerender_sem (1 por vez): LE os keyframes ATUAIS do DB
    (PUTs concorrentes do mesmo clipe convergem pro ULTIMO salvo, em vez de "vence quem termina") e
    produz a base 9:16 reenquadrada na GPU (se RERENDER_GPU_ENABLED) ou na CPU (fallback sempre ativo).
    Depois, sob _clip_burn_lock(clip_id) (serializa contra a queima do MESMO clipe, senao um _cap.mp4
    feito da base ANTIGA viraria cache quente): sobrescreve a base 9:16 e invalida os derivados
    (cap/trim/*_es + thumb/strip, que derivam da base). NAO mexe na janela/segmentos: mesma duracao/fps,
    so muda o pan, entao a legenda segue valida."""
    tmp = None
    try:
        with _rerender_sem, em_voo.registrar("rerender", f"clip {clip_id}"):
            raw = db.get_clip_keyframes(clip_id)
            try:
                rec = json.loads(raw) if raw else None
            except (TypeError, ValueError):
                rec = None
            kf_list = (rec or {}).get("kf") or []
            if not kf_list:
                raise RuntimeError("sem keyframes pra renderizar")
            tmp = tempfile.mkdtemp(prefix="rerender_")
            out_local = os.path.join(tmp, "base.mp4")
            produced_gpu = False
            if config.RERENDER_GPU_ENABLED:
                try:
                    _rerender_gpu(src_key, base_key, kf_list, out_local)
                    produced_gpu = True
                except Exception as e:
                    log.warning("[rerender] clip %s: GPU falhou (%s); fallback CPU", clip_id, e)
            if not produced_gpu:
                from reframer import reframe_clip_with_keyframes   # lazy: cv2 fora do import do web
                src_local = os.path.join(tmp, "src169.mp4")
                s3_client.download_file(src_key, src_local)
                reframe_clip_with_keyframes(src_local, out_local, kf_list)
            if not os.path.exists(out_local) or os.path.getsize(out_local) < 1024:
                raise RuntimeError("re-render produziu arquivo vazio")
            with _clip_burn_lock(clip_id):              # serializa contra a queima do MESMO clipe
                s3_client.upload_file(out_local, base_key)      # sobrescreve a base 9:16
                _invalidate_cap(base_key)                        # cap/trim/*_es
                for k in (_thumb_key_for(base_key), _strip_key_for(base_key)):
                    try:
                        s3_client.delete_object(k)               # thumb/strip derivam da base: refazer
                    except Exception:
                        pass
        _set_rerender(clip_id, "done")
        log.info("[rerender] clip %s ok (%s)", clip_id, "gpu" if produced_gpu else "cpu")
    except Exception as e:
        _set_rerender(clip_id, "error", str(e))
        log.warning("[rerender] clip %s falhou: %s", clip_id, e)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


@app.route("/api/clips/<path:clip_id>/keyframes", methods=["GET"])
@login_required
def clip_keyframes_get(clip_id):
    """Keyframes do crop (mode/kf/auto) + estado do ultimo re-render (rendering/done/error/idle)."""
    _job, _idx, _base_key = _clip_lookup(clip_id)
    raw = db.get_clip_keyframes(clip_id)
    try:
        kf = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        kf = None
    with _rerender_lock:
        st = _rerender_status.get(clip_id)
    return jsonify({"keyframes": kf, "rerender": st or {"state": "idle"}})


@app.route("/api/clips/<path:clip_id>/keyframes", methods=["PUT"])
@login_required
def clip_keyframes_put(clip_id):
    """Salva os keyframes do crop e dispara o RE-RENDER do base 9:16 a partir do 16:9 retido (async).
    body: {kf:[{t,cx}]} (modo manual) OU {reset:true} (volta ao enquadramento AUTO semeado). Preserva
    o seed AUTO pro toggle reverter sem perder a trajetoria da maquina. Exige clipe editavel COM 16:9
    retido. Responde 202 'rendering'; o front faz poll no GET /keyframes ate done/error."""
    job, idx, base_key = _clip_lookup(clip_id)
    if "_base.mp4" not in base_key:
        return jsonify({"error": "Este clipe nao e editavel (sem base re-renderizavel)."}), 409
    src_key = _src169_key_for(job["job_id"], idx)
    quente = _cache_quente(src_key)
    if quente is None:
        # S3 estolado: NAO dizer "clipe antigo" (seria mentira e o usuario apagaria o trabalho
        # dele achando que nao da pra editar). 503 = tente de novo em instantes.
        return jsonify({"error": "Armazenamento instavel agora. Tente salvar de novo em instantes."}), 503
    if not quente:
        return jsonify({"error": "Crop indisponivel: este clipe nao tem o 16:9 retido (clipe antigo)."}), 409
    data = request.get_json(silent=True) or {}
    # auto seed preservado (pro toggle Auto/Manual reverter sem perder a trajetoria da maquina)
    try:
        existing = json.loads(db.get_clip_keyframes(clip_id) or "null")
    except (TypeError, ValueError):
        existing = None
    auto_kf = None
    if isinstance(existing, dict):
        auto_kf = existing.get("auto") or (existing.get("kf") if existing.get("mode") == "auto" else None)
    if data.get("reset"):
        if not auto_kf:
            return jsonify({"error": "Sem trajetoria automatica pra restaurar."}), 409
        record = {"mode": "auto", "kf": auto_kf, "auto": auto_kf}
    else:
        try:
            manual_kf = _validate_keyframes(data)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        record = {"mode": "manual", "kf": manual_kf, "auto": auto_kf}
    db.set_clip_keyframes(clip_id, json.dumps(record, ensure_ascii=False))
    _set_rerender(clip_id, "rendering")
    # _do_rerender LE os keyframes do DB (gravados acima), entao PUTs concorrentes convergem pro ultimo.
    threading.Thread(target=_do_rerender, args=(clip_id, base_key, src_key), daemon=True).start()
    return jsonify({"ok": True, "mode": record["mode"], "rerender": {"state": "rendering"}}), 202


@app.route("/api/end_screens")
@login_required
def end_screens_list():
    """Biblioteca de telas finais disponíveis (id + rótulo), pro front montar os seletores."""
    return jsonify({"end_screens": _end_screen_options()})


@app.route("/api/clips/<path:clip_id>/end_screen", methods=["PUT"])
@login_required
def clip_end_screen_put(clip_id):
    """Override da tela final do clipe: body {end_screen: null|"none"|"<id>", dur: número|null}.
    end_screen: null herda o projeto, "none" remove, "<id>" força. dur (opcional): encurta a tela
    final neste clipe (s; null = inteira; clampado a [0,5s .. duração do asset/teto]). Invalida só os
    finais (_es): NÃO re-queima a legenda. Exige clipe editável (tem base re-renderizável)."""
    job, idx, base_key = _clip_lookup(clip_id)
    if "_base.mp4" not in base_key:
        return jsonify({"error": "Este clipe não é editável."}), 409
    if db.get_clip_captions(clip_id) is None:
        return jsonify({"error": "Este clipe não tem legenda editável."}), 409
    data = request.get_json(silent=True) or {}
    if "end_screen" in data:
        val = data.get("end_screen")
        if val is not None and val != "none" and not db.get_end_screen(val):
            return jsonify({"error": "Tela final inválida."}), 400
        db.set_clip_end_screen(clip_id, val)
    if "dur" in data:
        d = data.get("dur")
        if d is None:
            db.set_clip_end_screen_dur(clip_id, None)
        else:
            try:
                df = float(d)
            except (TypeError, ValueError):
                return jsonify({"error": "dur inválida."}), 400
            if not math.isfinite(df) or df <= 0:
                return jsonify({"error": "dur inválida."}), 400
            cap = config.END_SCREEN_MAX_SECONDS
            eff = _resolve_end_screen_id(job, clip_id)
            if eff:
                es = db.get_end_screen(eff)
                if es and es.get("duration"):
                    cap = min(cap, float(es["duration"]))
            db.set_clip_end_screen_dur(clip_id, round(max(0.5, min(df, cap)), 3))
    _invalidate_endscreen(base_key)
    return jsonify({"ok": True, "end_screen": db.get_clip_end_screen(clip_id),
                    "end_screen_effective": _resolve_end_screen_id(job, clip_id),
                    "end_screen_dur": db.get_clip_end_screen_dur(clip_id)})


@app.route("/api/end_screens", methods=["POST"])
@login_required
def end_screen_upload():
    """Sobe uma tela final (multipart 'file', mp4 ate END_SCREEN_MAX_SECONDS) pra a biblioteca. Valida
    com ffprobe (tem video + duracao no teto). Sobe pro S3 e grava no DB. Retorna a opcao nova.
    Biblioteca compartilhada: qualquer usuario ativo sobe."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Envie um arquivo de vídeo (campo 'file')."}), 400
    if os.path.splitext(f.filename)[1].lower() not in (".mp4", ".mov", ".m4v"):
        return jsonify({"error": "Formato inválido. Envie um .mp4."}), 400
    label = (request.form.get("label") or os.path.splitext(f.filename)[0]).strip()[:60] or "Tela final"
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix="es_up_")
        local = os.path.join(tmp, "src" + os.path.splitext(f.filename)[1].lower())
        f.save(local)
        size = os.path.getsize(local)
        if size < 1024 or size > config.END_SCREEN_MAX_BYTES:
            return jsonify({"error": "Arquivo vazio ou grande demais."}), 400
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=codec_type:format=duration", "-of", "default=nk=1:nw=1", local],
                            capture_output=True, text=True, timeout=30)
        parts = pr.stdout.split()
        if pr.returncode != 0 or "video" not in pr.stdout:
            return jsonify({"error": "O arquivo não parece um vídeo válido."}), 400
        try:
            dur = float(parts[-1])
        except (ValueError, IndexError):
            return jsonify({"error": "Não consegui ler a duração do vídeo."}), 400
        if dur <= 0 or dur > config.END_SCREEN_MAX_SECONDS + 0.5:
            return jsonify({"error": f"A tela final pode ter no máximo {config.END_SCREEN_MAX_SECONDS:g}s."}), 400
        es_id = uuid.uuid4().hex[:12]
        key = config.END_SCREEN_PREFIX + es_id + ".mp4"
        # normaliza pra mp4 h264/aac yuv420p (garante que o casamento do outro funcione depois)
        out = os.path.join(tmp, "es.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-i", local, "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
                            "-threads", str(config.BURN_FFMPEG_THREADS), out],
                           capture_output=True, text=True, timeout=config.END_SCREEN_TIMEOUT,
                           preexec_fn=lambda: os.nice(config.BURN_NICE))
        if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1024:
            log.warning("[endscreen] normalizar upload falhou: %s", r.stderr[-300:])
            return jsonify({"error": "Falha ao processar o vídeo."}), 400
        s3_client.upload_file(out, key)
        db.add_end_screen(es_id, label, key, round(dur, 3))
        return jsonify({"ok": True, "end_screen": {"id": es_id, "label": label, "duration": round(dur, 3)}})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "O vídeo demorou demais pra processar."}), 400
    except Exception as e:
        log.warning("[endscreen] upload falhou: %s", e)
        return jsonify({"error": "Falha no upload."}), 500
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


@app.route("/api/end_screens/<es_id>", methods=["DELETE"])
# admin, igual ao DELETE de vinhetas: as duas são bibliotecas de asset GLOBAL, e a assimetria
# não tinha justificativa. Aqui não há lixeira nem versionamento: um DELETE apaga o objeto no
# S3 e todos os _matched cacheados, e todo projeto que referenciava degrada em silêncio pra
# "sem tela final" (o _resolve_end_screen_id ignora id inexistente).
@admin_required
def end_screen_delete(es_id):
    """Remove uma tela final da biblioteca (DB + asset no S3 + matched cacheados). Clipes/projetos que
    a referenciavam degradam pra 'sem tela final' (o _resolve ignora id inexistente). Idempotente."""
    es = db.get_end_screen(es_id)
    db.delete_end_screen(es_id)
    if es and es.get("s3_key"):
        try:
            s3_client.delete_object(es["s3_key"])
        except Exception as e:
            log.warning("[endscreen] falha ao apagar asset %s: %s", es["s3_key"], e)
    try:
        s3_client.delete_prefix(config.END_SCREEN_PREFIX + f"_matched/{es_id}_")
    except Exception as e:
        log.warning("[endscreen] falha ao limpar matched de %s: %s", es_id, e)
    return jsonify({"ok": True})


# ─── Vinhetas (biblioteca do pipeline de podcast, §39) ────────────────────────

def _vinheta_options():
    """Biblioteca de vinhetas pro seletor do front (id + rótulo + duração). Lê do DB."""
    return [{"id": v["id"], "label": v.get("label", v["id"]), "duration": v.get("duration")}
            for v in db.list_vinhetas()]


@app.route("/api/vinhetas")
@login_required
def vinhetas_list():
    if not config.PODCAST_ENABLED:
        return jsonify({"error": "Recurso indisponível."}), 404
    return jsonify({"vinhetas": _vinheta_options()})


@app.route("/api/vinhetas", methods=["POST"])
@admin_required
def vinheta_upload():
    """Sobe uma vinheta (multipart 'file') pra biblioteca do podcast. Espelha o upload de
    end_screens; o gate diverge de propósito (admin_required): vinheta é ativo de marca,
    curadoria fica com o admin."""
    if not config.PODCAST_ENABLED:
        return jsonify({"error": "Recurso indisponível."}), 404
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Envie um arquivo de vídeo (campo 'file')."}), 400
    if os.path.splitext(f.filename)[1].lower() not in (".mp4", ".mov", ".m4v"):
        return jsonify({"error": "Formato inválido. Envie um .mp4."}), 400
    label = (request.form.get("label") or os.path.splitext(f.filename)[0]).strip()[:60] or "Vinheta"
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix="vin_up_")
        local = os.path.join(tmp, "src" + os.path.splitext(f.filename)[1].lower())
        f.save(local)
        size = os.path.getsize(local)
        if size < 1024 or size > config.VINHETA_MAX_BYTES:
            return jsonify({"error": "Arquivo vazio ou grande demais."}), 400
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=codec_type:format=duration", "-of", "default=nk=1:nw=1", local],
                            capture_output=True, text=True, timeout=30)
        parts = pr.stdout.split()
        if pr.returncode != 0 or "video" not in pr.stdout:
            return jsonify({"error": "O arquivo não parece um vídeo válido."}), 400
        try:
            dur = float(parts[-1])
        except (ValueError, IndexError):
            return jsonify({"error": "Não consegui ler a duração do vídeo."}), 400
        if dur <= 0 or dur > config.VINHETA_MAX_SECONDS + 0.5:
            return jsonify({"error": f"A vinheta pode ter no máximo {config.VINHETA_MAX_SECONDS:g}s."}), 400
        v_id = uuid.uuid4().hex[:12]
        key = config.VINHETA_PREFIX + v_id + ".mp4"
        # normaliza pra h264/aac yuv420p (mesma receita das telas finais). O casamento fino
        # com os params do podcast acontece na montagem, que re-encoda a vinheta de novo;
        # vinheta sem áudio segue sem áudio aqui (o anullsrc entra na montagem).
        out = os.path.join(tmp, "vin.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-i", local, "-c:v", "libx264", "-preset", "veryfast",
                            "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
                            "-threads", str(config.BURN_FFMPEG_THREADS), out],
                           capture_output=True, text=True, timeout=config.END_SCREEN_TIMEOUT,
                           preexec_fn=lambda: os.nice(config.BURN_NICE))
        if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1024:
            log.warning("[vinheta] normalizar upload falhou: %s", r.stderr[-300:])
            return jsonify({"error": "Falha ao processar o vídeo."}), 400
        s3_client.upload_file(out, key)
        db.add_vinheta(v_id, label, key, round(dur, 3))
        return jsonify({"ok": True, "vinheta": {"id": v_id, "label": label, "duration": round(dur, 3)}})
    except subprocess.TimeoutExpired:
        return jsonify({"error": "O vídeo demorou demais pra processar."}), 400
    except Exception as e:
        log.warning("[vinheta] upload falhou: %s", e)
        return jsonify({"error": "Falha no upload."}), 500
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


@app.route("/api/vinhetas/<v_id>", methods=["DELETE"])
@admin_required
def vinheta_delete(v_id):
    """Remove uma vinheta da biblioteca (DB + asset no S3). Jobs que a referenciavam
    degradam pra 'sem vinheta' (id inexistente é ignorado na montagem). Idempotente."""
    if not config.PODCAST_ENABLED:
        return jsonify({"error": "Recurso indisponível."}), 404
    v = db.get_vinheta(v_id)
    db.delete_vinheta(v_id)
    if v and v.get("s3_key"):
        try:
            s3_client.delete_object(v["s3_key"])
        except Exception as e:
            log.warning("[vinheta] falha ao apagar asset %s: %s", v["s3_key"], e)
    # Os _matched/ dela também: são re-encodes cacheados por ASSINATURA de vídeo
    # (podcast_compose._sig_tag), então há UM por combinação de fps/resolução/profile já
    # montada com esta vinheta. É AQUI que eles morrem, e não no delete de projeto: a key não
    # tem job nenhum, é cache compartilhado entre projetos, e apagar no delete de projeto
    # quebraria a montagem dos outros. Mesmo padrão do end_screen_delete logo acima.
    try:
        n = s3_client.delete_prefix(config.VINHETA_PREFIX + f"_matched/{v_id}_")
        if n:
            log.info("[vinheta] %d matched(s) da vinheta %s removidos", n, v_id)
    except Exception as e:
        log.warning("[vinheta] falha ao limpar matched de %s: %s", v_id, e)
    return jsonify({"ok": True})


# ─── Glossário do revisor de legendas ─────────────────────────────────────────
# Biblioteca compartilhada de nomes canônicos (marcas/produtos/pessoas). Qualquer usuário
# ativo adiciona/edita; só admin exclui. O glossário entra no prompt de revisão.

@app.route("/api/glossary", methods=["GET"])
@login_required
def glossary_list():
    return jsonify({"terms": db.list_glossary()})


@app.route("/api/glossary", methods=["POST"])
@login_required
def glossary_create():
    data = request.get_json(silent=True) or {}
    term = (data.get("term") or "").strip()
    if not term:
        return jsonify({"error": "Informe o termo."}), 400
    if len(term) > 120:
        return jsonify({"error": "Termo longo demais (máx. 120)."}), 400
    category = (data.get("category") or "").strip()[:40] or None
    notes = (data.get("notes") or "").strip()[:500] or None
    term_id = uuid.uuid4().hex[:12]
    try:
        db.add_glossary(term_id, term, category, notes, created_by=session["user"])
    except sqlite3.IntegrityError:
        return jsonify({"error": "Esse termo já existe no glossário."}), 409
    return jsonify({"ok": True, "term": db.get_glossary(term_id)}), 201


@app.route("/api/glossary/<term_id>", methods=["PUT"])
# admin, igual ao DELETE logo abaixo. Sem isto o controle do DELETE era decorativo: esta rota
# chama db.add_glossary, que é UPSERT (ON CONFLICT DO UPDATE), então qualquer usuário
# sobrescrevia o conteúdo de qualquer termo, com o mesmo efeito prático de apagar. O servidor
# bloqueava o VERBO e não o EFEITO, e o front escondia só o botão de excluir.
@admin_required
def glossary_update(term_id):
    existing = db.get_glossary(term_id)
    if not existing:
        return jsonify({"error": "Termo não encontrado."}), 404
    data = request.get_json(silent=True) or {}
    term = (data.get("term") or "").strip()
    if not term:
        return jsonify({"error": "Informe o termo."}), 400
    if len(term) > 120:
        return jsonify({"error": "Termo longo demais (máx. 120)."}), 400
    category = (data.get("category") or "").strip()[:40] or None
    notes = (data.get("notes") or "").strip()[:500] or None
    try:
        db.add_glossary(term_id, term, category, notes, created_by=existing.get("created_by"))
    except sqlite3.IntegrityError:
        return jsonify({"error": "Já existe outro termo com esse nome."}), 409
    return jsonify({"ok": True, "term": db.get_glossary(term_id)})


@app.route("/api/glossary/<term_id>", methods=["DELETE"])
@admin_required
def glossary_delete(term_id):
    """Exclusão restrita a admin (o resto do CRUD é liberado a usuário ativo)."""
    db.delete_glossary(term_id)
    log.info("[glossary] termo %s excluído por %s", term_id, session.get("user"))
    return jsonify({"ok": True})


# ─── Revisor de legendas (upload -> frames -> leitura -> revisão -> relatório) ─
# Job assíncrono em lane própria (CAPTION_REVIEW_CONCURRENCY), independente da fila de
# clipes. Mídia efêmera: o pipeline apaga JOB_DIR/<id>/ no fim.

def _probe_duration(path: str) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None


@app.route("/api/caption-review", methods=["POST"])
@login_required
def caption_review_create():
    if not config.CAPTION_REVIEW_ENABLED:
        return jsonify({"error": "revisor desativado"}), 404
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    if user["must_change_password"]:
        return jsonify({"error": "Troque a senha do primeiro acesso antes de revisar."}), 403

    file = request.files.get("video")
    if not file or not file.filename or not _allowed(file.filename):
        return jsonify({"error": "Arquivo inválido. Use mp4, mov, avi, mkv ou webm."}), 400

    review_id = uuid.uuid4().hex
    job_dir = os.path.join(config.JOB_DIR, review_id)
    ext = os.path.splitext(file.filename)[1].lower()
    local_path = os.path.join(job_dir, f"original{ext}")
    try:
        os.makedirs(job_dir, exist_ok=True)
        file.save(local_path)
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.warning("[caption_review] falha ao salvar upload: %s", e)
        return jsonify({"error": "Falha ao salvar o vídeo."}), 500

    # Valida tamanho + duração ANTES de reservar a vaga (não segura slot com lixo).
    size = os.path.getsize(local_path)
    if size < 1024 or size > config.CAPTION_REVIEW_MAX_BYTES:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": "Arquivo vazio ou grande demais."}), 400
    dur = _probe_duration(local_path)
    if dur is None or dur <= 0:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": "Não parece um vídeo válido."}), 400
    if dur > config.CAPTION_REVIEW_MAX_DURATION:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": f"Vídeo longo demais (máx. {config.CAPTION_REVIEW_MAX_DURATION}s)."}), 400

    # Admissão atômica na lane de revisões (separada do _upload_lock dos clipes).
    with _review_lock:
        if db.count_active_caption_reviews() >= config.CAPTION_REVIEW_CONCURRENCY:
            shutil.rmtree(job_dir, ignore_errors=True)
            return jsonify({
                "error": "Já há revisões em andamento. Aguarde uma terminar antes de enviar outra :)"
            }), 429
        db.create_caption_review(review_id, file.filename, session["user"], duration=round(dur, 2))

    threading.Thread(target=caption_reviewer.run_review, args=(review_id, local_path), daemon=True).start()
    return jsonify({"review_id": review_id}), 202


def _caption_review_payload(row: dict) -> dict:
    """Monta a resposta de status/resultado a partir da linha do DB."""
    resp = {
        "id": row["id"], "status": row["status"], "progress": row["progress"],
        "filename": row["filename"], "duration": row.get("duration"),
        "frames_count": row.get("frames_count"), "cost_usd": row.get("cost_usd"),
        "created_at": row.get("created_at"),
    }
    if row["status"] == "done":
        resp["legenda_fala"] = row.get("legenda_fala") or ""
        resp["graficos"] = json.loads(row.get("graficos_json") or "[]")
        resp["findings"] = json.loads(row.get("report_json") or "[]")
        resp["tokens"] = {
            "read_in": row.get("read_in_tok"), "read_out": row.get("read_out_tok"),
            "review_in": row.get("review_in_tok"), "review_out": row.get("review_out_tok"),
        }
    if row["status"] == "error":
        resp["error"] = row.get("error")
    return resp


@app.route("/api/caption-review/<review_id>", methods=["GET"])
@login_required
def caption_review_status(review_id):
    if not config.CAPTION_REVIEW_ENABLED:
        return jsonify({"error": "revisor desativado"}), 404
    row = db.get_caption_review(review_id)
    if not row:
        return jsonify({"error": "Revisão não encontrada."}), 404
    user = db.get_user(session["user"])
    if row.get("user_email") != session["user"] and not (user and user.get("is_admin")):
        return jsonify({"error": "Revisão não encontrada."}), 404
    return jsonify(_caption_review_payload(row))


@app.route("/api/caption-review", methods=["GET"])
@login_required
def caption_review_list():
    if not config.CAPTION_REVIEW_ENABLED:
        return jsonify({"error": "revisor desativado"}), 404
    return jsonify({"reviews": db.list_caption_reviews(session["user"])})


# ─── Música de fundo do clipe (mixada na queima sob demanda) ──────────────────
_MUSIC_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
_MAX_MUSIC_BYTES = 25 * 1024 * 1024
_DEFAULT_MUSIC_VOL = 0.15


def _has_audio_stream(path: str) -> bool:
    """Confirma com ffprobe que o arquivo tem trilha de áudio de verdade (a extensão
    sozinha não garante). Evita guardar lixo que faria a queima sob demanda falhar."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode == 0 and "audio" in (r.stdout or "")


@app.route("/api/clips/<path:clip_id>/music", methods=["POST"])
@login_required
def clip_music_upload(clip_id):
    """Sobe um arquivo de música pro clipe (multipart, campo 'music') e invalida o
    cache. A música é mixada de fundo, em loop, na próxima render."""
    job, idx, base_key = _clip_lookup(clip_id)
    if "_base.mp4" not in base_key:
        return jsonify({"error": "Este clipe não é editável."}), 409
    f = request.files.get("music")
    if not f or not f.filename:
        return jsonify({"error": "Arquivo de música ausente."}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in _MUSIC_EXTS:
        return jsonify({"error": "Formato inválido. Use mp3, m4a, aac, wav ou ogg."}), 400
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > _MAX_MUSIC_BYTES:
        return jsonify({"error": "Música muito grande (máx 25 MB)."}), 400
    try:
        vol = float(request.form.get("volume", _DEFAULT_MUSIC_VOL))
    except (TypeError, ValueError):
        vol = _DEFAULT_MUSIC_VOL
    vol = max(0.0, min(vol, 1.0))
    job_id = clip_id.rpartition(":")[0]
    music_key = f"jobs/{job_id}/music/clip_{idx + 1:02d}{ext}"
    tmp = tempfile.mkdtemp(prefix="music_")
    try:
        local = os.path.join(tmp, "track" + ext)
        f.save(local)
        if not _has_audio_stream(local):
            return jsonify({"error": "O arquivo enviado não parece ser um áudio válido."}), 400
        s3_client.upload_file(local, music_key)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    db.set_clip_music(clip_id, music_key, vol)
    _invalidate_cap(base_key)
    return jsonify({"ok": True, "music": {"filename": os.path.basename(music_key), "volume": vol}})


@app.route("/api/clips/<path:clip_id>/music", methods=["PUT"])
@login_required
def clip_music_volume(clip_id):
    """Ajusta só o volume da música do clipe (JSON {volume: 0..1}) e invalida o cache."""
    job, idx, base_key = _clip_lookup(clip_id)
    music = db.get_clip_music(clip_id)
    if not (music and music.get("music_key")):
        return jsonify({"error": "Sem música nesse clipe."}), 404
    data = request.get_json(silent=True) or {}
    try:
        vol = float(data.get("volume"))
    except (TypeError, ValueError):
        return jsonify({"error": "volume inválido."}), 400
    vol = max(0.0, min(vol, 1.0))
    db.set_clip_music(clip_id, music["music_key"], vol)
    _invalidate_cap(base_key)
    return jsonify({"ok": True, "volume": vol})


@app.route("/api/clips/<path:clip_id>/music", methods=["DELETE"])
@login_required
def clip_music_remove(clip_id):
    """Remove a música do clipe (apaga do S3 + do banco) e invalida o cache."""
    job, idx, base_key = _clip_lookup(clip_id)
    music = db.get_clip_music(clip_id)
    if music and music.get("music_key"):
        try:
            s3_client.delete_object(music["music_key"])
        except Exception as e:
            log.warning("[music] falha ao apagar %s: %s", music["music_key"], e)
    db.delete_clip_music(clip_id)
    _invalidate_cap(base_key)
    return jsonify({"ok": True})


@app.route("/api/clips/<path:clip_id>/music/audio")
@login_required
def clip_music_audio(clip_id):
    """Entrega o ARQUIVO de música do clipe (302 pra URL presignada do S3), pra o preview AO VIVO do
    editor tocar a música em paralelo ao vídeo base (sem re-queimar). 404 se o clipe não tem música."""
    _clip_lookup(clip_id)  # valida formato/job/sessão (aborta 404 se inválido)
    music = db.get_clip_music(clip_id)
    if not music or not music.get("music_key"):
        abort(404)
    return _s3_redirect(music["music_key"])


def _can_manage(job) -> bool:
    """Quem pode destruir um projeto na biblioteca compartilhada: o DONO (quem subiu) ou
    um admin. Os demais usuários acessam e editam, mas não excluem/cancelam. Job legado
    (dono NULL) não tem quem reivindicar, então só admin gerencia."""
    user = db.get_user(session["user"]) or {}
    if user.get("is_admin"):
        return True
    owner = job.get("user_email")
    return bool(owner) and owner == session["user"]


def _apagar_armazenamento_do_job(job_id: str) -> int:
    """Apaga TUDO que o job ocupa fora do banco: os dois prefixos do S3 e o diretório local.
    Devolve quantos objetos saíram do S3. PROPAGA falha do S3 de propósito (quem chama decide),
    e o disco é best-effort.

    Teto MENOR aqui (config.S3_DELETE_TIMEOUT): roda síncrono dentro do request e segura uma
    das 4 threads do gunicorn até responder.

    IDEMPOTENTE nos três passos, que é o que faz o retry funcionar depois de uma falha no meio:
    delete_prefix em prefixo já vazio devolve 0 sem chamar delete_objects, e rmtree com
    ignore_errors não liga pra diretório inexistente.

    O caminho local é montado do JOB_DIR + job_id, NUNCA de job["local_path"]: em job legado
    esse campo pode apontar pra fora do JOB_DIR, e aqui a operação é destrutiva."""
    # Guarda de forma antes de qualquer destruição. Hoje é redundante em três camadas (o
    # converter <job_id> do Flask é [^/]+, todo job_id do banco é uuid4 do servidor, e a rota
    # só chega aqui depois do get_job achar a linha), mas é a última barreira num caminho que
    # faz rmtree: no dia em que alguém aceitar id vindo do cliente (importação, seed) ou trocar
    # pra <path:job_id>, esta linha é o que impede um "../" de escapar do JOB_DIR.
    if not job_id or job_id != os.path.basename(job_id):
        raise ValueError(f"job_id com formato inesperado: {job_id!r}")
    t = config.S3_DELETE_TIMEOUT
    n = s3_client.delete_prefix(f"jobs/{job_id}/", timeout=t)
    n += s3_client.delete_prefix(f"{config.SOURCE16X9_PREFIX}{job_id}/", timeout=t)  # 16:9 retido
    # O disco segue best-effort (ignore_errors): resíduo local é barato perto do bucket pago e
    # não deve impedir a exclusão lógica. Mas ele não pode sumir CALADO: como o db.delete_job
    # vem logo depois, um rmtree que falhou de verdade (permissão, arquivo em uso) deixa o
    # diretório sem nenhum ponteiro e ninguém fica sabendo. Visto acontecer no deploy de
    # 2026-07-26: diretório de dono errado, rmtree engoliu o erro e o job sumiu do banco com o
    # arquivo ainda em disco.
    job_dir = os.path.join(config.JOB_DIR, job_id)
    shutil.rmtree(job_dir, ignore_errors=True)
    if os.path.isdir(job_dir):
        log.warning("[delete] job %s: o diretório local %s SOBROU depois do rmtree "
                    "(permissão? arquivo em uso?); o registro será apagado assim mesmo e o "
                    "resíduo fica órfão em disco", job_id, job_dir)
    return n


@app.route("/api/projects/<job_id>", methods=["DELETE"])
@login_required
def delete_project(job_id):
    """Exclui o projeto (clipe OU podcast: a rota é a mesma, sem if por job_type) e tudo que
    ele ocupa. Não é gateada por PODCAST_ENABLED de propósito: podcast órfão precisa ser
    excluível mesmo com o kill switch desligado.

    Reap antes de tudo pelo mesmo motivo das rotas de admissão: sem isso um job com a thread
    morta em 'montando' ficaria travado na checagem abaixo e o projeto viraria INELIMINÁVEL,
    porque o reap não roda em timer."""
    db.reap_stale_jobs(config.JOB_STALE_MINUTES)
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Projeto não encontrado."}), 404
    if not _can_manage(job):
        return jsonify({"error": "Só o dono do projeto ou um admin pode excluir."}), 403
    # Trava POR JOB (db.job_em_andamento), não pelas contagens globais: bloquear pelo total
    # impediria excluir este projeto por causa de trabalho de OUTRO. O _podcast_audio_stale
    # compõe porque a auto-cura do 'extraindo' só roda no GET de polling: sem ele, extração
    # morta por restart deixaria o projeto travado até alguém abrir a tela.
    if db.job_em_andamento(job) and not _podcast_audio_stale(job):
        return jsonify({"error": "Este projeto tem trabalho em andamento. "
                                 "Aguarde terminar (ou cancele) antes de excluir."}), 409
    # S3 -> disco -> BANCO POR ÚLTIMO. A linha do banco é o único ponteiro pro job_id, e o
    # job_id é o que reconstrói os dois prefixos e o caminho local: apagá-la antes tornaria o
    # que sobrou inalcançável (o retry veria 404) e recriaria a categoria "órfão invisível",
    # que é exatamente o que esta ordem existe pra matar. Falha de S3 ABORTA a exclusão e o
    # projeto continua listado, clicável de novo.
    try:
        n = _apagar_armazenamento_do_job(job_id)
    except s3_client.S3StalledError:
        log.warning("[delete] S3 não respondeu ao excluir o job %s; projeto preservado", job_id)
        # Sem prometer "nada foi excluído": o estol pode ter vindo no SEGUNDO prefixo, com o
        # primeiro já apagado. O projeto continua listado (é o que garante o retry), mas
        # parte dos arquivos pode ter saído, e o texto precisa empurrar pro retry em vez de
        # dar o assunto por encerrado.
        return jsonify({"error": "O armazenamento não respondeu e a exclusão não terminou; "
                                 "parte dos arquivos pode já ter saído. O projeto continua "
                                 "aqui: tente de novo em alguns minutos."}), 503
    except Exception as e:
        log.warning("[delete] falha ao limpar o armazenamento do job %s: %s", job_id, e)
        return jsonify({"error": "Não consegui apagar os arquivos deste projeto, então ele "
                                 "foi preservado. Tente de novo; se insistir, avise o time."}), 502
    db.delete_job(job_id)
    log.info("[delete] job %s removido (%d objeto(s) no S3)", job_id, n)
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
@login_required
def cancel_job(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job não encontrado."}), 404
    if not _can_manage(job):
        return jsonify({"error": "Só o dono do projeto ou um admin pode cancelar."}), 403
    if job["status"] in ("done", "error"):
        return jsonify({"ok": True, "already_final": True})
    # Cancel honesto: marca error (libera a vaga do MAX_CONCURRENT na hora). As threads
    # do processor checam isso entre etapas e param; o callback do N8N não retoma o corte.
    # A transcrição já em curso no RunPod não é recolhida (custo já gasto).
    db.update_job(job_id, status="error", error="Cancelado pelo usuário.")
    return jsonify({"ok": True})


_PREF_KEYS = {"defaultMin", "defaultMax", "aspect", "language", "captions", "autoplayPreview"}


def _clean_preferences(data) -> dict:
    """Whitelist + checagem de tipo das prefs: não guardar blob arbitrário do cliente
    e não deixar entrar tipo errado (ex: string onde o front espera número)."""
    out = {}
    if not isinstance(data, dict):
        return out
    for k in ("defaultMin", "defaultMax"):
        if k in data:
            try:
                out[k] = int(data[k])
            except (TypeError, ValueError):
                pass
    for k in ("captions", "autoplayPreview"):
        if k in data:
            out[k] = bool(data[k])
    for k in ("aspect", "language"):
        v = data.get(k)
        if isinstance(v, str):
            out[k] = v[:32]
    return out


@app.route("/api/preferences")
@login_required
def get_preferences():
    raw = db.get_preferences(session["user"])
    try:
        return jsonify(json.loads(raw) if raw else {})
    except (TypeError, ValueError):
        return jsonify({})


@app.route("/api/preferences", methods=["PUT"])
@login_required
def put_preferences():
    data = request.get_json(silent=True) or {}
    clean = _clean_preferences(data)
    db.set_preferences(session["user"], json.dumps(clean))
    return jsonify({"ok": True})


def _pref_captions_default(user_email) -> int:
    """Legenda padrão a partir da preferência salva do usuário (default ligada). O override por
    requisição (form do upload ou JSON do YouTube) é aplicado por cada rota em cima disto."""
    captions_on = 1
    try:
        _p = json.loads(db.get_preferences(user_email) or "{}")
        if isinstance(_p, dict) and "captions" in _p:
            captions_on = 1 if _p["captions"] else 0
    except (TypeError, ValueError):
        pass
    return captions_on


@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    if user["must_change_password"]:
        return jsonify({"error": "Troque a senha do primeiro acesso antes de processar vídeos."}), 403

    # Mesma razão do upload de podcast: recusa depois de um envio longo tem que dizer POR QUE
    # no journal, senão o motivo se perde e sobra só o 400 no log do nginx.
    _tam = request.content_length or 0
    file = request.files.get("video")
    if not file or not file.filename or not _allowed(file.filename):
        log.warning("[upload] recusado (extensão/arquivo): filename=%r tamanho=%.2fGB",
                    getattr(file, "filename", None), _tam / 2**30)
        return jsonify({"error": "Arquivo inválido. Use mp4, mov, avi, mkv ou webm."}), 400

    min_duration = request.form.get("min_duration", type=int)
    max_duration = request.form.get("max_duration", type=int)
    if min_duration is None or max_duration is None or min_duration >= max_duration:
        log.warning("[upload] recusado (duração inválida): min=%r max=%r arquivo=%r",
                    min_duration, max_duration, file.filename)
        return jsonify({
            "error": "min_duration e max_duration são obrigatórios e min deve ser menor que max."
        }), 400
    if not (_MIN_CLIP_SECONDS <= min_duration < max_duration <= _MAX_CLIP_SECONDS):
        return jsonify({
            "error": f"Duração fora do intervalo permitido ({_MIN_CLIP_SECONDS} a {_MAX_CLIP_SECONDS}s)."
        }), 400

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(config.JOB_DIR, job_id)
    ext = os.path.splitext(file.filename)[1].lower()
    local_path = os.path.join(job_dir, f"original{ext}")

    # Legenda queimada: default ligada; respeita a preferência salva do usuário e
    # um override opcional no form do upload (frontend).
    captions_on = _pref_captions_default(session["user"])
    _form_cap = request.form.get("captions")
    if _form_cap is not None:
        captions_on = 1 if _form_cap.strip().lower() in ("1", "true", "on", "yes", "sim") else 0

    # Admissão atômica: reap + check de vaga + reserva, tudo sob o lock, fechando
    # a race onde dois uploads simultâneos passariam o check antes de qualquer um
    # gravar. A vaga é reservada criando o job como 'queued' (conta em count_active).
    with _upload_lock:
        reaped = db.reap_stale_jobs(config.JOB_STALE_MINUTES)
        if reaped:
            log.info("[watchdog] %d job(s) preso(s) liberado(s) por timeout", reaped)
        if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
            return jsonify({
                "error": "Um vídeo já está em processamento! Vai tomar uma água, respirar e aguardar a conclusão antes de enviar novo vídeo :)"
            }), 429
        db.create_job(job_id, file.filename, min_duration, max_duration, local_path, session["user"], captions=captions_on)

    # Fora do lock: salvar o arquivo (grande/lento) e disparar a thread, pra não
    # bloquear a admissão de outros uploads durante o upload em si.
    try:
        os.makedirs(job_dir, exist_ok=True)
        file.save(local_path)
    except Exception as e:
        db.update_job(job_id, status="error", error=f"Falha ao salvar upload: {e}")
        return jsonify({"error": "Falha ao salvar o vídeo."}), 500

    threading.Thread(target=processor.run_thread1, args=(job_id,), daemon=True).start()

    return jsonify({"job_id": job_id}), 202


# ─── Upload de podcast 16:9 (§39: só a ENTRADA; transcrição/gancho nas próximas partes) ─

def _podcast_tem_fonte(job) -> bool:
    """Substitui a guarda "local_path existe?" que as 6 rotas de podcast repetiam. Com upload
    direto o fonte nasce SÓ no S3 e chega ao disco depois, em thread (podcast_ingest.
    garantir_fonte_local): sem isto o upload funcionaria e a tela seguinte daria 409 em tudo."""
    local = job.get("local_path")
    return bool((local and os.path.exists(local)) or job.get("podcast_source_key"))


def _podcast_fonte_para_probe(job):
    """Um caminho que o ffprobe consegue abrir: o arquivo local, ou uma presignada.

    Ler por HTTPS aqui é seguro e barato porque o ffprobe faz RANGE REQUEST e busca só o moov:
    medido em 2026-07-26, 4,6MB e 0,3s num arquivo de 8,12GB. Quem NÃO pode rodar remoto é o
    _podcast_is_cfr, que percorre todos os pacotes e puxaria os 8GB inteiros; por isso ele
    continua só no caminho com arquivo local."""
    local = job.get("local_path")
    if local and os.path.exists(local):
        return local
    key = job.get("podcast_source_key")
    return s3_client.get_presigned_url(key, expiry_seconds=600) if key else None


def _probe_podcast_video(path: str):
    """(width, height, dar) do primeiro stream de vídeo, com SAR aplicado, ou None se
    o arquivo não tiver vídeo legível."""
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                         "stream=width,height,sample_aspect_ratio", "-of", "json", path],
                        capture_output=True, text=True, timeout=60)
    if pr.returncode != 0:
        return None
    try:
        st = json.loads(pr.stdout)["streams"][0]
        w, h = int(st["width"]), int(st["height"])
        sar_n, sar_d = 1, 1
        sar = st.get("sample_aspect_ratio") or ""
        if ":" in sar:
            a, b = sar.split(":", 1)
            if int(a) > 0 and int(b) > 0:
                sar_n, sar_d = int(a), int(b)
        return w, h, (w * sar_n) / (h * sar_d)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None


def _podcast_is_cfr(path: str) -> bool:
    """Detecção de VFR validada empiricamente (§39): histograma dos deltas de DTS de TODOS
    os pacotes de vídeo (só demux, sem decode: ~30s pra 8GB). CFR = um único delta cobre
    >=99,9% dos pacotes e não há delta não-positivo. Propaga TimeoutExpired pro caller."""
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "packet=dts", "-of", "csv=p=0", path],
                        capture_output=True, text=True, timeout=config.PODCAST_PROBE_TIMEOUT)
    if pr.returncode != 0:
        return False
    deltas = {}
    prev = None
    for tok in pr.stdout.split():
        try:
            dts = int(tok)
        except ValueError:
            continue
        if prev is not None:
            d = dts - prev
            deltas[d] = deltas.get(d, 0) + 1
        prev = dts
    total = sum(deltas.values())
    if not total:
        return False
    return max(deltas.values()) / total >= 0.999 and min(deltas) > 0


# ─── Upload DIRETO do navegador pro S3 (multipart presignado) ────────────────
# Quatro rotas finas: a orquestração mora em podcast_ingest.py e as primitivas de S3 em
# s3_client.py, porque app.py já tem quase 4000 linhas. Gateadas por PODCAST_UPLOAD_DIRETO
# (default false): enquanto estiver off, a rota antiga /api/podcast/upload continua sendo a
# única entrada. Combinar data pra remover a antiga, senão vira dívida permanente.

def _guarda_upload_direto():
    """Guardas comuns às 4 rotas. Devolve (user, None) ou (None, resposta_de_erro)."""
    if not config.PODCAST_ENABLED or not config.PODCAST_UPLOAD_DIRETO:
        # O marcador distingue "kill switch desligado" de qualquer OUTRO 404 (deploy fora de
        # ordem, path errado, 404 do proxy). Sem ele o front cairia no caminho antigo em
        # silêncio nos dois casos, e o antigo é justamente o de 21% de sucesso.
        return None, (jsonify({"error": "Recurso indisponível.", "upload_direto": False}), 404)
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return None, (jsonify({"error": "Sessão inválida.", "auth": False}), 401)
    if user["must_change_password"]:
        return None, (jsonify({"error": "Troque a senha do primeiro acesso antes de "
                                        "processar vídeos."}), 403)
    return user, None


def _recusar_e_limpar(key, job_id, motivo):
    """Recusa DEPOIS do objeto já existir no bucket. Diferente do caminho antigo (que recusava
    antes de qualquer arquivo existir), aqui deixar de apagar significa GB órfãos num bucket
    pago que nada ceifa. Best-effort: se o delete falhar, LOGA e segue, porque a resposta de
    recusa é mais importante que a limpeza (e o delete do projeto ainda cobre depois)."""
    try:
        s3_client.delete_object(key)
    except Exception as e:
        log.warning("[podcast/complete] job %s: recusado por %s mas o objeto FICOU no S3 "
                    "(%s): %s", job_id, motivo, key, e)


@app.route("/api/podcast/upload/init", methods=["POST"])
@login_required
def podcast_upload_init():
    """Abre o multipart e devolve o plano de envio. NENHUMA linha é criada no banco aqui: o
    estado da transação viaja num token assinado (podcast_ingest.emitir_token) e o S3 já é o
    registro dos uploads pendentes (list_multipart_uploads enumera, o lifecycle de 7 dias
    coleta). Job no init apareceria na lista de projetos por 75min com todos os botões dando
    409, e seria excluível pelo DELETE por baixo das partes em voo."""
    user, err = _guarda_upload_direto()
    if err:
        return err
    d = request.get_json(silent=True) or {}
    filename = (d.get("filename") or "").strip()
    vinheta_id = (d.get("vinheta_id") or "").strip()
    try:
        tamanho = int(d.get("size") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Tamanho de arquivo inválido."}), 400

    if not filename or not _allowed(filename):
        log.warning("[podcast/init] recusado (extensão): filename=%r", filename)
        return jsonify({"error": "Arquivo inválido. Use mp4, mov, avi, mkv ou webm."}), 400
    if not vinheta_id or not db.get_vinheta(vinheta_id):
        log.warning("[podcast/init] recusado (vinheta): id=%r", vinheta_id)
        return jsonify({"error": "Escolha uma vinheta válida da biblioteca."}), 400
    if tamanho <= 0 or tamanho > config.PODCAST_UPLOAD_MAX_BYTES:
        log.warning("[podcast/init] recusado (tamanho): %.2fGB (teto %.0fGB)",
                    tamanho / 2**30, config.PODCAST_UPLOAD_MAX_BYTES / 2**30)
        return jsonify({"error": f"Arquivo de {tamanho / 2**30:.1f}GB. O limite é "
                                 f"{config.PODCAST_UPLOAD_MAX_BYTES / 2**30:.0f}GB."}), 400
    try:
        part_size, part_count = podcast_ingest.calcular_partes(tamanho)
    except ValueError as e:
        log.warning("[podcast/init] recusado (partes): %s", e)
        return jsonify({"error": str(e)}), 400

    # job_id e key derivados AQUI, no servidor. A key nunca vem do cliente: presignada é
    # credencial ao portador, então aceitar key do cliente deixaria qualquer usuário logado
    # assinar um PUT pra jobs/<outro>/podcast/final.mp4.
    job_id = str(uuid.uuid4())
    ext = os.path.splitext(filename)[1].lower()
    key = f"jobs/{job_id}/podcast/source{ext}"
    try:
        upload_id = s3_client.criar_multipart(key)
    except Exception as e:
        log.warning("[podcast/init] falha ao abrir multipart: %s", e)
        return jsonify({"error": "Não consegui iniciar o envio agora. Tente de novo."}), 503

    token = podcast_ingest.emitir_token({
        "job_id": job_id, "key": key, "upload_id": upload_id, "ext": ext,
        "part_count": part_count,
        # splitext antes do sanitizador: ele NÃO tira extensão, então sanitizar o nome
        # inteiro e concatenar ext produziria "Podcast Carbone 12.mp4.mp4" no card e no
        # nome do download do episódio.
        "filename": _safe_filename(os.path.splitext(filename)[0], fallback="podcast") + ext,
        "vinheta_id": vinheta_id, "size": tamanho,
        "user_email": session["user"], "t0": time.time(),
    })
    log.info("[podcast/init] job %s: %.2fGB em %d parte(s) de %dMiB, conc=%d",
             job_id, tamanho / 2**30, part_count, part_size // 2**20,
             config.PODCAST_UPLOAD_CONCURRENCY)
    return jsonify({
        "token": token, "job_id": job_id,
        "part_size": part_size, "part_count": part_count,
        "concurrency": config.PODCAST_UPLOAD_CONCURRENCY,
        "lote": config.PODCAST_UPLOAD_LOTE,
    }), 201


@app.route("/api/podcast/upload/parts", methods=["POST"])
@login_required
def podcast_upload_parts():
    """Presignadas de um LOTE de partes. Em lotes e não todas de uma vez: 640 URLs dariam
    ~450KB de JSON e obrigariam validade de horas pra uma credencial de escrita."""
    user, err = _guarda_upload_direto()
    if err:
        return err
    d = request.get_json(silent=True) or {}
    try:
        tk = podcast_ingest.ler_token(d.get("token") or "", session["user"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    numeros = d.get("parts") or []
    if not isinstance(numeros, list) or not numeros:
        return jsonify({"error": "Nenhuma parte pedida."}), 400
    if len(numeros) > config.PODCAST_UPLOAD_LOTE:
        # Sem este teto, um POST pede 100 mil URLs e o servidor assina 100 mil HMACs numa
        # das 4 threads do gunicorn.
        return jsonify({"error": f"No máximo {config.PODCAST_UPLOAD_LOTE} partes por vez."}), 400
    try:
        nums = [int(n) for n in numeros]
    except (TypeError, ValueError):
        return jsonify({"error": "Números de parte inválidos."}), 400
    # Contra o part_count DO TOKEN, não contra o 10000 do S3. Sem isto, quem declara 16MiB no
    # init ainda consegue pedir presignadas de 1 a 10000 e, como a presignada não fixa
    # Content-Length, subir 5GiB em cada uma: o teto de tamanho viraria auto-declarado.
    teto = int(tk.get("part_count") or 0) or 10000
    if any(n < 1 or n > teto for n in nums):
        log.warning("[podcast/parts] job %s pediu parte fora da faixa (teto %d): %r",
                    tk.get("job_id"), teto, nums[:5])
        return jsonify({"error": "Números de parte fora da faixa."}), 400

    urls = {str(n): s3_client.presign_parte(tk["key"], tk["upload_id"], n,
                                            config.PODCAST_UPLOAD_URL_TTL)
            for n in nums}
    return jsonify({"urls": urls, "expira_em": config.PODCAST_UPLOAD_URL_TTL})


@app.route("/api/podcast/upload/complete", methods=["POST"])
@login_required
def podcast_upload_complete():
    """Fecha o multipart, valida por range e cria o job. IDEMPOTENTE de propósito: é a
    proteção da única janela que sobra (restart entre o último byte e esta chamada)."""
    user, err = _guarda_upload_direto()
    if err:
        return err
    d = request.get_json(silent=True) or {}
    try:
        tk = podcast_ingest.ler_token(d.get("token") or "", session["user"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    job_id, key = tk["job_id"], tk["key"]

    # Retry de um complete que já deu certo: o job existe e é deste dono. Responde 200 sem
    # refazer nada, que é o que torna o retry do cliente seguro.
    ja = db.get_job(job_id)
    if ja and ja.get("job_type") == "podcast":
        return jsonify({"job_id": job_id, "status": ja["status"]}), 200

    partes = d.get("parts") or []
    try:
        lista = [{"PartNumber": int(p["number"]), "ETag": p["etag"]} for p in partes]
    except (TypeError, KeyError, ValueError):
        return jsonify({"error": "Lista de partes inválida."}), 400

    try:
        s3_client.concluir_multipart(key, tk["upload_id"], lista)
    except ClientError as e:
        codigo = e.response.get("Error", {}).get("Code", "")
        if codigo == "NoSuchUpload":
            # NoSuchUpload é a MESMA resposta pra "já concluí", "abortei" e "expirou". Quem
            # desambigua é o objeto final: se ele está lá com o tamanho certo, o complete
            # anterior funcionou e este é um retry legítimo.
            try:
                tam = s3_client.head_size(key)
            except Exception:
                tam = None
            if tam != tk["size"]:
                # Objeto PRESENTE com tamanho errado é caso diferente de objeto ausente, e
                # sem apagar ele fica no bucket pago sem linha no banco: invisível pro
                # delete_project (que só chega por job_id) e sem nada que o ceife.
                if tam is not None:
                    _recusar_e_limpar(key, job_id, "objeto montado com tamanho divergente")
                log.warning("[podcast/complete] job %s: uploadId sumiu (tam=%r, esperado=%r)",
                            job_id, tam, tk["size"])
                return jsonify({"error": "Este envio não pôde ser concluído. "
                                         "Envie o arquivo de novo."}), 409
            log.info("[podcast/complete] job %s: complete repetido, objeto já montado", job_id)
        elif codigo in ("InvalidPart", "InvalidPartOrder", "EntityTooSmall"):
            log.warning("[podcast/complete] job %s recusado (%s)", job_id, codigo)
            return jsonify({"error": "Algumas partes do envio não chegaram inteiras. "
                                     "Tente enviar de novo."}), 400
        else:
            log.warning("[podcast/complete] job %s: erro do S3 (%s)", job_id, codigo)
            return jsonify({"error": "Falha ao finalizar o envio. Tente de novo."}), 502
    except s3_client.S3StalledError:
        # O objeto PODE ter sido montado mesmo assim; o retry cai no ramo NoSuchUpload acima.
        log.warning("[podcast/complete] job %s: S3 não respondeu ao finalizar", job_id)
        return jsonify({"error": "O armazenamento não respondeu. Tente de novo em "
                                 "instantes; o que já subiu não se perdeu."}), 503

    # O tamanho declarado no init vira VERIFICADO aqui, contra o objeto montado. Sem isto o
    # teto de PODCAST_UPLOAD_MAX_BYTES é só um número que o cliente escolheu: ele declara
    # 16MiB, sobe partes de 5GiB (a presignada não fixa Content-Length) e o complete aceita.
    # Tolerância zero de propósito: o cliente sabe o tamanho exato do arquivo que escolheu.
    try:
        real = s3_client.head_size(key)
    except Exception as e:
        log.warning("[podcast/complete] job %s: não consegui conferir o tamanho (%s)", job_id, e)
        real = None
    if real is not None and real != tk["size"]:
        _recusar_e_limpar(key, job_id, "tamanho divergente")
        log.warning("[podcast/complete] job %s recusado: declarou %d bytes e montou %d",
                    job_id, tk["size"], real)
        return jsonify({"error": "O arquivo enviado não bate com o que foi anunciado. "
                                 "Envie de novo."}), 400

    # Validação por RANGE REQUEST: ffprobe lê só o moov. Medido em 2026-07-26: 4,6MB e 0,3s
    # num arquivo de 8,12GB. O CFR NÃO é validado aqui de propósito (leria o arquivo inteiro,
    # 8GB): fica pro início do processamento, que já materializa o fonte.
    if not db.get_vinheta(tk["vinheta_id"]):
        # Em 75min de upload um admin pode ter apagado a vinheta, e o /compose 409 depois.
        _recusar_e_limpar(key, job_id, "vinheta inexistente")
        log.warning("[podcast/complete] job %s recusado (vinheta sumiu): %s",
                    job_id, tk["vinheta_id"])
        return jsonify({"error": "A vinheta escolhida não existe mais. Escolha outra."}), 409
    try:
        url = s3_client.get_presigned_url(key, expiry_seconds=600)
        probe = _probe_podcast_video(url)
    except subprocess.TimeoutExpired:
        # Timeout do probe NÃO recusa: moov no fim do arquivo faria o ffprobe puxar tudo.
        # Aceita e deixa o probe do processamento (com o arquivo local) decidir.
        log.warning("[podcast/complete] job %s: probe por range estourou; aceitando assim "
                    "mesmo (o processamento valida com o arquivo local)", job_id)
        probe = None
    if probe:
        w, h, dar = probe
        if abs(dar - 16 / 9) > (16 / 9) * 0.02:
            _recusar_e_limpar(key, job_id, "não ser 16:9")
            log.warning("[podcast/complete] job %s recusado (não é 16:9): %dx%d dar=%.4f",
                        job_id, w, h, dar)
            return jsonify({"error": f"O vídeo do podcast precisa ser 16:9 (ex.: 1920x1080). "
                                     f"Este veio {w}x{h}."}), 400

    # FPS pela MESMA presignada do probe acima. A rota antiga grava o fps (é o que alimenta a
    # navegação quadro a quadro da tela de corte), e deixar o caminho novo gravando None criava
    # duas rotas escrevendo coisas diferentes na mesma coluna: hoje sem consumidor no pipeline,
    # daqui a três meses um bug que ninguém liga à origem do job. Best-effort igual na antiga:
    # probe que falha deixa fps=None e o job segue, porque isto é auxiliar, não crítico.
    fps = None
    try:
        sig = podcast_compose.probe_av_signature(url, require_audio=False)
        if sig and sig.get("fps_s") and "/" in sig["fps_s"]:
            num, den = sig["fps_s"].split("/")
            fps = float(num) / float(den) if float(den) else None
    except Exception as e:
        # AMPLO de propósito: o fps é auxiliar e este ponto roda DEPOIS de o arquivo inteiro
        # já estar no bucket. Qualquer exceção não prevista aqui custaria um upload de 10GB
        # que deu certo, o que é desproporcional a não saber o frame rate.
        log.warning("[podcast/complete] job %s: não consegui ler o fps (%s)", job_id, e)

    try:
        db.create_podcast_job(job_id, tk["filename"], None, session["user"], tk["vinheta_id"],
                              fps=fps, podcast_source_key=key)
    except sqlite3.IntegrityError:
        # Duplo clique: dois completes em voo passaram juntos pelo get_job() lá em cima, que
        # ainda devolvia None pros dois. O outro ganhou a corrida e o job já existe. É o mesmo
        # caso do retry idempotente, só detectado mais tarde: 200, não 500.
        log.info("[podcast/complete] job %s: complete concorrente, job já criado", job_id)
        return jsonify({"job_id": job_id, "status": "uploaded"}), 200
    # Telemetria do cliente é ENTRADA EXTERNA: %r pra não deixar forjar linha de journal (o
    # único lugar onde a operação enxerga o pipeline), e float() defensivo porque um TypeError
    # aqui viraria 500 DEPOIS do job já criado, com o usuário achando que perdeu o upload.
    med = d.get("medicao") if isinstance(d.get("medicao"), dict) else {}
    try:
        ms = float(med.get("ms") or 0)
    except (TypeError, ValueError):
        ms = 0.0
    log.info("[podcast/complete] job %s PRONTO: %.2fGB, cliente %.0fs (%.1f MB/s), conc=%r, "
             "retries=%r, servidor %.0fs", job_id, tk["size"] / 2**30, ms / 1000,
             (tk["size"] / 2**20) / max(ms / 1000, 0.001),
             med.get("concorrencia"), med.get("retries"), time.time() - tk["t0"])
    return jsonify({"job_id": job_id, "status": "uploaded"}), 201


@app.route("/api/podcast/upload/abort", methods=["POST"])
@login_required
def podcast_upload_abort():
    """Cancelamento explícito do usuário. Chama AbortMultipartUpload: sem isso, cancelar
    deixaria GB de partes cobradas e invisíveis no list_objects_v2. Idempotente: abortar duas
    vezes, ou abortar o que já expirou, responde 200."""
    user, err = _guarda_upload_direto()
    if err:
        return err
    d = request.get_json(silent=True) or {}
    try:
        tk = podcast_ingest.ler_token(d.get("token") or "", session["user"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    try:
        s3_client.abortar_multipart(tk["key"], tk["upload_id"])
        log.info("[podcast/abort] job %s: envio cancelado pelo usuário", tk["job_id"])
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "NoSuchUpload":
            log.warning("[podcast/abort] job %s: %s", tk["job_id"], e)
    except Exception as e:
        log.warning("[podcast/abort] job %s: %s", tk["job_id"], e)
    return jsonify({"ok": True})


@app.route("/api/podcast/upload", methods=["POST"])
@login_required
def podcast_upload():
    """Entrada do pipeline de podcast: valida 16:9 + frame rate constante e ESTACIONA o
    job ('uploaded', job_type='podcast'), sem disparar transcrição. Diferente do upload
    de clipes, valida ANTES de criar a linha (rejeição não deixa job nem arquivo órfão)
    e não passa pela admissão de MAX_CONCURRENT_JOBS (job parado não compete com a fila)."""
    if not config.PODCAST_ENABLED:
        return jsonify({"error": "Recurso indisponível."}), 404
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    if user["must_change_password"]:
        return jsonify({"error": "Troque a senha do primeiro acesso antes de processar vídeos."}), 403

    # Todo ramo de recusa daqui pra baixo LOGA o motivo. Sem isto o motivo se perdia: em 14
    # dias de log do nginx houve 18 respostas 400 nesta rota e não havia como saber se foram
    # VFR, não-16:9, extensão, vinheta inexistente ou arquivo ilegível. O usuário sobe 10GB e
    # espera até 75min pra ser recusado, então saber POR QUE é o mínimo. Inclui o tamanho: um
    # 400 depois de 8GB dói diferente de um 400 em 2MB.
    _tam = request.content_length or 0
    file = request.files.get("video")
    if not file or not file.filename or not _allowed(file.filename):
        log.warning("[podcast] upload recusado (extensão/arquivo): filename=%r tamanho=%.2fGB",
                    getattr(file, "filename", None), _tam / 2**30)
        return jsonify({"error": "Arquivo inválido. Use mp4, mov, avi, mkv ou webm."}), 400
    vinheta_id = (request.form.get("vinheta_id") or "").strip()
    if not vinheta_id or not db.get_vinheta(vinheta_id):
        log.warning("[podcast] upload recusado (vinheta): id=%r arquivo=%r tamanho=%.2fGB",
                    vinheta_id, file.filename, _tam / 2**30)
        return jsonify({"error": "Escolha uma vinheta válida da biblioteca."}), 400

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(config.JOB_DIR, job_id)
    ext = os.path.splitext(file.filename)[1].lower()
    local_path = os.path.join(job_dir, f"original{ext}")
    try:
        os.makedirs(job_dir, exist_ok=True)
        file.save(local_path)
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.warning("[podcast] falha ao salvar upload: %s", e)
        return jsonify({"error": "Falha ao salvar o vídeo."}), 500

    try:
        probe = _probe_podcast_video(local_path)
        if not probe:
            shutil.rmtree(job_dir, ignore_errors=True)
            log.warning("[podcast] upload recusado (ffprobe não leu vídeo): arquivo=%r "
                        "tamanho=%.2fGB", file.filename, _tam / 2**30)
            return jsonify({"error": "O arquivo não parece um vídeo válido."}), 400
        w, h, dar = probe
        if abs(dar - 16 / 9) > (16 / 9) * 0.02:
            shutil.rmtree(job_dir, ignore_errors=True)
            log.warning("[podcast] upload recusado (não é 16:9): %dx%d dar=%.4f arquivo=%r "
                        "tamanho=%.2fGB", w, h, dar, file.filename, _tam / 2**30)
            return jsonify({"error": f"O vídeo do podcast precisa ser horizontal, na proporção 16:9 "
                                     f"(ex.: 1920x1080). Este arquivo veio {w}x{h}. Exporte o vídeo em "
                                     f"16:9 e envie de novo."}), 400
        if not _podcast_is_cfr(local_path):
            shutil.rmtree(job_dir, ignore_errors=True)
            log.warning("[podcast] upload recusado (VFR): %dx%d arquivo=%r tamanho=%.2fGB",
                        w, h, file.filename, _tam / 2**30)
            return jsonify({"error": "Esse vídeo está com taxa de quadros irregular e ainda não pode ser "
                                     "processado. Peça ao estúdio um export com frame rate constante "
                                     "(CFR, ex.: 30 fps fixos) e envie de novo."}), 422
    except subprocess.TimeoutExpired:
        shutil.rmtree(job_dir, ignore_errors=True)
        log.warning("[podcast] upload recusado (probe estourou %ds): arquivo=%r tamanho=%.2fGB",
                    config.PODCAST_PROBE_TIMEOUT, file.filename, _tam / 2**30)
        return jsonify({"error": "A análise do vídeo demorou demais e foi interrompida. Tente enviar de "
                                 "novo; se acontecer outra vez, avise o administrador."}), 400

    # FPS exato pra tela "Cortar o início" (navegação quadro a quadro), reusando o MESMO
    # probe da montagem final (evita duplicar parse de fps). Auxiliar, não crítico: se
    # a assinatura vier None ou o "num/den" não parsear, o job segue com fps=None.
    fps = None
    sig = _probe_av_signature(local_path, require_audio=False)
    if sig and sig.get("fps_s"):
        try:
            num, den = sig["fps_s"].split("/")
            fps = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            fps = None

    db.create_podcast_job(job_id, file.filename, local_path, session["user"], vinheta_id, fps)
    log.info("[podcast] job %s estacionado em 'uploaded' (%dx%d, vinheta %s, fps %s)",
             job_id, w, h, vinheta_id, fps)
    return jsonify({"job_id": job_id, "status": "uploaded"}), 201


@app.route("/api/podcast/<job_id>/cut-intro", methods=["POST"])
@login_required
def podcast_cut_intro_route(job_id):
    """Corte manual do início do corpo bruto (§39 Parte 2), ANTES da transcrição: reencoda
    só o pedacinho até o próximo keyframe e copia o resto em -c copy (nunca o corpo
    inteiro). Disparo manual e explícito, mesma admissão atômica de podcast_transcribe
    (reserva a vaga virando 'cortando_inicio' dentro do lock); o local_path do job só
    troca em caso de sucesso confirmado (podcast_cut.py cuida disso na thread). Aceita
    também status 'error' com intro_cut_at gravado (mesmo padrão de retry do /compose):
    é o corte de um cut_at anterior que falhou, não outra falha qualquer do pipeline."""
    if not config.PODCAST_ENABLED:
        return jsonify({"error": "Recurso indisponível."}), 404
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    if job.get("user_email") != session["user"] and not user.get("is_admin"):
        return jsonify({"error": "Este projeto é de outro usuário."}), 403

    data = request.get_json(silent=True) or {}
    cut_at = data.get("cut_at")
    if not isinstance(cut_at, (int, float)) or isinstance(cut_at, bool) or cut_at < 0:
        return jsonify({"error": "Informe um ponto de corte válido (cut_at, em segundos, >= 0)."}), 400
    cut_at = float(cut_at)

    if not _podcast_tem_fonte(job):
        return jsonify({"error": "O arquivo do podcast não está mais no servidor. Envie de novo."}), 409

    total = _probe_duration(_podcast_fonte_para_probe(job))
    if total is None and job.get("local_path") and os.path.exists(job["local_path"]):
        # Só é erro com o arquivo EM DISCO: aí não conseguir ler a duração é sintoma de
        # arquivo quebrado. Com o fonte só no S3 o probe é remoto (timeout de 30s), e um
        # timeout de rede não pode impedir o produtor de cortar: segue sem a checagem, e a
        # thread revalida com o arquivo local, que é onde a decisão de verdade acontece.
        return jsonify({"error": "Não consegui ler a duração do arquivo do podcast."}), 400
    if total is not None and (total - cut_at) < config.PODCAST_CUT_MIN_REMAINING_SECONDS:
        return jsonify({
            "error": f"Esse ponto de corte deixaria só {max(total - cut_at, 0):.0f}s de episódio "
                     f"(mínimo {config.PODCAST_CUT_MIN_REMAINING_SECONDS:.0f}s). Escolha um ponto mais cedo."
        }), 400

    with _upload_lock:
        # Re-checa o status DENTRO do lock: dois disparos simultâneos não podem
        # reservar a vaga e subir duas threads de corte pro mesmo job. 'error' só é
        # aceito como retry quando intro_cut_at já está gravado (achado do code-review:
        # a tela promete "tenta de novo" pro corte que falhou, mas a rota rejeitava
        # qualquer 'error' com 409; sem o intro_cut_at, é outra falha do pipeline, não
        # um corte pra tentar de novo, e continua fora).
        job = db.get_job(job_id)
        # Corte-sobre-corte proibido (achado em produção, job Thiago Medeiros): 'uploaded'
        # com intro_cut_at já gravado só acontece DEPOIS de um corte bem-sucedido (é
        # justamente esse par de valores que _cortar_inicio grava junto no sucesso). Cortar
        # de novo em cima do arquivo já cortado reencoda/copia por cima de uma emenda
        # anterior e quebra a matemática de duração da validação. Ajustar o ponto exige
        # reenviar o episódio e cortar uma vez só.
        if job["status"] == "uploaded" and job.get("intro_cut_at") is not None:
            return jsonify({
                "error": "Este episódio já teve o início cortado. Pra ajustar o ponto de "
                         "corte, envie o episódio de novo e corte uma vez só."
            }), 409
        pode_tentar_de_novo = job["status"] == "error" and job.get("intro_cut_at") is not None
        if job["status"] != "uploaded" and not pode_tentar_de_novo:
            return jsonify({"error": f"O podcast não está aguardando corte (status: {job['status']})."}), 409
        reaped = db.reap_stale_jobs(config.JOB_STALE_MINUTES)
        if reaped:
            log.info("[watchdog] %d job(s) preso(s) liberado(s) por timeout", reaped)
        if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
            return jsonify({
                "error": "Um vídeo já está em processamento! Aguarde a conclusão antes de cortar "
                         "o início do podcast."
            }), 429
        db.update_job(job_id, status="cortando_inicio", error=None)

    threading.Thread(target=podcast_cut.run_podcast_cut_intro, args=(job_id, cut_at), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "cortando_inicio"}), 202


@app.route("/api/podcast/<job_id>/transcribe", methods=["POST"])
@login_required
def podcast_transcribe(job_id):
    """Dispara a Fase A do podcast (transcrição via run_thread1 + análise de gancho no
    callback) pro job estacionado em 'uploaded'. Disparo EXPLÍCITO de propósito: um
    upload de GBs não deve levar 429 depois de pronto; o produtor aciona com a fila
    livre. Usa a MESMA admissão atômica dos clipes (reserva a vaga virando 'queued')."""
    if not config.PODCAST_ENABLED:
        return jsonify({"error": "Recurso indisponível."}), 404
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Projeto não encontrado."}), 404
    if job.get("user_email") != session["user"] and not user.get("is_admin"):
        return jsonify({"error": "Este projeto é de outro usuário."}), 403
    if job.get("job_type") != "podcast":
        return jsonify({"error": "Este projeto não é um podcast."}), 400
    if not _podcast_tem_fonte(job):
        return jsonify({"error": "O arquivo do podcast não está mais no servidor. Envie de novo."}), 409

    with _upload_lock:
        # Re-checa o status DENTRO do lock: dois disparos simultâneos não podem
        # reservar a vaga e subir duas thread1 pro mesmo job.
        job = db.get_job(job_id)
        if job["status"] != "uploaded":
            return jsonify({"error": f"O podcast não está aguardando disparo (status: {job['status']})."}), 409
        reaped = db.reap_stale_jobs(config.JOB_STALE_MINUTES)
        if reaped:
            log.info("[watchdog] %d job(s) preso(s) liberado(s) por timeout", reaped)
        if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
            return jsonify({
                "error": "Um vídeo já está em processamento! Aguarde a conclusão antes de disparar o podcast."
            }), 429
        db.update_job(job_id, status="queued", progress=5)

    threading.Thread(target=processor.run_thread1, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "queued"}), 202


def _podcast_job_or_error(job_id):
    """Guardas comuns das rotas da tela de aprovação do gancho (§39 Parte 4): flag ligada,
    job existe e é podcast. Devolve (job, None) ou (None, resposta de erro pronta)."""
    if not config.PODCAST_ENABLED:
        return None, (jsonify({"error": "Recurso indisponível."}), 404)
    job = db.get_job(job_id)
    if not job:
        return None, (jsonify({"error": "Projeto não encontrado."}), 404)
    if job.get("job_type") != "podcast":
        return None, (jsonify({"error": "Este projeto não é um podcast."}), 400)
    return job, None


def _job_hooks(job) -> list:
    try:
        return json.loads(job.get("hooks_json") or "{}").get("hooks") or []
    except (ValueError, AttributeError):
        return []


def _podcast_audio_stale(job: dict) -> bool:
    """True se `podcast_audio_status` está em 'extraindo' mas `updated_at` já passou do
    teto de staleness (JOB_STALE_MINUTES): sinal de thread morta (crash/restart do
    systemd) sem heartbeat recente, não de trabalho legítimo ainda em andamento."""
    if job.get("podcast_audio_status") != "extraindo":
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=config.JOB_STALE_MINUTES)).isoformat()
    return (job.get("updated_at") or "") < cutoff


@app.route("/api/podcast/<job_id>/hooks")
@login_required
def podcast_hooks_view(job_id):
    """Dados da tela de aprovação: os ganchos sugeridos + a escolha já gravada (se houver).
    Biblioteca compartilhada: qualquer usuário ativo vê (igual get_project); a APROVAÇÃO é
    que exige dono/admin.

    Auto-cura da extração de áudio: esta é a rota de polling que o front já chama a cada
    poucos segundos enquanto audio_status == "extraindo" (sem nenhum botão pra reenviar o
    POST nesse meio tempo). Uma thread morta no meio (ex.: restart do systemd) travaria o
    job em "extraindo" pra sempre sem este efeito colateral de leitura, mesmo precedente
    de _ensure_burn (GET que gera/cacheia como efeito colateral). A checagem+escrita roda
    sob o MESMO lock por job da rota POST (com double-check da leitura já dentro do
    lock): sem isto, um GET decidindo sobre um snapshot velho podia sobrescrever pra
    'error' um status 'extraindo' fresco que um POST concorrente acabou de redisparar."""
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    if _podcast_audio_stale(job):
        with _podcast_audio_lock(job_id):
            job = db.get_job(job_id)
            if _podcast_audio_stale(job):
                log.warning("[podcast_audio] job %s: extração presa há mais de %dmin "
                           "(detectada via polling), marcando como erro",
                           job_id, config.JOB_STALE_MINUTES)
                db.update_job(job_id, podcast_audio_status="error",
                              podcast_audio_error="Extração interrompida (provavelmente "
                                                  "por reinício do servidor). Tente "
                                                  "novamente.")
                job = db.get_job(job_id)
    try:
        choice = json.loads(job["hook_choice"]) if job.get("hook_choice") else None
    except ValueError:
        choice = None
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "progress": job.get("progress"),
        "filename": job["filename"],
        "hooks": _job_hooks(job),
        "hook_choice": choice,
        "intro_cut_at": job.get("intro_cut_at"),
        "fps": job.get("fps"),
        "source_available": _podcast_tem_fonte(job),
        "final_available": bool(job.get("podcast_final_key")) and job["status"] == "done",
        "audio_status": job.get("podcast_audio_status"),
        "audio_available": bool(job.get("podcast_audio_key")) and job.get("podcast_audio_status") == "done",
        # Pro front decidir se mostra o botão de excluir, igual o projeto de clipe já faz com
        # p.owner_email. Sem isto o podcast teria que fazer uma segunda request no ciclo de
        # polling (de 3s) só pra saber o dono. Mesmo campo que _project_from_job já devolve.
        "owner_email": job.get("user_email"),
        "error": job.get("error") if job["status"] == "error" else None,
    })


@app.route("/api/podcast/<job_id>/source")
@login_required
def podcast_source(job_id):
    """16:9 original do JOB_DIR pro preview do trim, SEM re-encode: conditional=True liga
    o suporte a HTTP Range (206), o navegador busca só os bytes do trecho que vai tocar.
    O peso do preview fica no cliente; a VPS só faz I/O de disco."""
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    local = job.get("local_path")
    if local and os.path.exists(local):
        return send_file(local, conditional=True)
    if job.get("podcast_source_key"):
        # Fonte ainda só no S3 (upload direto, antes da materialização). 302 pra presignada em
        # vez de baixar 10GB pra servir: o S3 responde Range nativamente, então o preview do
        # trim funciona igual, e a VPS sai do caminho dos bytes também na leitura.
        # TTL curto e explícito, ao contrário dos entregáveis: isto é o MASTER BRUTO, e uma
        # presignada é link ao portador que nenhuma revogação de sessão alcança. 15min cobre
        # o preview do trim (o player renegocia a cada seek com uma request nova à rota).
        return _s3_redirect(job["podcast_source_key"], expiry_seconds=900)
    return jsonify({"error": "O arquivo do podcast não está mais no servidor. Envie de novo."}), 409


@app.route("/api/podcast/<job_id>/approve", methods=["POST"])
@login_required
def podcast_approve(job_id):
    """Gate humano do gancho: grava a escolha (qual gancho + pontas aparadas) em
    jobs.hook_choice e estaciona o job em 'gancho_aprovado' pra montagem (Parte 5)
    consumir. NÃO monta nada aqui. Guarda de duração FROUXA de propósito (10 a 90s,
    anti dedo gordo): a faixa 30-60s do hooks.py é do modelo; aqui quem manda é o humano."""
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    if job.get("user_email") != session["user"] and not user.get("is_admin"):
        return jsonify({"error": "Este projeto é de outro usuário."}), 403
    if job["status"] != "aguardando_aprovacao":
        return jsonify({"error": f"O podcast não está aguardando aprovação (status: {job['status']})."}), 409

    data = request.get_json(silent=True) or {}
    hooks_list = _job_hooks(job)
    idx = data.get("index")
    if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(hooks_list)):
        return jsonify({"error": "Escolha um dos ganchos sugeridos."}), 400
    try:
        start, end = float(data["start"]), float(data["end"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Tempos de início e fim inválidos."}), 400
    if not (start >= 0 and end > start):
        return jsonify({"error": "O fim precisa vir depois do início (e o início ser >= 0)."}), 400
    dur = end - start
    if not (10.0 <= dur <= 90.0):
        return jsonify({"error": f"Duração de {dur:.0f}s fora do razoável pra uma abertura (10 a 90s)."}), 400
    if not _podcast_tem_fonte(job):
        return jsonify({"error": "O arquivo do podcast não está mais no servidor. Envie de novo."}), 409
    total = _probe_duration(_podcast_fonte_para_probe(job))
    if total and end > total + 1.0:
        return jsonify({"error": f"O fim ({end:.1f}s) passa da duração do vídeo ({total:.1f}s)."}), 400

    choice = {"index": idx, "start": round(start, 3), "end": round(end, 3),
              "approved_by": session["user"],
              "approved_at": datetime.now(timezone.utc).isoformat()}
    db.update_job(job_id, hook_choice=json.dumps(choice, ensure_ascii=False),
                  status="gancho_aprovado", progress=90)
    log.info("[podcast] job %s: gancho %d aprovado (%.1fs a %.1fs)", job_id, idx, start, end)
    return jsonify({"job_id": job_id, "status": "gancho_aprovado", "hook_choice": choice})


@app.route("/api/podcast/<job_id>/compose", methods=["POST"])
@login_required
def podcast_compose_route(job_id):
    """Dispara a MONTAGEM (§39 Parte 5) do podcast com gancho aprovado. Manual e explícito
    (mesma razão do /transcribe: trabalho pronto não pode tomar 429 por fila cheia; o
    produtor aciona com a fila livre), mesma admissão atômica. Aceita também status
    'error' COM hook_choice: retry de montagem que falhou é só re-POSTar."""
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    if job.get("user_email") != session["user"] and not user.get("is_admin"):
        return jsonify({"error": "Este projeto é de outro usuário."}), 403
    if not _podcast_tem_fonte(job):
        return jsonify({"error": "O arquivo do podcast não está mais no servidor. Envie de novo."}), 409
    try:
        choice = json.loads(job.get("hook_choice") or "null")
    except ValueError:
        choice = None
    if not isinstance(choice, dict):
        return jsonify({"error": "Este podcast não tem gancho aprovado."}), 409
    if not db.get_vinheta(job.get("vinheta_id") or ""):
        return jsonify({"error": "A vinheta do projeto não existe mais na biblioteca."}), 409

    with _upload_lock:
        job = db.get_job(job_id)
        if job["status"] not in ("gancho_aprovado", "error"):
            return jsonify({"error": f"O podcast não está pronto pra montagem (status: {job['status']})."}), 409
        reaped = db.reap_stale_jobs(config.JOB_STALE_MINUTES)
        if reaped:
            log.info("[watchdog] %d job(s) preso(s) liberado(s) por timeout", reaped)
        if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
            return jsonify({
                "error": "Um vídeo já está em processamento! Aguarde a conclusão antes de montar o podcast."
            }), 429
        db.update_job(job_id, status="montando", progress=10, error=None)

    threading.Thread(target=podcast_compose.run_podcast_compose, args=(job_id,), daemon=True).start()
    return jsonify({"job_id": job_id, "status": "montando"}), 202


@app.route("/api/podcast/<job_id>/final")
@login_required
def podcast_final(job_id):
    """302 pra URL presignada do episódio montado (player). ?download=1 embute
    Content-Disposition: attachment com nome amigável (mesma mecânica dos clipes)."""
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    key = job.get("podcast_final_key")
    if not key or job["status"] != "done":
        return jsonify({"error": "O episódio ainda não foi montado."}), 409
    dl = None
    if request.args.get("download"):
        dl = _safe_filename(os.path.splitext(job.get("filename") or "podcast")[0],
                            fallback="podcast") + "_final.mp4"
    return _s3_redirect(key, dl)


@app.route("/api/podcast/<job_id>/audio", methods=["POST"])
@login_required
def podcast_audio_route(job_id):
    """Dispara a extração ASSÍNCRONA do áudio (MP3) do episódio já montado: thread de
    fundo + polling (GET desta mesma rota), igual à queima sob demanda dos clipes. Um
    episódio de horas tornaria a extração síncrona mais lenta que o timeout do nginx
    (abordagem já tentada e rejeitada por esse motivo), daí a rota só disparar e voltar
    202. Mesma autorização de podcast_final (biblioteca compartilhada: qualquer usuário
    ativo pode pedir o áudio de qualquer projeto, sem gate de dono/admin).

    Kill switch de emergência (config.PODCAST_AUDIO_ENABLED, default OFF): achado em
    produção 2026-07-24 que essa extração pode travar o app inteiro (mesma classe do
    download_file, mas em outro ponto do caminho, ainda não isolado). Fica fora do ar até
    o conserto real; a rota de download (GET, abaixo) continua liberada pra quem já tem
    o MP3 pronto."""
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    if not config.PODCAST_AUDIO_ENABLED:
        return jsonify({"error": "Extração de áudio temporariamente desativada (investigando "
                                 "um problema). Tente novamente mais tarde."}), 503
    if job["status"] != "done" or not job.get("podcast_final_key"):
        return jsonify({"error": "O episódio ainda não foi montado."}), 409

    # Gate de dono, o MESMO das outras quatro rotas de podcast (/cut-intro, /transcribe,
    # /approve, /compose). Faltava só aqui, e a assimetria não era inofensiva: as outras são
    # etapas do pipeline do próprio dono, e esta é a única que GASTA DINHEIRO por conta de
    # terceiro (baixa o episódio inteiro do bucket pago e roda ffmpeg). Ler o MP3 pronto segue
    # aberto, como o resto da biblioteca compartilhada: o que fecha aqui é o POST que produz.
    user = db.get_user(session["user"])
    if job.get("user_email") != session["user"] and not (user or {}).get("is_admin"):
        return jsonify({"error": "Este projeto é de outro usuário."}), 403

    with _podcast_audio_lock(job_id):
        job = db.get_job(job_id)
        if job.get("podcast_audio_status") == "done" and job.get("podcast_audio_key"):
            quente = _cache_quente(job["podcast_audio_key"])
            if quente:
                return jsonify({"job_id": job_id, "audio_status": "done"}), 200
            if quente is None:
                # ÚNICO ponto onde S3 estolado NÃO degrada pra "não existe": aqui um False
                # dispararia uma extração NOVA de um episódio de vários GB (download + ffmpeg +
                # upload) só porque um HEAD não respondeu. Melhor pedir pra tentar de novo.
                return jsonify({"error": "Armazenamento instável agora. Tente novamente em "
                                         "instantes."}), 503
        if job.get("podcast_audio_status") == "extraindo":
            # A auto-cura via polling (podcast_hooks_view) já cobre o caso comum; esta
            # checagem redundante aqui só cobre quem chama o POST direto sem nunca ter
            # passado pelo GET.
            if not _podcast_audio_stale(job):
                return jsonify({"job_id": job_id, "audio_status": "extraindo"}), 202
            log.warning("[podcast_audio] job %s: extração presa há mais de %dmin, "
                       "redisparando", job_id, config.JOB_STALE_MINUTES)
        db.update_job(job_id, podcast_audio_status="extraindo", podcast_audio_error=None)

    threading.Thread(target=podcast_audio.run_podcast_audio_extract, args=(job_id,),
                     daemon=True).start()
    return jsonify({"job_id": job_id, "audio_status": "extraindo"}), 202


@app.route("/api/podcast/<job_id>/audio")
@login_required
def podcast_audio_download(job_id):
    """302 pra URL presignada do MP3 já extraído (mesmo padrão de podcast_final)."""
    job, err = _podcast_job_or_error(job_id)
    if err:
        return err
    key = job.get("podcast_audio_key")
    if not key or job.get("podcast_audio_status") != "done":
        return jsonify({"error": "O áudio ainda não foi extraído."}), 409
    dl_name = _safe_filename(os.path.splitext(job.get("filename") or "podcast")[0],
                             fallback="podcast") + ".mp3"
    return _s3_redirect(key, dl_name)


def _is_youtube_url(url: str) -> bool:
    """Aceita só URLs de vídeo do YouTube (não qualquer host): evita apontar o yt-dlp pra
    destinos arbitrários (abuso/SSRF) e mantém a feature no escopo pedido."""
    try:
        p = urllib.parse.urlparse(url)
    except (ValueError, AttributeError):
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


@app.route("/api/upload/youtube", methods=["POST"])
@login_required
def upload_youtube():
    """Mesma geração de clipes, mas a fonte é um link do YouTube (VOD). Valida o link e a
    duração (teto de custo) ANTES de reservar a vaga; depois baixa via yt-dlp e cai no mesmo
    pipeline do upload (ver processor.run_youtube_thread1)."""
    user = db.get_user(session["user"])
    if not user:
        session.clear()
        return jsonify({"error": "Sessão inválida.", "auth": False}), 401
    if user["must_change_password"]:
        return jsonify({"error": "Troque a senha do primeiro acesso antes de processar vídeos."}), 403

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url or not _is_youtube_url(url):
        return jsonify({"error": "Cole um link válido do YouTube."}), 400

    try:
        min_duration = int(data.get("min_duration"))
        max_duration = int(data.get("max_duration"))
    except (TypeError, ValueError):
        return jsonify({"error": "min_duration e max_duration são obrigatórios."}), 400
    if min_duration >= max_duration:
        return jsonify({"error": "A duração mínima deve ser menor que a máxima."}), 400
    if not (_MIN_CLIP_SECONDS <= min_duration < max_duration <= _MAX_CLIP_SECONDS):
        return jsonify({
            "error": f"Duração fora do intervalo permitido ({_MIN_CLIP_SECONDS} a {_MAX_CLIP_SECONDS}s)."
        }), 400

    # Metadados ANTES de reservar a vaga: confirma que não é live em andamento e respeita o
    # teto de duração (sem isso, uma live de horas custaria caro e travaria o único slot). O
    # probe (subprocess yt-dlp de até 45s) roda sob um semáforo pra não esgotar as threads.
    if not _yt_probe_sem.acquire(blocking=False):
        return jsonify({"error": "Muitas validações de link em andamento. Tente de novo em instantes."}), 429
    _tk_yt = em_voo.entrar("ytprobe", "youtube probe")
    try:
        info = processor.youtube_probe(url)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    finally:
        em_voo.sair(_tk_yt)
        _yt_probe_sem.release()
    # Recusa live em andamento, agendada E recém-encerrada (post_live: VOD ainda processando,
    # duração instável/parcial). Só VOD totalmente pronto segue.
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
        return jsonify({
            "error": "Transmissões ao vivo (ou recém-encerradas, ainda processando) não são suportadas. Use o vídeo depois que a live terminar e o VOD ficar pronto."
        }), 400
    dur = info.get("duration")
    if not dur or dur <= 0:
        return jsonify({"error": "Não consegui ler a duração do vídeo (pode ser live ou estar indisponível)."}), 400
    if dur > config.YOUTUBE_MAX_DURATION:
        lim = config.YOUTUBE_MAX_DURATION // 60
        return jsonify({"error": f"Vídeo muito longo ({round(dur / 60)} min). O limite é {lim} min."}), 400

    # Legenda: mesma lógica do upload (preferência salva + override opcional no corpo).
    captions_on = _pref_captions_default(session["user"])
    if "captions" in data:
        captions_on = 1 if data.get("captions") else 0

    title = info.get("title") or "Vídeo do YouTube"
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(config.JOB_DIR, job_id)
    local_path = os.path.join(job_dir, "original.mp4")

    # Admissão atômica, idêntica ao upload (reap + check de vaga + reserva sob o lock).
    with _upload_lock:
        reaped = db.reap_stale_jobs(config.JOB_STALE_MINUTES)
        if reaped:
            log.info("[watchdog] %d job(s) preso(s) liberado(s) por timeout", reaped)
        if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
            return jsonify({
                "error": "Um vídeo já está em processamento! Vai tomar uma água, respirar e aguardar a conclusão antes de enviar novo vídeo :)"
            }), 429
        db.create_job(job_id, title[:200], min_duration, max_duration, local_path,
                      session["user"], captions=captions_on)

    threading.Thread(target=processor.run_youtube_thread1, args=(job_id, url), daemon=True).start()
    return jsonify({"job_id": job_id, "title": title}), 202


# ─── Rotas internas (N8N) ────────────────────────────────────────────────────
# NÃO usam login de sessão. Defesa em três camadas:
#  1) Flask escuta só em 127.0.0.1 e 172.17.0.1 (NUNCA público) -> ver opussb.service
#  2) Nginx (porta 80) devolve 404 para /api/callback e /api/internal
#  3) gate abaixo: só loopback e a subnet da bridge Docker (qualquer IP de container).
# Confiar na subnet inteira (não um IP fixo) evita quebra silenciosa se o Docker
# recriar o container do N8N com outro IP (ex: 172.17.0.3).
_DOCKER_BRIDGE = ipaddress.ip_network("172.17.0.0/16")


def _is_trusted_caller(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.is_loopback or ip in _DOCKER_BRIDGE


# Teto de corpo do callback: este caminho chega pelo bind 172.17.0.1 SEM passar pelo
# nginx, então o client_max_body_size de lá não vale aqui, e o MAX_CONTENT_LENGTH global
# (16 GiB, dimensionado pro upload de vídeo) não protege nada. O maior payload legítimo
# observado tem ~220KB de transcript; 32 MiB dá folga de ~100x e ainda impede que um nó
# do N8N mal mapeado (ex.: áudio em base64 no campo errado) carregue GBs pra RAM do
# worker único via get_json e derrube o serviço por OOM.
_MAX_CALLBACK_BODY = 32 * 1024 * 1024


@app.route("/api/callback", methods=["POST"])
def callback():
    if not _is_trusted_caller(request.remote_addr):
        abort(403)
    # Rejeitar Content-Length ausente cobre transfer-encoding chunked, que furaria o
    # teto; o nó HTTP do N8N sempre manda Content-Length normal.
    if request.content_length is None or request.content_length > _MAX_CALLBACK_BODY:
        abort(413)
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "corpo inválido"}), 400
    job_id = data.get("job_id")
    job = db.get_job(job_id)
    if not job_id or not job:
        return jsonify({"error": "job_id inválido"}), 400
    # Se o job foi cancelado durante a transcrição, não retoma o corte. E IDEMPOTENTE: um callback
    # duplicado (N8N e at-least-once) num job ja 'done' NAO re-spawna a thread2 (que re-cortaria e,
    # se falhasse, dispararia _fail_job -> apagaria o 16:9 retido VIVO daquele projeto).
    if job["status"] in ("error", "done"):
        return jsonify({"ok": True, "skipped": f"job ja {job['status']}"})
    # Podcast (§39): a transcrição volta pelo MESMO webhook do N8N, mas daqui o fluxo é
    # outro: guarda o SRT, IGNORA o clips_json (o scoring de clipes roda à toa no N8N por
    # ora; morre na frente de remover o N8N) e dispara a análise de gancho. Idempotência
    # por transcript (NÃO por status 'transcribing'): o N8N de produção faz PATCH direto
    # de status/progress ANTES do callback (Steps 2 e 7 do N8N_WORKFLOW.md, feitos pro
    # fluxo de clipes: "transcribing"/40 e depois "cutting"/65), então quando o callback
    # chega o job de podcast já pode estar em 'cutting'. Uma guarda por status travava
    # nesse "cutting" e pulava o callback pra sempre (transcript nunca salvo, hooks nunca
    # dispara). transcript IS NULL é o estado real que importa: ainda não processamos
    # este job, não importa que status o N8N tenha deixado. E fica ATÔMICO com o disparo
    # do hooks (mesmo update abaixo grava transcript e já tira o job do "ainda não
    # processado"), o que hooks_json IS NULL sozinho não garantiria: o hooks.run_podcast_hooks
    # leva vários segundos (até 2 chamadas ao Claude) com hooks_json ainda NULL nesse meio
    # tempo, e o serviço roda com threads reais (gthread) recebendo um N8N "at least once";
    # uma duplicata nessa janela reprocessaria e dispararia o Claude em dobro.
    # Sanity check de transcript degenerado (incidente 2026-08-20: o Whisper alucinou
    # a MESMA frase em 263/263 segmentos e o pipeline rodou por cima, entregando 18
    # clipes falsos como 'done'). Degenerado marca error e NÃO segue: barra corte,
    # reframe e S3 no fluxo de clipes e o gancho no de podcast. Roda DEPOIS das guardas
    # de idempotência (duplicata do N8N nunca chega aqui) e é avaliado nos dois ramos,
    # logo antes da ação. O scoring já rodou no N8N a essa altura; a chamada Haiku
    # desperdiçada é aceita de propósito (mesma decisão do scoring de podcast, acima),
    # em vez de acoplar a barreira em nós novos do N8N, que está marcado pra remoção.
    _msg_degenerado = ("A transcrição saiu degenerada, provavelmente silêncio "
                       "longo ou áudio ruim. Nada foi gerado; verifique o áudio "
                       "do arquivo e reenvie.")

    def _transcript_degenerado() -> bool:
        # Kill switch (ver o comentário de TRANSCRIPT_CHECK_ENABLED em config.py): com a
        # flag desligada o check nem roda, e o log deixa rastro de que foi pulado por
        # decisão operacional, não por bug.
        if not config.TRANSCRIPT_CHECK_ENABLED:
            log.info("[callback] job %s: sanity check de transcript DESLIGADO por flag "
                     "(TRANSCRIPT_CHECK_ENABLED=false)", job_id)
            return False
        veredito = captions.avaliar_transcript(data.get("transcript") or "")
        if veredito["degenerado"]:
            # Métricas são só números (nunca logar texto do transcript: dado de cliente).
            log.warning("[callback] job %s: transcript degenerado (%s); metricas=%s",
                        job_id, veredito["motivo"], veredito["metricas"])
        return veredito["degenerado"]

    if job.get("job_type") == "podcast":
        if job.get("transcript") is not None:
            return jsonify({"ok": True, "skipped": f"podcast já processado (status {job['status']})"})
        if _transcript_degenerado():
            # Podcast: só marca error, sem mexer no disco (mesmo padrão dos outros
            # erros de podcast, ex. hooks sem transcript); a fonte fica pro delete.
            db.update_job(job_id, status="error", error=_msg_degenerado)
            return jsonify({"ok": True, "skipped": "transcript degenerado"})
        db.update_job(job_id, transcript=data.get("transcript"),
                      status="analisando_gancho", progress=75)
        threading.Thread(target=hooks.run_podcast_hooks, args=(job_id,), daemon=True).start()
        return jsonify({"ok": True})
    if _transcript_degenerado():
        # Clipes: erro terminal segue o MESMO caminho dos demais (_fail_job: error +
        # limpa JOB_DIR e o 16:9 retido), senão o vídeo de origem (GBs) vaza no disco
        # da VPS até alguém deletar o card na mão.
        processor._fail_job(job_id, _msg_degenerado)
        return jsonify({"ok": True, "skipped": "transcript degenerado"})
    db.update_job(
        job_id,
        transcript=data.get("transcript"),
        clips_json=data.get("clips_json"),
        status="cutting",
        progress=70,
    )
    threading.Thread(target=processor.run_thread2, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/internal/jobs/<job_id>", methods=["PATCH"])
def internal_update_job(job_id):
    if not _is_trusted_caller(request.remote_addr):
        abort(403)
    data = request.get_json(force=True)
    allowed = {"runpod_job_id", "status", "progress", "error"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nenhum campo válido."}), 400
    db.update_job(job_id, **updates)
    return jsonify({"ok": True})


@app.route("/api/internal/deploy_gate")
def deploy_gate():
    """Responde "dá pra reiniciar agora?" pro ritual de deploy. Rota INTERNA de propósito.

    Por que não no /health: aquele é a única rota pública junto do /login (nginx serve sem
    auth), e publicar em tempo real quais operações rodam, quantas e há quanto tempo entrega
    a um anônimo o perfil operacional da empresa: horário de upload de episódio, duração,
    quando o sistema fica ocioso e quando a fila esvazia (ou seja, quando o deploy vem e o
    site cai). O ritual lê isso de dentro da VPS (localhost:5001), então nada disso precisa
    sair pela internet.

    Não dá pra gatear o /health com _is_trusted_caller: tudo que passa pelo nginx chega como
    127.0.0.1 e seria considerado confiável. Rota sob /api/internal, que o nginx já devolve
    404 pra fora, é a barreira que funciona (e ainda tem o gate por subnet abaixo)."""
    if not _is_trusted_caller(request.remote_addr):
        abort(404)
    try:
        active = db.count_trabalho_em_andamento()
        db_ok = True
    except Exception as e:
        active, db_ok = None, False
        log.warning("deploy_gate: checagem do banco falhou (%s)", e)
    voo = em_voo.snapshot()
    for cat, idade, rotulo in em_voo.novos_suspeitos():
        # Uma vez por vazamento, não a cada consulta: o rótulo (job/clip/path) só aparece
        # AQUI, no journal, nunca numa resposta HTTP.
        log.warning("[em_voo] SUSPEITO: %s em voo há %ds (%s). Provavelmente vazou (não "
                    "decrementou); não conta mais pro pode_reiniciar", cat, idade, rotulo)
    return jsonify({
        "db": db_ok,
        "active_jobs": active,
        "em_voo": voo["em_voo"],
        "suspeitos": voo["suspeitos"],
        "pode_reiniciar": bool(db_ok) and active == 0 and not voo["bloqueia_deploy"],
    })


@app.route("/api/internal/maintenance_gate")
def maintenance_gate():
    """Chamada só pelo nginx (auth_request) durante a manutenção, pra decidir se a
    sessão do request original é de um admin ativo. Mesma checagem do admin_required
    (BACKEND §43), sem reusar o decorator: aqui o chamador é o nginx, não o cliente
    HTTP, então não faz sentido limpar sessão nem devolver mensagem em português."""
    if not _is_trusted_caller(request.remote_addr):
        abort(403)
    email = _session_email()
    if not email:
        return jsonify({"allowed": False}), 401
    user = db.get_user(email)
    if not user or not user.get("is_admin") or (user.get("status") or "active") != "active":
        return jsonify({"allowed": False}), 401
    return jsonify({"allowed": True}), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=config.FLASK_PORT)
