import os
import uuid
import json
import ipaddress
import functools
import threading
from datetime import timedelta

from flask import Flask, request, jsonify, abort, session
from werkzeug.security import generate_password_hash, check_password_hash

import config
import db
import s3_client
import processor

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

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MIN_PASSWORD_LEN = 8


def _allowed(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


# ─── Autenticação ────────────────────────────────────────────────────────────

def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user"):
            return jsonify({"error": "Não autenticado.", "auth": False}), 401
        return fn(*args, **kwargs)
    return wrapper


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = db.get_user(email)
    # Mensagem genérica de propósito: não revela se o email existe.
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Email ou senha inválidos."}), 401

    session.permanent = True
    session["user"] = email
    return jsonify({"email": email, "must_change_password": bool(user["must_change_password"])})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    email = session.get("user")
    if not email:
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
    return jsonify({"ok": True})


# ─── Rotas do app ────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


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
    }

    if job["status"] == "done" and job["result_urls"]:
        resp["clips"] = _clip_objects(job_id, job["result_urls"])

    if job["status"] == "error":
        resp["error"] = job["error"]
        if job["result_urls"]:
            resp["partial_clips"] = _clip_objects(job_id, job["result_urls"])

    return jsonify(resp)


def _clip_objects(job_id, result_urls_json):
    """Constrói a lista de Clip (score/title/reason + URL presignada) a partir do
    result_urls. Aceita o formato novo (objeto por clipe) e o antigo (só a chave),
    pra jobs criados antes desta mudança não quebrarem."""
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
            "url": s3_client.get_presigned_url(key, expiry_seconds=604800),
            "title": meta.get("title"),
            "reason": meta.get("reason"),
            "score": meta.get("score"),
            "duration": meta.get("duration"),
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
        "thumbnail_url": None,
        "clip_count": len(items) if job["status"] == "done" else 0,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "duration": None,
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
    proj["clips"] = _clip_objects(job_id, job["result_urls"]) if job["result_urls"] else []
    return jsonify(proj)


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
            cache[jid] = _clip_objects(jid, job["result_urls"]) if job and job["result_urls"] else []
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
        print(f"[delete] falha ao limpar S3 do job {job_id}: {e}")
    db.delete_job(job_id)
    return jsonify({"ok": True})


_PREF_KEYS = {"defaultMin", "defaultMax", "aspect", "language", "captions", "autoplayPreview"}


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
    # Whitelist das chaves conhecidas: não guardar blob arbitrário do cliente.
    clean = {k: data[k] for k in _PREF_KEYS if k in data}
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

    job_id = str(uuid.uuid4())
    job_dir = os.path.join(config.JOB_DIR, job_id)
    ext = os.path.splitext(file.filename)[1].lower()
    local_path = os.path.join(job_dir, f"original{ext}")

    # Admissão atômica: reap + check de vaga + reserva, tudo sob o lock, fechando
    # a race onde dois uploads simultâneos passariam o check antes de qualquer um
    # gravar. A vaga é reservada criando o job como 'queued' (conta em count_active).
    with _upload_lock:
        reaped = db.reap_stale_jobs(config.JOB_STALE_MINUTES)
        if reaped:
            print(f"[watchdog] {reaped} job(s) preso(s) liberado(s) por timeout")
        if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
            return jsonify({
                "error": "Um vídeo já está em processamento! Vai tomar uma água, respirar e aguardar a conclusão antes de enviar novo vídeo :)"
            }), 429
        db.create_job(job_id, file.filename, min_duration, max_duration, local_path, session["user"])

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
    if not job_id or not db.get_job(job_id):
        return jsonify({"error": "job_id inválido"}), 400
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
