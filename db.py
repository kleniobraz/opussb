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
        # Tela final PADRAO do projeto (id da tela na biblioteca, ex.: "clubcast"). NULL = nenhuma
        # (a marca so entra nos projetos que o usuario escolher; nao estampa cliente errado). Os
        # clipes herdam isto, com override por clipe em clip_captions.end_screen.
        _ensure_column(conn, "jobs", "end_screen", "TEXT")
        # Tipo do job (BACKEND_CONTEXT §39): 'clips' = pipeline 9:16 de hoje, 'podcast' =
        # pipeline 16:9 pra YouTube. O DEFAULT preenche os jobs existentes e todo INSERT
        # do create_job (que não menciona a coluna), então o fluxo de clipes segue intacto.
        _ensure_column(conn, "jobs", "job_type", "TEXT NOT NULL DEFAULT 'clips'")
        # Vinheta escolhida pro job de podcast (id na futura biblioteca `vinhetas`, mesmo
        # padrão do end_screen). NULL sempre em jobs de clipes.
        _ensure_column(conn, "jobs", "vinheta_id", "TEXT")
        # Sugestões de gancho do podcast (hooks.py): JSON com a lista {start,end,text,reason}
        # + modelo/usage/custo da chamada. O dono escolhe/ajusta na tela de aprovação.
        _ensure_column(conn, "jobs", "hooks_json", "TEXT")
        # Escolha humana da tela de aprovação (§39 Parte 4): JSON {index,start,end,
        # approved_by,approved_at}. A montagem (Parte 5) consome daqui, não do hooks_json.
        _ensure_column(conn, "jobs", "hook_choice", "TEXT")
        # Key S3 do episódio final montado (§39 Parte 5: gancho + vinheta + podcast).
        # Fora de result_urls de propósito: aquele formato é de clipes (_clip_objects).
        _ensure_column(conn, "jobs", "podcast_final_key", "TEXT")
        # Segundos cortados do início do corpo bruto (§39 Parte 2), gravado ANTES do corte
        # rodar (não só no sucesso), igual hook_choice é gravado antes da montagem: permite
        # o front distinguir erro do corte de erro de outra fase. NULL = nunca cortou.
        _ensure_column(conn, "jobs", "intro_cut_at", "REAL")
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
        # Posicao vertical da legenda (fracao 0..1 do centro, do topo). NULL = posicao
        # padrao do estilo SB (alignment 2 + MARGIN_V). O usuario arrasta no editor.
        _ensure_column(conn, "clip_captions", "caption_y", "REAL")
        # Estilo da fonte por clipe (JSON: {size, tracking, color, shadow}). NULL = estilo SB
        # padrao (constantes do captions.py). O usuario ajusta no painel "Estilo" do editor.
        _ensure_column(conn, "clip_captions", "caption_style", "TEXT")
        # Handles (margem de corte): o clip_NN_base.mp4 passa a ser a JANELA estendida; estes 4
        # campos situam o corte "oficial" dentro dela. window_start = onde a janela comeca no
        # video original (s absolutos); window_dur = duracao real da janela (= base.mp4);
        # in_point/out_point = entrada/saida do corte, relativos a janela (o usuario arrasta no
        # editor). NULL = clipe sem handles (legado ou CLIP_HANDLE_SECONDS=0): o corte e a janela
        # inteira e a queima nunca aplica trim.
        _ensure_column(conn, "clip_captions", "window_start", "REAL")
        _ensure_column(conn, "clip_captions", "window_dur", "REAL")
        _ensure_column(conn, "clip_captions", "in_point", "REAL")
        _ensure_column(conn, "clip_captions", "out_point", "REAL")
        # Palavras com timestamp (output.word_timestamps do Whisper) recortadas por clipe, relativas
        # a janela: JSON [{start,end,word}]. NULL = clipe sem word-level (legado ou fetch falhou): a
        # legenda usa o metodo por frase e o editor cai no karaoke por frase. Pro editor OpusClip.
        _ensure_column(conn, "clip_captions", "words", "TEXT")
        # Picos da forma de onda do clipe (JSON [0..1]) pra a faixa de audio do editor, gerados sob
        # demanda no servidor (o WebAudio do navegador e bloqueado pelo CORS do S3). NULL = ainda
        # nao gerado (a 1a abertura do clipe no editor gera e cacheia aqui).
        _ensure_column(conn, "clip_captions", "waveform", "TEXT")
        # Tela final do clipe (OVERRIDE sobre o default do projeto, jobs.end_screen):
        #   NULL   = herda o default do projeto;
        #   "none" = o usuario removeu a tela final NESTE clipe (mesmo que o projeto tenha uma);
        #   "<id>" = forca uma tela especifica neste clipe (mesmo sem default no projeto).
        _ensure_column(conn, "clip_captions", "end_screen", "TEXT")
        # Duracao (s) da tela final A USAR neste clipe (encurtar arrastando na timeline). NULL =
        # usa a tela inteira; senao corta nos primeiros N segundos (clampado a duracao do asset).
        _ensure_column(conn, "clip_captions", "end_screen_dur", "REAL")
        # Keyframes do crop horizontal (editor de enquadramento, BACKEND §33). JSON
        # {"mode": "auto"|"manual", "kf": [{"t": <s rel. a janela>, "cx": <0..1 centro horizontal>}]}.
        # NULL = clipe sem crop manual (usa o enquadramento auto que ja esta no base.mp4). cx em
        # FRACAO da largura (resolucao-independente); o re-render converte pra px/borda no reframe_core.
        _ensure_column(conn, "clip_captions", "keyframes", "TEXT")


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


def init_end_screens() -> None:
    """Biblioteca de telas finais (outros) que o usuario sobe pelo sistema. id = slug/uuid;
    s3_key aponta pro mp4 no S3 (assets/end_screens/<id>.mp4); duration = duracao total do asset.
    Os projetos referenciam por id (jobs.end_screen) e os clipes herdam/sobrescrevem."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS end_screens (
                id         TEXT PRIMARY KEY,
                label      TEXT NOT NULL,
                s3_key     TEXT NOT NULL,
                duration   REAL,
                created_at TEXT
            )
        """)


def list_end_screens() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, label, s3_key, duration FROM end_screens ORDER BY created_at, id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_end_screen(es_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, label, s3_key, duration FROM end_screens WHERE id = ?", (es_id,)
        ).fetchone()
        return dict(row) if row else None


def add_end_screen(es_id: str, label: str, s3_key: str, duration) -> None:
    """Insere/atualiza uma tela final na biblioteca (upsert por id)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO end_screens (id, label, s3_key, duration, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET label=excluded.label, s3_key=excluded.s3_key,
                                             duration=excluded.duration""",
            (es_id, label, s3_key, duration, _now()),
        )


def ensure_end_screen(es_id: str, label: str, s3_key: str, duration) -> None:
    """Semeia uma tela final se ela ainda nao existir (nao sobrescreve edicoes do usuario)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO end_screens (id, label, s3_key, duration, created_at)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
            (es_id, label, s3_key, duration, _now()),
        )


def delete_end_screen(es_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM end_screens WHERE id = ?", (es_id,))


# ─── Vinhetas (biblioteca do pipeline de podcast, §39) ───────────────────────

def init_vinhetas() -> None:
    """Espelho de end_screens pro podcast: a vinheta entra entre o gancho e o corpo.
    s3_key aponta pro mp4 no S3 (assets/vinhetas/<id>.mp4); duration = duração do asset.
    Sem seed do config: a biblioteca nasce vazia e o time sobe pelo sistema."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vinhetas (
                id         TEXT PRIMARY KEY,
                label      TEXT NOT NULL,
                s3_key     TEXT NOT NULL,
                duration   REAL,
                created_at TEXT
            )
        """)


def list_vinhetas() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, label, s3_key, duration FROM vinhetas ORDER BY created_at, id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_vinheta(v_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, label, s3_key, duration FROM vinhetas WHERE id = ?", (v_id,)
        ).fetchone()
        return dict(row) if row else None


def add_vinheta(v_id: str, label: str, s3_key: str, duration) -> None:
    """Insere/atualiza uma vinheta na biblioteca (upsert por id)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO vinhetas (id, label, s3_key, duration, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET label=excluded.label, s3_key=excluded.s3_key,
                                             duration=excluded.duration""",
            (v_id, label, s3_key, duration, _now()),
        )


def delete_vinheta(v_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM vinhetas WHERE id = ?", (v_id,))


# ─── Glossário do revisor de legendas ────────────────────────────────────────

def init_glossary() -> None:
    """Glossário editável de nomes canônicos (marcas/produtos/pessoas) usado pela revisão de
    legendas. Biblioteca compartilhada: qualquer usuário ativo adiciona/edita; só admin exclui
    (regra na rota). O índice UNIQUE em term (case-insensitive) barra duplicata acidental."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS glossary (
                id         TEXT PRIMARY KEY,
                term       TEXT NOT NULL,
                category   TEXT,
                notes      TEXT,
                created_by TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_glossary_term ON glossary(term COLLATE NOCASE)"
        )


def list_glossary() -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, term, category, notes, created_by, created_at, updated_at "
            "FROM glossary ORDER BY term COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def get_glossary(term_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM glossary WHERE id = ?", (term_id,)).fetchone()
        return dict(row) if row else None


def add_glossary(term_id: str, term: str, category=None, notes=None, created_by=None) -> None:
    """Insere/atualiza um termo (upsert por id). Pode levantar IntegrityError se `term`
    colidir (case-insensitive) com outro id — a rota trata como 409."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO glossary (id, term, category, notes, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET term=excluded.term, category=excluded.category,
                   notes=excluded.notes, updated_at=excluded.updated_at""",
            (term_id, term, category, notes, created_by, _now(), _now()),
        )


def ensure_glossary(term_id: str, term: str, category=None, notes=None) -> None:
    """Semeia um termo se ainda não existir (não sobrescreve edições do usuário)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO glossary (id, term, category, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
            (term_id, term, category, notes, _now(), _now()),
        )


def delete_glossary(term_id: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM glossary WHERE id = ?", (term_id,))


# ─── Revisões de legenda (job assíncrono) ────────────────────────────────────

def init_caption_reviews() -> None:
    """Revisões de legenda: upload -> frames -> leitura (visão) -> revisão -> relatório. A
    mídia é efêmera; a linha guarda status/progresso, o texto lido, o relatório e tokens/custo."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS caption_reviews (
                id             TEXT PRIMARY KEY,
                user_email     TEXT,
                filename       TEXT,
                status         TEXT NOT NULL,
                progress       INTEGER DEFAULT 0,
                duration       REAL,
                frames_count   INTEGER,
                legenda_fala   TEXT,
                graficos_json  TEXT,
                report_json    TEXT,
                read_in_tok    INTEGER,
                read_out_tok   INTEGER,
                review_in_tok  INTEGER,
                review_out_tok INTEGER,
                cost_usd       REAL,
                error          TEXT,
                created_at     TEXT,
                updated_at     TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_caption_reviews_user ON caption_reviews(user_email, created_at)"
        )


def create_caption_review(review_id: str, filename: str, user_email: str, duration=None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO caption_reviews
               (id, user_email, filename, status, progress, duration, created_at, updated_at)
               VALUES (?, ?, ?, 'queued', 5, ?, ?, ?)""",
            (review_id, user_email, filename, duration, _now(), _now()),
        )


def get_caption_review(review_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM caption_reviews WHERE id = ?", (review_id,)).fetchone()
        return dict(row) if row else None


# Whitelist das colunas atualizáveis: os nomes são interpolados no SQL (como update_job),
# então só permitimos colunas conhecidas — nunca chaves vindas de fora.
_CAPTION_REVIEW_UPDATABLE = {
    "status", "progress", "duration", "frames_count", "legenda_fala", "graficos_json",
    "report_json", "read_in_tok", "read_out_tok", "review_in_tok", "review_out_tok",
    "cost_usd", "error",
}


def update_caption_review(review_id: str, **kwargs) -> None:
    kwargs = {k: v for k, v in kwargs.items() if k in _CAPTION_REVIEW_UPDATABLE}
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [review_id]
    with _conn() as conn:
        conn.execute(f"UPDATE caption_reviews SET {fields} WHERE id = ?", values)


def list_caption_reviews(user_email: str, limit: int = 50) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, status, progress, frames_count, cost_usd, error, created_at "
            "FROM caption_reviews WHERE user_email = ? ORDER BY created_at DESC LIMIT ?",
            (user_email, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def count_active_caption_reviews() -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM caption_reviews WHERE status NOT IN ('done', 'error')"
        ).fetchone()
        return row[0]


def init_sessions() -> None:
    """Sessões server-side (lista + revogação na página de Segurança). O cookie
    assinado guarda o id (uuid4); aqui fica a linha que o login_required valida a cada
    request. ip é best-effort e influenciado pelo cliente: SÓ exibição, nunca decisão."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,      -- uuid4 (também no cookie assinado)
                user_email  TEXT NOT NULL,
                ip          TEXT,                  -- best-effort, cliente influencia: só exibição
                user_agent  TEXT,
                created_at  TEXT,
                last_active TEXT
            )
        """)
        # Cobre o filtro+ordenação da lista (WHERE user_email ORDER BY last_active).
        # O PK em id já cobre a validação por request e o delete por id.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_active ON sessions(user_email, last_active)")


def create_session(sid: str, user_email: str, ip, user_agent) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_email, ip, user_agent, created_at, last_active) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, user_email, ip, user_agent, _now(), _now()),
        )


def get_session(sid: str):
    """Linha da sessão por id (validação por request, lookup por PK). None se não existir."""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return dict(row) if row else None


def list_sessions(user_email: str, max_age_days: int | None = None) -> list:
    """Sessões do usuário, mais recentes primeiro. max_age_days esconde linhas velhas
    (antes mesmo do reap) pra a lista nunca mostrar sessão morta."""
    with _conn() as conn:
        if max_age_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            rows = conn.execute(
                "SELECT * FROM sessions WHERE user_email = ? AND last_active >= ? ORDER BY last_active DESC",
                (user_email, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE user_email = ? ORDER BY last_active DESC", (user_email,)
            ).fetchall()
        return [dict(r) for r in rows]


def touch_session(sid: str) -> None:
    """Atualiza só o last_active (chamado com throttle pelo login_required)."""
    with _conn() as conn:
        conn.execute("UPDATE sessions SET last_active = ? WHERE id = ?", (_now(), sid))


def refresh_session(sid: str, ip, user_agent) -> None:
    """Re-login no mesmo navegador: atualiza ip/ua + last_active, sem criar linha nova."""
    with _conn() as conn:
        conn.execute(
            "UPDATE sessions SET ip = ?, user_agent = ?, last_active = ? WHERE id = ?",
            (ip, user_agent, _now(), sid),
        )


def delete_session(sid: str, user_email: str) -> None:
    """Revoga UMA sessão, escopada ao dono (WHERE id AND user_email): um usuário não
    encerra a sessão de outro. Idempotente (0 linhas não é erro)."""
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ? AND user_email = ?", (sid, user_email))


def delete_sessions_except(user_email: str, keep_sid) -> None:
    """Revoga todas do usuário MENOS a atual, num único DELETE (sem TOCTOU com um
    login concorrente). keep_sid vazio = apaga todas do usuário."""
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_email = ? AND id <> ?", (user_email, keep_sid or ""))


def delete_user_sessions(user_email: str) -> None:
    """Cascade ao remover o usuário (chamado dentro da mesma transação do delete)."""
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_email = ?", (user_email,))


def reap_sessions(days: int) -> int:
    """Apaga sessões inativas há mais de `days` (cookie já teria expirado). Best-effort,
    chamado no login. Retorna quantas removeu."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE last_active < ?", (cutoff,))
        return cur.rowcount


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
        # Migração aditiva (idempotente). DEFAULT 'active' garante que usuários
        # legados continuam logando; novos cadastros manuais nascem 'pending'.
        _ensure_column(conn, "users", "status", "TEXT NOT NULL DEFAULT 'active'")
        _ensure_column(conn, "users", "is_admin", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "auth_provider", "TEXT")


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


def create_podcast_job(job_id: str, filename: str, local_path: str,
                       user_email: str | None, vinheta_id: str) -> None:
    """Entrada do pipeline de podcast (§39): o job nasce ESTACIONADO em 'uploaded',
    status que count_active_jobs e reap_stale_jobs ignoram (não compete com a fila de
    clipes nem é morto pelo watchdog; as próximas fases do pipeline é que o movem).
    Sem min/max (não corta clipes) e captions=0 (o export 16:9 sai sem legenda)."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, filename, status, progress, local_path, user_email,
                job_type, vinheta_id, captions, created_at, updated_at)
               VALUES (?, ?, 'uploaded', 0, ?, ?, 'podcast', ?, 0, ?, ?)""",
            (job_id, filename, local_path, user_email, vinheta_id, _now(), _now()),
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


def get_clip_caption_y(clip_id: str):
    """Posição vertical da legenda do clipe (fração 0..1) ou None (= padrão SB)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT caption_y FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["caption_y"] if row else None


def set_clip_caption_y(clip_id: str, caption_y) -> None:
    """Grava a posição vertical da legenda (fração 0..1, ou None p/ voltar ao padrão).
    Só atualiza a coluna (a linha já existe quando há segmentos salvos)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE clip_captions SET caption_y = ?, updated_at = ? WHERE clip_id = ?",
            (caption_y, _now(), clip_id),
        )


def get_clip_caption_style(clip_id: str):
    """Estilo da fonte do clipe (JSON string {size,tracking,color,shadow}) ou None (= padrão SB)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT caption_style FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["caption_style"] if row else None


def set_clip_caption_style(clip_id: str, style_json) -> None:
    """Grava o estilo da fonte (JSON serializado, ou None p/ voltar ao padrão SB). Só atualiza a
    coluna (a linha já existe quando há segmentos salvos)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE clip_captions SET caption_style = ?, updated_at = ? WHERE clip_id = ?",
            (style_json, _now(), clip_id),
        )


def get_clip_words(clip_id: str):
    """Palavras com timestamp do clipe (JSON string [{start,end,word}] relativo à janela) ou None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT words FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["words"] if row else None


def set_clip_words(clip_id: str, words_json) -> None:
    """Grava as palavras com timestamp do clipe (JSON serializado). Só atualiza a coluna (a linha já
    existe: set_clip_captions roda antes no run_thread2)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE clip_captions SET words = ?, updated_at = ? WHERE clip_id = ?",
            (words_json, _now(), clip_id),
        )


def get_clip_waveform(clip_id: str):
    """Picos da forma de onda do clipe (JSON string [0..1]) ou None (= ainda não gerado)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT waveform FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["waveform"] if row else None


def set_clip_waveform(clip_id: str, waveform_json) -> None:
    """Cacheia os picos da forma de onda (JSON serializado). Só atualiza a coluna (a linha já existe
    pra clipe editável: set_clip_captions rodou no processamento)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE clip_captions SET waveform = ?, updated_at = ? WHERE clip_id = ?",
            (waveform_json, _now(), clip_id),
        )


def get_clip_end_screen(clip_id: str):
    """Override da tela final do clipe: None (herda o projeto), "none" (removida neste clipe) ou
    "<id>" (forca uma tela especifica neste clipe)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT end_screen FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["end_screen"] if row else None


def set_clip_end_screen(clip_id: str, value) -> None:
    """Grava o override da tela final do clipe ("none" pra remover, "<id>" pra forcar uma, None pra
    voltar a herdar do projeto). Só atualiza a coluna (a linha já existe pra clipe editável)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE clip_captions SET end_screen = ?, updated_at = ? WHERE clip_id = ?",
            (value, _now(), clip_id),
        )


def get_clip_end_screen_dur(clip_id: str):
    """Duracao (s) da tela final a usar neste clipe, ou None (= tela inteira)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT end_screen_dur FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["end_screen_dur"] if row else None


def set_clip_end_screen_dur(clip_id: str, dur) -> None:
    """Grava a duracao da tela final do clipe (s, ou None pra usar inteira). Só atualiza a coluna."""
    with _conn() as conn:
        conn.execute(
            "UPDATE clip_captions SET end_screen_dur = ?, updated_at = ? WHERE clip_id = ?",
            (dur, _now(), clip_id),
        )


def get_clip_keyframes(clip_id: str):
    """Keyframes do crop horizontal (JSON string {mode, kf:[{t,cx}]}) ou None (= sem crop manual;
    o base.mp4 ja tem o enquadramento auto)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT keyframes FROM clip_captions WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row["keyframes"] if row else None


def set_clip_keyframes(clip_id: str, keyframes_json) -> None:
    """Grava os keyframes do crop (JSON serializado, ou None pra voltar ao auto). UPSERT pra cobrir
    o clipe sem fala (sem linha de segmentos): o auto-semeio pode rodar pra ele tambem."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO clip_captions (clip_id, segments, keyframes, updated_at)
                   VALUES (?, '[]', ?, ?)
               ON CONFLICT(clip_id) DO UPDATE SET
                   keyframes = excluded.keyframes, updated_at = excluded.updated_at""",
            (clip_id, keyframes_json, _now()),
        )


def get_clip_window(clip_id: str):
    """Janela/handles do clipe: dict {window_start, window_dur, in_point, out_point} ou None se
    não houver linha OU window_dur for NULL (clipe sem handles: o corte é a janela inteira)."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT window_start, window_dur, in_point, out_point FROM clip_captions WHERE clip_id = ?",
            (clip_id,),
        ).fetchone()
    if not row or row["window_dur"] is None:
        return None
    return {
        "window_start": row["window_start"],
        "window_dur": row["window_dur"],
        "in_point": row["in_point"],
        "out_point": row["out_point"],
    }


def set_clip_window(clip_id: str, window_start, window_dur, in_point, out_point) -> None:
    """Grava a janela + o corte inicial na produção (run_thread2). A linha de clip_captions
    costuma já existir (segmentos salvos antes); o UPSERT cobre o clipe sem fala (sem segmentos)
    pra a janela existir mesmo assim. Não toca em segments quando a linha já existe."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO clip_captions
                   (clip_id, segments, window_start, window_dur, in_point, out_point, updated_at)
               VALUES (?, '[]', ?, ?, ?, ?, ?)
               ON CONFLICT(clip_id) DO UPDATE SET
                   window_start = excluded.window_start, window_dur = excluded.window_dur,
                   in_point = excluded.in_point, out_point = excluded.out_point,
                   updated_at = excluded.updated_at""",
            (clip_id, window_start, window_dur, in_point, out_point, _now()),
        )


def set_clip_inout(clip_id: str, in_point, out_point) -> None:
    """Atualiza só os pontos de corte (in/out) editados no front. window_start/window_dur (a
    janela física do base.mp4) nunca mudam em runtime, só o recorte dentro dela."""
    with _conn() as conn:
        conn.execute(
            "UPDATE clip_captions SET in_point = ?, out_point = ?, updated_at = ? WHERE clip_id = ?",
            (in_point, out_point, _now(), clip_id),
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


def heartbeat_job(job_id: str, expected_status: str) -> None:
    """Atualiza só `updated_at` (sinal de vida), e SÓ se o job ainda estiver no status
    esperado. Usado durante etapas longas sem progresso (ex.: download do YouTube) pra o
    watchdog (reap_stale_jobs) não marcar como stale um job que está vivo. O WHERE status
    garante que um cancelamento concorrente (status='error') NÃO é ressuscitado."""
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE job_id = ? AND status = ?",
            (_now(), job_id, expected_status),
        )


def count_active_jobs() -> int:
    # 'uploaded', 'aguardando_aprovacao' e 'gancho_aprovado' = podcast estacionado sem
    # pipeline rodando (§39): não ocupam a vaga de MAX_CONCURRENT_JOBS, senão um podcast
    # parado travaria a fila de clipes com 429. Os estados de trabalho real do podcast
    # (queued..analisando_gancho, 'cortando_inicio' e 'montando' inclusive) CONTAM, de
    # propósito: transcrição, corte de início, gancho e montagem disputam a mesma VPS.
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status NOT IN "
            "('done', 'error', 'uploaded', 'aguardando_aprovacao', 'gancho_aprovado')"
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


def create_pending_user(email: str, password_hash: str) -> bool:
    """Cadastro manual: cria a conta como 'pending' (aguardando aprovação de admin).
    NÃO mexe em conta já existente — retorna False se o email já está cadastrado."""
    email = email.strip().lower()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO users (email, password_hash, must_change_password, status, is_admin, auth_provider, created_at, updated_at)
               VALUES (?, ?, 0, 'pending', 0, 'manual', ?, ?)
               ON CONFLICT(email) DO NOTHING""",
            (email, password_hash, _now(), _now()),
        )
        return cur.rowcount > 0


def upsert_user_oauth(email: str, auth_provider: str = "zoho") -> None:
    """Login via SSO (Zoho): provisiona como 'active' (auto-aprovado — domínio confiável).
    Senha fica inutilizável ('!', nunca casa em check_password_hash). Em conflito NÃO mexe
    em password_hash nem is_admin (preserva break-glass e admin) e PRESERVA status
    'rejected'/'inactive' — decisão de admin não é revertida por um login Zoho; os demais
    estados (pending/legado) viram 'active'."""
    email = email.strip().lower()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO users (email, password_hash, must_change_password, status, is_admin, auth_provider, created_at, updated_at)
               VALUES (?, '!', 0, 'active', 0, ?, ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                   status = CASE WHEN status IN ('rejected', 'inactive') THEN status ELSE 'active' END,
                   auth_provider = excluded.auth_provider,
                   updated_at = excluded.updated_at""",
            (email, auth_provider, _now(), _now()),
        )


def ensure_admin(email: str) -> None:
    """Semeia um admin no boot (config ADMIN_EMAILS): garante a linha com is_admin=1 e
    status='active'. Cria sem senha utilizável se não existir; se existir, só promove
    (preserva a senha — ele pode também ser o break-glass via manage_users.py)."""
    email = email.strip().lower()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO users (email, password_hash, must_change_password, status, is_admin, auth_provider, created_at, updated_at)
               VALUES (?, '!', 0, 'active', 1, 'bootstrap', ?, ?)
               ON CONFLICT(email) DO UPDATE SET
                   is_admin = 1,
                   status = 'active',
                   updated_at = excluded.updated_at""",
            (email, _now(), _now()),
        )


def set_user_status(email: str, status: str) -> None:
    """Estados: 'active' (loga) | 'pending' | 'rejected' | 'inactive' (não logam)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET status = ?, updated_at = ? WHERE email = ?",
            (status, _now(), email.strip().lower()),
        )


def set_user_admin(email: str, is_admin: bool) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE users SET is_admin = ?, updated_at = ? WHERE email = ?",
            (1 if is_admin else 0, _now(), email.strip().lower()),
        )


def delete_user(email: str) -> None:
    """Remove o usuário e seus dados PESSOAIS (sessões, favoritos, preferências). NÃO toca
    em jobs/projetos: a biblioteca de Projetos é compartilhada pelo time (owner_email), então
    excluir a conta não apaga o conteúdo dela. Chamado só pelo painel admin, que restringe a
    exclusão a contas 'rejected'/'inactive'."""
    email = email.strip().lower()
    with _conn() as conn:
        conn.execute("DELETE FROM sessions WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM favorites WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM preferences WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM users WHERE email = ?", (email,))


def count_active_admins() -> int:
    """Quantos admins ativos existem — usado pra impedir derrubar o último admin."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND status = 'active'"
        ).fetchone()
        return row["n"] if row else 0


def list_all_users() -> list:
    """Painel admin: todos os usuários, pendentes primeiro. Sem password_hash."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT email, status, is_admin, auth_provider, created_at, updated_at
               FROM users
               ORDER BY
                 CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                 email"""
        ).fetchall()
        return [dict(r) for r in rows]


def reap_stale_jobs(stale_minutes: int) -> int:
    """Marca como error os jobs presos em estado não terminal sem atualização há
    mais de stale_minutes (ex: callback do N8N perdido). Como MAX_CONCURRENT_JOBS=1,
    um job preso trava a fila inteira; isto libera a vaga. Retorna quantos liberou."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
    msg = f"timeout: sem atualização há mais de {stale_minutes}min (callback do N8N perdido?)"
    with _conn() as conn:
        # 'uploaded', 'aguardando_aprovacao' e 'gancho_aprovado' ficam de fora: podcast
        # estacionado (§39) não é job preso, é espera legítima sem heartbeat; a expiração
        # vem por TTL próprio (parte de retenção). Um podcast preso em transcribing/
        # analisando_gancho/cortando_inicio/montando é ceifado normalmente, igual clipe
        # (callback perdido ou ffmpeg pendurado não pode travar a fila pra sempre); essas
        # etapas longas têm heartbeat próprio (db.heartbeat_job) pra não serem ceifadas
        # no meio de um trabalho legítimo.
        cur = conn.execute(
            "UPDATE jobs SET status = 'error', error = ?, updated_at = ? "
            "WHERE status NOT IN "
            "('done', 'error', 'uploaded', 'aguardando_aprovacao', 'gancho_aprovado') "
            "AND updated_at < ?",
            (msg, _now(), cutoff),
        )
        return cur.rowcount
