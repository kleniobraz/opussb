import os
import sys
import math
import time
import uuid
import json
import logging
import ipaddress
import functools
import threading
import subprocess
import tempfile
import shutil
from datetime import datetime, timezone, timedelta

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
    SESSION_COOKIE_SAMESITE="Lax",
    # secure=False porque ainda não há HTTPS. Quando subir TLS, virar True.
    SESSION_COOKIE_SECURE=False,
    PERMANENT_SESSION_LIFETIME=timedelta(days=config.SESSION_DAYS),
    # Teto de upload. Secundário ao Nginx (client_max_body_size 15G), protege
    # o caminho direto :5001. 16 GiB fica acima do Nginx pra ele ser o 1o gate.
    MAX_CONTENT_LENGTH=16 * 1024 * 1024 * 1024,
)

# Serializa a admissão de upload (check de vaga + reserva). Gunicorn roda 1 worker
# com 4 threads, então um lock de processo fecha a race de uploads simultâneos.
_upload_lock = threading.Lock()

db.init_db()
db.init_users()
db.init_favorites()
db.init_preferences()
db.init_clip_captions()
db.init_clip_music()
db.init_sessions()

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
        if not _session_email():
            # Sessão ausente/revogada/legada (cookie sem sid): limpa o cookie e 401 no
            # MESMO formato de sempre, pro front cair na tela de login sem regressão.
            session.clear()
            return jsonify({"error": "Não autenticado.", "auth": False}), 401
        return fn(*args, **kwargs)
    return wrapper


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

    _login_clear(key)
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
        sid = str(uuid.uuid4())
        db.create_session(sid, email, ip, ua)
    session.permanent = True
    session["user"] = email
    session["sid"] = sid
    return jsonify({"email": email, "must_change_password": bool(user["must_change_password"])})


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
    if not user:
        session.clear()
        return jsonify({"error": "Não autenticado.", "auth": False}), 401
    return jsonify({"email": email, "must_change_password": bool(user["must_change_password"])})


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
            "thumbnail_url": thumbnail_url,
        })
    return clips


def _project_from_job(job):
    """Mapeia um job para o shape Project que o front consome (um job = um projeto)."""
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
    }


@app.route("/api/projects")
@login_required
def list_projects():
    jobs = db.list_jobs(session["user"])
    return jsonify({"projects": [_project_from_job(j) for j in jobs], "total": len(jobs)})


@app.route("/api/projects/<job_id>")
@login_required
def get_project(job_id):
    job = db.get_job(job_id)
    if not job or (job.get("user_email") and job["user_email"] != session["user"]):
        return jsonify({"error": "Projeto não encontrado."}), 404
    proj = _project_from_job(job)
    proj["clips"] = _clip_objects(job_id, job["result_urls"], _thumb_url(job)) if job["result_urls"] else []
    return jsonify(proj)


@app.route("/api/projects/<job_id>/thumb")
@login_required
def project_thumb(job_id):
    """Thumbnail do projeto (frame real do melhor clipe) via 302 pra URL presignada.
    Mesmo padrão de auth da rota de vídeo: exige sessão e dono. 404 se não houver frame."""
    job = db.get_job(job_id)
    if not job or (job.get("user_email") and job["user_email"] != session["user"]):
        abort(404)
    key = job.get("thumbnail_key")
    if not key:
        abort(404)
    return redirect(s3_client.get_presigned_url(key), code=302)


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


def _build_burn_cmd(base_path: str, ass_path, music_path, music_vol, out_path: str) -> list:
    """Monta o comando ffmpeg da queima (função pura, sem efeito colateral, pra ser
    testável). Regras de mapeamento de stream:
      - vídeo: filtro de legenda (.ass) se houver, senão o vídeo da base direto;
      - áudio: se houver música, mixa fala+música; SENÃO mapeia o áudio original da
        base explicitamente (`0:a?`). Sem esse map, o `-map [v]` da legenda desliga
        o mapeamento automático do ffmpeg e o clipe sairia MUDO (era o bug). O `?`
        deixa o áudio opcional pra não quebrar caso a base não tenha trilha."""
    cmd = ["ffmpeg", "-y", "-i", base_path]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]   # loopa a música pra cobrir o clipe
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
    cmd += ["-movflags", "+faststart"]   # moov no início: playback progressivo no navegador
    if music_path:
        cmd += ["-shortest"]
    cmd += [out_path]
    return cmd


def _burn_clip(base_path: str, ass_path, music_path, music_vol, out_path: str) -> None:
    """Renderiza o clipe final a partir da base (já 1080x1920): queima a legenda
    (.ass, se houver) no vídeo e mixa a música de fundo (se houver, em loop e com
    volume) no áudio. Levanta se o ffmpeg falhar, sair vazio, ou estourar
    _BURN_TIMEOUT (o subprocess é morto). O chamador só sobe pro S3 DEPOIS que isto
    retorna ok, então uma render que falha no meio nunca vira um _cap.mp4 corrompido."""
    cmd = _build_burn_cmd(base_path, ass_path, music_path, music_vol, out_path)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=_BURN_TIMEOUT)
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        raise RuntimeError(f"ffmpeg render falhou: {r.stderr[-400:]}")


def _invalidate_cap(base_key: str) -> None:
    """Apaga o _cap.mp4 (idempotente). Chamado quando legenda ou música mudam, pra
    a próxima abertura re-renderizar com o conteúdo novo."""
    cap_key = base_key.replace("_base.mp4", "_cap.mp4")
    if cap_key == base_key:
        return
    try:
        s3_client.delete_object(cap_key)
    except Exception as e:
        log.warning("[cache] falha ao invalidar %s: %s", cap_key, e)


@app.route("/api/clips/<path:clip_id>/video")
@login_required
def clip_video(clip_id):
    """Entrega o vídeo do clipe via 302 pra URL presignada do S3. Queima a legenda
    sob demanda (cacheada). Em qualquer falha de queima, faz 302 pra base SEM
    legenda (o clipe sempre toca) e nunca deixa _cap.mp4 corrompido no S3."""
    job, idx, base_key = _clip_lookup(clip_id)

    segments_json = db.get_clip_captions(clip_id)
    music = db.get_clip_music(clip_id)
    cap_key = base_key.replace("_base.mp4", "_cap.mp4")
    is_new = cap_key != base_key                         # _base (novo) vs _9x16 (antigo, já queimado)
    use_caps = bool(job.get("captions", 1)) and bool(segments_json)
    use_music = bool(music and music.get("music_key"))
    # Sem render quando: clipe antigo, ou nada a aplicar (sem legenda e sem música).
    if not (is_new and (use_caps or use_music)):
        return redirect(s3_client.get_presigned_url(base_key), code=302)

    if s3_client.exists(cap_key):                        # cache quente
        return redirect(s3_client.get_presigned_url(cap_key), code=302)

    with _clip_burn_lock(clip_id):
        if s3_client.exists(cap_key):                    # outra thread renderizou enquanto esperávamos
            return redirect(s3_client.get_presigned_url(cap_key), code=302)
        tmp = tempfile.mkdtemp(prefix="burn_")
        try:
            base_path = os.path.join(tmp, "base.mp4")
            out_path = os.path.join(tmp, "out.mp4")
            s3_client.download_file(base_key, base_path)
            ass_path = None
            if use_caps:
                ass = captions.build_ass_from_segments(json.loads(segments_json))
                if ass:
                    ass_path = os.path.join(tmp, "cap.ass")
                    with open(ass_path, "w", encoding="utf-8") as f:
                        f.write(ass)
            music_path = None
            if use_music:
                music_path = os.path.join(tmp, "music" + os.path.splitext(music["music_key"])[1])
                s3_client.download_file(music["music_key"], music_path)
            if not ass_path and not music_path:          # nada a aplicar -> base
                return redirect(s3_client.get_presigned_url(base_key), code=302)
            _burn_clip(base_path, ass_path, music_path, (music or {}).get("volume"), out_path)
            s3_client.upload_file(out_path, cap_key)      # só após render local ok
        except Exception as e:
            log.warning("[burn] clip %s falhou (%s); servindo base", clip_id, e)
            return redirect(s3_client.get_presigned_url(base_key), code=302)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return redirect(s3_client.get_presigned_url(cap_key), code=302)


# ─── Legenda editável do clipe (correção pelo usuário; base do mini editor) ───
_MAX_SEGMENTS = 1000
_MAX_TEXT_LEN = 300


def _clip_lookup(clip_id):
    """Resolve clip_id "<job_id>:<idx>" -> (job, idx, base_key), com auth de dono.
    Aborta 404 se o formato, o job, o dono ou o índice não baterem."""
    job_id, _, idx_s = clip_id.rpartition(":")
    try:
        idx = int(idx_s)
    except ValueError:
        abort(404)
    if not job_id:
        abort(404)
    job = db.get_job(job_id)
    if not job or (job.get("user_email") and job["user_email"] != session["user"]):
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
    return jsonify({"segments": segs, "editable": editable, "music": music_info})


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
    _invalidate_cap(base_key)   # próxima abertura re-renderiza com o texto corrigido
    return jsonify({"ok": True, "segments": segs})


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


@app.route("/api/projects/<job_id>", methods=["DELETE"])
@login_required
def delete_project(job_id):
    job = db.get_job(job_id)
    if not job or (job.get("user_email") and job["user_email"] != session["user"]):
        return jsonify({"error": "Projeto não encontrado."}), 404
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
    if not job or (job.get("user_email") and job["user_email"] != session["user"]):
        return jsonify({"error": "Job não encontrado."}), 404
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
    captions_on = 1
    try:
        _p = json.loads(db.get_preferences(session["user"]) or "{}")
        if isinstance(_p, dict) and "captions" in _p:
            captions_on = 1 if _p["captions"] else 0
    except (TypeError, ValueError):
        pass
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
