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
from flask import Flask, request, jsonify, abort, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException

import config
import db
import s3_client
import processor
import captions

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
    """Saúde do processo. Inclui um ping leve no banco e a contagem de jobs ativos,
    pra checagem de ops enxergar 'degraded' quando o SQLite trava. Sem segredos."""
    db_ok, active = True, None
    try:
        active = db.count_active_jobs()
    except Exception as e:
        db_ok = False
        log.warning("health: checagem do banco falhou (%s)", e)
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


def _clip_redirect(key, download_name=None):
    """302 pra URL presignada de um clipe. Com download_name, a presignada carrega
    Content-Disposition: attachment (força o download com nome amigável em vez de tocar inline)."""
    return redirect(s3_client.get_presigned_url(key, download_name=download_name), code=302)


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
    status = job["status"] if job["status"] in ("done", "error") else "processing"
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
    if val is not None and val not in config.END_SCREENS:
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
    return redirect(s3_client.get_presigned_url(key), code=302)


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
    done = [j for j in jobs if j["status"] == "done"]
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
        "processing_count": sum(1 for j in jobs if j["status"] not in ("done", "error")),
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
    força essa tela; NULL = herda o default do projeto (jobs.end_screen). Retorna um id válido em
    config.END_SCREENS ou None (sem tela final)."""
    choice = db.get_clip_end_screen(clip_id)
    if choice == "none":
        return None
    es_id = choice or job.get("end_screen")
    if es_id and es_id in config.END_SCREENS:
        return es_id
    return None


def _end_screen_options():
    """Biblioteca de telas finais pro front (id + rótulo). Ordem estável (dict ordenado)."""
    return [{"id": k, "label": v.get("label", k)} for k, v in config.END_SCREENS.items()]


def _endscreen_key(content_key: str) -> str:
    """Chave do entregável final (content + tela): clip_NN_cap.mp4 -> clip_NN_cap_es.mp4 (idem _trim
    e _base, e qualquer .mp4 -> _es.mp4 cobrindo clipe antigo)."""
    if content_key.endswith("_es.mp4"):
        return content_key
    if content_key.endswith(".mp4"):
        return content_key[:-4] + "_es.mp4"
    return content_key + "_es.mp4"


def _probe_av_signature(path: str):
    """Params de A/V do content pra casar o outro: {fps_s, timescale, ar, ac} ou None se faltar vídeo
    OU áudio (sem áudio o concat-copy quebraria por nº de streams diferente -> sem tela final)."""
    def _probe(stream, entries):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", stream,
             "-show_entries", f"stream={entries}", "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout.split() if r.returncode == 0 else []
    try:
        v = _probe("v:0", "r_frame_rate,time_base")
        a = _probe("a:0", "sample_rate,channels")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if len(v) < 2 or len(a) < 2:
        return None
    fps_s = v[0]                              # ex.: "30/1" ou "24000/1001"
    tb = v[1]                                 # time_base "1/15360"
    timescale = tb.split("/")[-1] if "/" in tb else "15360"
    if not fps_s or "/" not in fps_s or fps_s.startswith("0/"):
        return None
    try:
        return {"fps_s": fps_s, "timescale": int(timescale), "ar": int(a[0]), "ac": int(a[1])}
    except ValueError:
        return None


def _ensure_matched_outro(es_id: str, outro_src_key: str, sig: dict, tmp: str):
    """Re-encoda o outro pros params do clipe (fps/timescale/áudio) e cacheia no S3 por assinatura.
    Devolve o path local do outro casado (baixado), ou None em falha. ~0,7s na 1a vez por assinatura,
    instantâneo depois (raro variar entre clipes do mesmo job)."""
    sig_str = f"{sig['fps_s']}_{sig['timescale']}_{sig['ar']}_{sig['ac']}".replace("/", "-")
    matched_key = config.END_SCREEN_PREFIX + f"_matched/{es_id}_{sig_str}.mp4"
    matched_path = os.path.join(tmp, "matched.mp4")
    if s3_client.exists(matched_key):
        s3_client.download_file(matched_key, matched_path)
        return matched_path
    src_path = os.path.join(tmp, "outro_src.mp4")
    s3_client.download_file(outro_src_key, src_path)
    cmd = ["ffmpeg", "-y", "-i", src_path,
           "-r", sig["fps_s"], "-video_track_timescale", str(sig["timescale"]),
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


def _append_end_screen(content_path: str, outro_path: str, out_path: str, tmp: str) -> bool:
    """Concatena content + outro (já casado) por concat-copy (NÃO re-encoda o content, o caro) +
    faststart. Só funciona porque o outro foi casado aos params do content. Retorna True/False."""
    list_path = os.path.join(tmp, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        f.write(f"file '{content_path}'\nfile '{outro_path}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
           "-c", "copy", "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=config.END_SCREEN_TIMEOUT,
                       preexec_fn=lambda: os.nice(config.BURN_NICE))
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        log.warning("[endscreen] concat falhou: %s", r.stderr[-300:])
        return False
    return True


def _produce_endscreen(content_key: str, es_id: str, final_key: str) -> bool:
    """Baixa o content, casa o outro, concat-copy [content + outro] -> final_key no S3. Sob o semáforo
    GLOBAL de queima (ffmpeg pesado-ish; não pode ser chamado com o semáforo já segurado). Retorna
    True se subiu o final; False em qualquer falha (o chamador serve o content sem tela final)."""
    es = config.END_SCREENS.get(es_id)
    if not es:
        return False
    if not _burn_sem.acquire(timeout=_BURN_QUEUE_TIMEOUT):
        log.warning("[endscreen] fila cheia para %s; sem tela final", content_key)
        return False
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
        matched_path = _ensure_matched_outro(es_id, es["key"], sig, tmp)
        if not matched_path:
            return False
        out_path = os.path.join(tmp, "final.mp4")
        if not _append_end_screen(content_path, matched_path, out_path, tmp):
            return False
        s3_client.upload_file(out_path, final_key)            # só após render local ok
        return True
    except Exception as e:
        log.warning("[endscreen] clip %s falhou (%s); sem tela final", content_key, e)
        return False
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        _burn_sem.release()


def _serve_clip(content_key: str, es_id, dl_name):
    """302 pro entregável do clipe, anexando a tela final (es_id) se houver. Sem es_id, serve o content
    direto. Com es_id, garante <content>_es.mp4 e serve ele; se o anexo falhar, serve o content sem
    tela final (o clipe sempre toca). Só pode ser chamado FORA do semáforo de queima (o anexo o
    adquire)."""
    if not es_id:
        return _clip_redirect(content_key, dl_name)
    final_key = _endscreen_key(content_key)
    if final_key == content_key:                              # chave sem .mp4 (defensivo)
        return _clip_redirect(content_key, dl_name)
    if s3_client.exists(final_key):                           # cache quente do final
        return _clip_redirect(final_key, dl_name)
    if _produce_endscreen(content_key, es_id, final_key):
        return _clip_redirect(final_key, dl_name)
    return _clip_redirect(content_key, dl_name)               # anexo falhou: degrada (clipe toca)


def _ensure_burn(clip_id, base_key, target_key, do_caps, do_music, segments_json,
                 caption_y, caption_style, music, trim_needed, in_pt, out_pt):
    """Garante o content_key (queima/trim sob demanda) no S3. Devolve a chave a SERVIR (target_key se
    renderizou/existe, ou base_key quando não há nada a aplicar), ou None em fallback (servir base SEM
    tela final). Gerencia o semáforo GLOBAL de queima inteiramente (sempre libera antes de retornar),
    pra o anexo da tela final (que readquire o semáforo) nunca dar deadlock."""
    if s3_client.exists(target_key):                          # cache quente
        return target_key
    with _clip_burn_lock(clip_id):
        if s3_client.exists(target_key):                      # outra thread renderizou
            return target_key
        if not _burn_sem.acquire(timeout=_BURN_QUEUE_TIMEOUT):
            log.warning("[burn] fila de queima cheia para %s; servindo base", clip_id)
            return None                                       # fallback: base sem tela final
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

    use_caps = bool(job.get("captions", 1)) and bool(segments_json)
    use_music = bool(music and music.get("music_key"))

    es_id = _resolve_end_screen_id(job, clip_id)         # tela final efetiva (id) ou None

    if want_trim:
        # Export sem legenda: corte cru reenquadrado (sem legenda, sem música). A tela final ENTRA
        # aqui também (decisão do dono: em todo entregável), anexada por _serve_clip. Sem trim real,
        # a base já é o content.
        if not trim_needed:
            return _serve_clip(base_key, es_id, dl_name)
        target_key = base_key.replace("_base.mp4", "_trim.mp4")
        do_caps = do_music = False
    else:
        # Sem queima/trim (clipe antigo, ou novo sem legenda/música e sem trim): o content é a base
        # (a tela final ainda pode entrar por cima dela).
        if not (is_new and (use_caps or use_music or trim_needed)):
            return _serve_clip(base_key, es_id, dl_name)
        target_key = cap_key
        do_caps, do_music = use_caps, use_music

    # Garante o content (queima/trim sob demanda) e SÓ DEPOIS anexa a tela final (fora do semáforo
    # de queima, que o anexo readquire). _ensure_burn devolve None em fallback de queima.
    content_key = _ensure_burn(clip_id, base_key, target_key, do_caps, do_music, segments_json,
                               caption_y, caption_style, music, trim_needed, in_pt, out_pt)
    if content_key is None:                              # fallback da queima: serve a base SEM tela final
        return _clip_redirect(base_key, dl_name)
    return _serve_clip(content_key, es_id, dl_name)


@app.route("/api/clips/<path:clip_id>/base")
@login_required
def clip_base(clip_id):
    """Entrega o clipe SEM a legenda queimada (302 pra base no S3). Usado pelo editor
    de timeline do front: a legenda e sobreposta AO VIVO no preview (sem duplicar com a
    queimada), e a queima final continua acontecendo sob demanda em /video. So faz sentido
    pra clipe novo (_base.mp4); clipe antigo (_9x16.mp4) cai na propria base mesmo."""
    _job, _idx, base_key = _clip_lookup(clip_id)
    return _clip_redirect(base_key)


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
    if s3_client.exists(thumb_key):
        return _clip_redirect(thumb_key)
    with _clip_thumb_lock(clip_id):
        if s3_client.exists(thumb_key):                      # outra thread acabou de gerar
            return _clip_redirect(thumb_key)
        if not _thumb_sem.acquire(timeout=config.THUMB_QUEUE_TIMEOUT):
            abort(404)                                       # fila cheia: card sem poster, re-tenta depois
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
                return _clip_redirect(thumb_key)
            log.warning("[thumb] clip %s: ffmpeg falhou (%s)", clip_id, r.stderr[-200:])
            abort(404)
        finally:
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
    try:
        peaks = _compute_peaks(s3_client.get_presigned_url(base_key))
        if peaks:
            db.set_clip_waveform(clip_id, json.dumps(peaks))
        return jsonify({"peaks": peaks})
    except Exception as e:
        log.warning("[waveform] clip %s: %s", clip_id, e)
        return jsonify({"peaks": []})
    finally:
        _thumb_sem.release()


_STRIP_TIMEOUT = 60
_STRIP_COLS = 12   # nº de frames no sprite; o front estica o sprite na largura da trilha (N exato não importa)


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
    if s3_client.exists(strip_key):
        return jsonify({"url": s3_client.get_presigned_url(strip_key)})
    if not _thumb_sem.acquire(timeout=config.THUMB_QUEUE_TIMEOUT):
        return jsonify({"url": None})
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
    editable = bool(raw) and "_base.mp4" in base_key
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
    # Tela final: override do clipe (None/"none"/id), o efetivo após herança, o default do projeto,
    # e a biblioteca de opções (pro editor montar o toggle/seletor sem outra request).
    return jsonify({"segments": segs, "editable": editable, "music": music_info,
                    "caption_y": caption_y, "style": style_obj, "bounds": bounds,
                    "end_screen": db.get_clip_end_screen(clip_id),
                    "end_screen_effective": _resolve_end_screen_id(job, clip_id),
                    "project_end_screen": job.get("end_screen"),
                    "end_screens": _end_screen_options()})


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


@app.route("/api/end_screens")
@login_required
def end_screens_list():
    """Biblioteca de telas finais disponíveis (id + rótulo), pro front montar os seletores."""
    return jsonify({"end_screens": _end_screen_options()})


@app.route("/api/clips/<path:clip_id>/end_screen", methods=["PUT"])
@login_required
def clip_end_screen_put(clip_id):
    """Override da tela final do clipe: body {end_screen: null|"none"|"<id>"}. null = herda o projeto;
    "none" = remove neste clipe; "<id>" = força essa tela. Invalida só os finais (_es): NÃO re-queima
    a legenda (o content cap/trim continua válido). Exige clipe editável (tem base re-renderizável)."""
    job, idx, base_key = _clip_lookup(clip_id)
    if "_base.mp4" not in base_key:
        return jsonify({"error": "Este clipe não é editável."}), 409
    if db.get_clip_captions(clip_id) is None:
        return jsonify({"error": "Este clipe não tem legenda editável."}), 409
    data = request.get_json(silent=True) or {}
    val = data.get("end_screen")
    if val is not None and val != "none" and val not in config.END_SCREENS:
        return jsonify({"error": "Tela final inválida."}), 400
    db.set_clip_end_screen(clip_id, val)
    _invalidate_endscreen(base_key)
    return jsonify({"ok": True, "end_screen": val,
                    "end_screen_effective": _resolve_end_screen_id(job, clip_id)})


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


def _can_manage(job) -> bool:
    """Quem pode destruir um projeto na biblioteca compartilhada: o DONO (quem subiu) ou
    um admin. Os demais usuários acessam e editam, mas não excluem/cancelam. Job legado
    (dono NULL) não tem quem reivindicar, então só admin gerencia."""
    user = db.get_user(session["user"]) or {}
    if user.get("is_admin"):
        return True
    owner = job.get("user_email")
    return bool(owner) and owner == session["user"]


@app.route("/api/projects/<job_id>", methods=["DELETE"])
@login_required
def delete_project(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Projeto não encontrado."}), 404
    if not _can_manage(job):
        return jsonify({"error": "Só o dono do projeto ou um admin pode excluir."}), 403
    # Apaga os arquivos no S3 (áudio + clipes) e depois o registro. Se a limpeza do
    # S3 falhar parcialmente, ainda removemos o job pra UI ficar consistente (o que
    # sobrar no bucket é órfão e pode ser varrido depois).
    try:
        s3_client.delete_prefix(f"jobs/{job_id}/")
    except Exception as e:
        log.warning("[delete] falha ao limpar S3 do job %s: %s", job_id, e)
    db.delete_job(job_id)
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

    file = request.files.get("video")
    if not file or not file.filename or not _allowed(file.filename):
        return jsonify({"error": "Arquivo inválido. Use mp4, mov, avi, mkv ou webm."}), 400

    min_duration = request.form.get("min_duration", type=int)
    max_duration = request.form.get("max_duration", type=int)
    if min_duration is None or max_duration is None or min_duration >= max_duration:
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
    try:
        info = processor.youtube_probe(url)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    finally:
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


@app.route("/api/callback", methods=["POST"])
def callback():
    if not _is_trusted_caller(request.remote_addr):
        abort(403)
    data = request.get_json(force=True)
    job_id = data.get("job_id")
    job = db.get_job(job_id)
    if not job_id or not job:
        return jsonify({"error": "job_id inválido"}), 400
    # Se o job foi cancelado durante a transcrição, não retoma o corte.
    if job["status"] == "error":
        return jsonify({"ok": True, "skipped": "job cancelado ou em erro"})
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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=config.FLASK_PORT)
