import os
import uuid
import json
import threading
from flask import Flask, request, jsonify, abort
import config
import db
import s3_client
import processor

app = Flask(__name__, static_folder="static", static_url_path="")
db.init_db()

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def _allowed(filename: str) -> bool:
    return os.path.splitext(filename.lower())[1] in ALLOWED_EXTENSIONS


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/status/<job_id>")
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
        keys = json.loads(job["result_urls"])
        resp["clips"] = [s3_client.get_presigned_url(k, expiry_seconds=604800) for k in keys]

    if job["status"] == "error":
        resp["error"] = job["error"]
        if job["result_urls"]:
            keys = json.loads(job["result_urls"])
            resp["partial_clips"] = [s3_client.get_presigned_url(k, expiry_seconds=604800) for k in keys]

    return jsonify(resp)


@app.route("/api/upload", methods=["POST"])
def upload():
    if db.count_active_jobs() >= config.MAX_CONCURRENT_JOBS:
        return jsonify({
            "error": "Um vídeo já está em processamento! Vai tomar uma água, respirar e aguardar a conclusão antes de enviar novo vídeo :)"
        }), 429

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
    os.makedirs(job_dir, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower()
    local_path = os.path.join(job_dir, f"original{ext}")
    file.save(local_path)

    db.create_job(job_id, file.filename, min_duration, max_duration, local_path)
    threading.Thread(target=processor.run_thread1, args=(job_id,), daemon=True).start()

    return jsonify({"job_id": job_id}), 202


_TRUSTED_CALLERS = {"127.0.0.1", "172.17.0.1"}


@app.route("/api/callback", methods=["POST"])
def callback():
    if request.remote_addr not in _TRUSTED_CALLERS:
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
    if request.remote_addr not in _TRUSTED_CALLERS:
        abort(403)
    data = request.get_json(force=True)
    allowed = {"runpod_job_id", "status", "progress", "error"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nenhum campo válido."}), 400
    db.update_job(job_id, **updates)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.FLASK_PORT)
