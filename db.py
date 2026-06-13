import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager


def _db_path() -> str:
    return os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "opussb.db"))


@contextmanager
def _conn():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id        TEXT PRIMARY KEY,
                filename      TEXT NOT NULL,
                status        TEXT NOT NULL,
                progress      INTEGER DEFAULT 0,
                min_duration  INTEGER,
                max_duration  INTEGER,
                local_path    TEXT,
                audio_s3_url  TEXT,
                runpod_job_id TEXT,
                transcript    TEXT,
                clips_json    TEXT,
                result_urls   TEXT,
                error         TEXT,
                created_at    TEXT,
                updated_at    TEXT
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(job_id: str, filename: str, min_duration: int, max_duration: int, local_path: str) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, filename, status, progress, min_duration, max_duration, local_path, created_at, updated_at)
               VALUES (?, ?, 'queued', 5, ?, ?, ?, ?, ?)""",
            (job_id, filename, min_duration, max_duration, local_path, _now(), _now()),
        )


def get_job(job_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None


def update_job(job_id: str, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    with _conn() as conn:
        conn.execute(f"UPDATE jobs SET {fields} WHERE job_id = ?", values)


def count_active_jobs() -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status NOT IN ('done', 'error')"
        ).fetchone()
        return row[0]
