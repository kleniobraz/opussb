import sqlite3
import os
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager


def _db_path() -> str:
    return os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "opussb.db"))


@contextmanager
def _conn():
    # timeout=30: o gunicorn roda 1 worker com 4 threads e várias rotas escrevem
    # (update_job, clip_captions, clip_music). Em vez de estourar "database is locked"
    # na hora, o SQLite espera o lock liberar por até 30s.
    conn = sqlite3.connect(_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL deixa leitor e escritor coexistirem (status/polling do front não bloqueia o
    # processor escrevendo). busy_timeout casa com o timeout acima no nível do SQLite.
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_column(conn, table: str, col: str, decl: str) -> None:
    """Migração idempotente: adiciona a coluna se ainda não existir."""
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


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
        # Dono do job. Jobs antigos ficam com NULL (legado = visível a todos).
        _ensure_column(conn, "jobs", "user_email", "TEXT")
        # Legenda queimada ligada/desligada por job. Default 1 (ligada) pro piloto.
        _ensure_column(conn, "jobs", "captions", "INTEGER DEFAULT 1")
        # Frame real do projeto (thumbnail do card). NULL = sem frame: jobs antigos
        # degradam pra capa generativa no front, sem quebrar.
        _ensure_column(conn, "jobs", "thumbnail_key", "TEXT")
        # Índices: list_jobs filtra por dono e ordena por created_at; count_active e
        # reap_stale filtram por status. Baratos e ajudam conforme o histórico cresce.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_email, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")


def init_favorites() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_email TEXT NOT NULL,
                clip_id    TEXT NOT NULL,          -- formato "job_id:index"
                created_at TEXT,
                PRIMARY KEY (user_email, clip_id)
            )
        """)


def init_preferences() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                user_email TEXT PRIMARY KEY,
                data       TEXT,                   -- JSON serializado das prefs
                updated_at TEXT
            )
        """)


def init_clip_captions() -> None:
    """Legenda editável por clipe (base da queima sob demanda). clip_id é o mesmo
    "job_id:index" usado em favoritos e no _clip_objects."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clip_captions (
                clip_id    TEXT PRIMARY KEY,       -- "<job_id>:<index>"
                segments   TEXT NOT NULL,          -- JSON [{start,end,text}] relativo ao clipe
                updated_at TEXT
            )
        """)


def init_clip_music() -> None:
    """Música de fundo por clipe (mixada na queima sob demanda). Opcional: clipe
    sem registro = sem música. clip_id é o mesmo "job_id:index"."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clip_music (
                clip_id    TEXT PRIMARY KEY,       -- "<job_id>:<index>"
                music_key  TEXT NOT NULL,          -- chave S3 do arquivo de música
                volume     REAL NOT NULL DEFAULT 0.15,  -- 0..1 (fundo, abaixo da fala)
                updated_at TEXT
            )
        """)


def init_users() -> None:
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email                TEXT PRIMARY KEY,
                password_hash        TEXT NOT NULL,
                must_change_password INTEGER NOT NULL DEFAULT 1,
                created_at           TEXT,
                updated_at           TEXT
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(job_id: str, filename: str, min_duration: int, max_duration: int,
               local_path: str, user_email: str | None = None, captions: int = 1) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, filename, status, progress, min_duration, max_duration,
                local_path, user_email, captions, created_at, updated_at)
               VALUES (?, ?, 'queued', 5, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, filename, min_duration, max_duration, local_path, user_email,
             1 if captions else 0, _now(), _now()),
        )


def list_jobs(user_email: str | None = None) -> list:
    """Jobs do usuário + os legados (dono NULL, criados antes do escopo por dono),
    pra histórico não sumir. Sem user_email, retorna tudo (uso administrativo)."""
    with _conn() as conn:
        if user_email:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_email = ? OR user_email IS NULL ORDER BY created_at DESC",
                (user_email,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def add_favorite(user_email: str, clip_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_email, clip_id, created_at) VALUES (?, ?, ?)",
            (user_email, clip_id, _now()),
        )


def remove_favorite(user_email: str, clip_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM favorites WHERE user_email = ? AND clip_id = ?", (user_email, clip_id))


def list_favorites(user_email: str) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT clip_id FROM favorites WHERE user_email = ? ORDER BY created_at DESC", (user_email,)
        ).fetchall()
        return [r["clip_id"] for r in rows]


def delete_job(job_id: str) -> None:
    """Remove o job, favoritos, legendas editáveis e música que apontem para clipes
    dele (clip_id "job_id:N"). Só é chamado em remoção explícita do usuário; o
    pipeline nunca chama isto."""
    with _conn() as conn:
        conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM favorites WHERE clip_id LIKE ?", (job_id + ":%",))
        conn.execute("DELETE FROM clip_captions WHERE clip_id LIKE ?", (job_id + ":%",))
        conn.execute("DELETE FROM clip_music WHERE clip_id LIKE ?", (job_id + ":%",))


def get_preferences(user_email: str):
    with _conn() as conn:
        row = conn.execute(
            "SELECT data FROM preferences WHERE user_email = ?", (user_email,)
        ).fetchone()
        return row["data"] if row else None


def set_preferences(user_email: str, data: str) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO preferences (user_email, data, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(user_email) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at""",
            (user_email, data, _now()),
        )


def get_clip_captions(clip_id: str):
    """Devolve o JSON (string) dos segmentos editáveis do clipe, ou None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT segments FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["segments"] if row else None


def set_clip_captions(clip_id: str, segments: str) -> None:
    """Grava/atualiza os segmentos editáveis do clipe (JSON serializado)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO clip_captions (clip_id, segments, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(clip_id) DO UPDATE SET segments = excluded.segments, updated_at = excluded.updated_at""",
            (clip_id, segments, _now()),
        )


def get_clip_music(clip_id: str):
    """Devolve {music_key, volume} do clipe, ou None se não houver música."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT music_key, volume FROM clip_music WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return {"music_key": row["music_key"], "volume": row["volume"]} if row else None


def set_clip_music(clip_id: str, music_key: str, volume: float) -> None:
    """Grava/atualiza a música de fundo do clipe (chave S3 + volume 0..1)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO clip_music (clip_id, music_key, volume, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(clip_id) DO UPDATE SET music_key = excluded.music_key,
                   volume = excluded.volume, updated_at = excluded.updated_at""",
            (clip_id, music_key, volume, _now()),
        )


def delete_clip_music(clip_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM clip_music WHERE clip_id = ?", (clip_id,))


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


def get_user(email: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None


def upsert_user(email: str, password_hash: str, must_change: bool = True) -> None:
    """Cria ou redefine um usuário. Usado pelo manage_users.py para cadastrar e
    resetar com a senha padrão (must_change=True força troca no primeiro acesso)."""
    email = email.strip().lower()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO users (email, password_hash, must_change_password, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                   password_hash = excluded.password_hash,
                   must_change_password = excluded.must_change_password,
                   updated_at = excluded.updated_at""",
            (email, password_hash, 1 if must_change else 0, _now(), _now()),
        )


def set_user_password(email: str, password_hash: str) -> None:
    """Troca de senha pelo próprio usuário: limpa o must_change_password."""
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE email = ?",
            (password_hash, _now(), email.strip().lower()),
        )


def list_users() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT email, must_change_password, created_at FROM users ORDER BY email"
        ).fetchall()
        return [dict(r) for r in rows]


def reap_stale_jobs(stale_minutes: int) -> int:
    """Marca como error os jobs presos em estado não terminal sem atualização há
    mais de stale_minutes (ex: callback do N8N perdido). Como MAX_CONCURRENT_JOBS=1,
    um job preso trava a fila inteira; isto libera a vaga. Retorna quantos liberou."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
    msg = f"timeout: sem atualização há mais de {stale_minutes}min (callback do N8N perdido?)"
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'error', error = ?, updated_at = ? "
            "WHERE status NOT IN ('done', 'error') AND updated_at < ?",
            (msg, _now(), cutoff),
        )
        return cur.rowcount
