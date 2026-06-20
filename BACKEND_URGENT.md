# OpusSB · Backend URGENTE (handoff pronto pra implementar)

> Para o time/agent do **backend**. O **frontend já está pronto** e só **consome** as rotas abaixo
> (não precisa mexer no front). Enquanto estas rotas não existirem, o front cai em dados de exemplo e
> mostra o badge **"Modo demonstração"**. Implementando o **Passo 0 ao 3**, o badge some e as telas
> Dashboard / Projetos / Detalhe / Favoritos passam a usar dados reais — sem nenhuma mudança no front.
>
> Stack: Flask + SQLite (`app.py`, `db.py`), sessão por cookie. Usuário logado = `session["user"]` (email).
> Gate de auth = decorator `@login_required` (já existe). Documento de contrato completo: `BACKEND_INTEGRATION.md`.

---

## 0. O que apaga o badge (escopo urgente)

Estas 4 rotas GET precisam responder `200` com JSON (mesmo que vazio):

| Rota | Tela | Fonte de dados |
|------|------|----------------|
| `GET /api/stats` | Dashboard (Início) | agrega a tabela `jobs` |
| `GET /api/projects` | Página Projetos | tabela `jobs` |
| `GET /api/projects/<job_id>` | Detalhe do projeto | `jobs` + `_clip_objects` (já existe) |
| `GET /api/clips/favorites` | Favoritos | tabela nova `favorites` |

Mais as 2 mutações triviais de favorito (`POST`/`DELETE /api/clips/<clip_id>/favorite`) que completam a página Favoritos.

**Modelo mental:** um **job = um Project**. Os **clipes** não têm tabela própria; vivem no JSON `result_urls`
do job, e o helper `_clip_objects(job_id, result_urls_json)` (em `app.py`, ~linha 141) já devolve o objeto
`Clip` no formato que o front espera. **Reaproveite-o em tudo.**

---

## 1. Schema (db.py)

### 1a. Tabela de favoritos

```python
# db.py
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
```

### 1b. Coluna de dono no `jobs` (recomendado)

Sem isso, "Projetos/Stats" mostram os jobs de **todos** os usuários. Como a tabela já existe em produção,
use migração idempotente (ALTER) em vez de só CREATE:

```python
# db.py
def _ensure_column(conn, table, col, decl):
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

# em init_db(), DENTRO do `with _conn() as conn:`, depois do CREATE TABLE jobs:
        _ensure_column(conn, "jobs", "user_email", "TEXT")
```

### 1c. `create_job` passa a gravar o dono

```python
# db.py — versão nova de create_job (acrescenta user_email; default None mantém retrocompat)
def create_job(job_id: str, filename: str, min_duration: int, max_duration: int,
               local_path: str, user_email: str | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (job_id, filename, status, progress, min_duration, max_duration,
                local_path, user_email, created_at, updated_at)
               VALUES (?, ?, 'queued', 5, ?, ?, ?, ?, ?, ?)""",
            (job_id, filename, min_duration, max_duration, local_path, user_email, _now(), _now()),
        )
```

### 1d. Funções de consulta novas (db.py)

```python
# db.py
def list_jobs(user_email=None) -> list:
    with _conn() as conn:
        if user_email:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE user_email = ? ORDER BY created_at DESC", (user_email,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

def add_favorite(user_email, clip_id):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_email, clip_id, created_at) VALUES (?,?,?)",
            (user_email, clip_id, _now()),
        )

def remove_favorite(user_email, clip_id):
    with _conn() as conn:
        conn.execute("DELETE FROM favorites WHERE user_email=? AND clip_id=?", (user_email, clip_id))

def list_favorites(user_email) -> list:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT clip_id FROM favorites WHERE user_email=? ORDER BY created_at DESC", (user_email,)
        ).fetchall()
        return [r["clip_id"] for r in rows]
```

### 1e. Boot e upload (app.py)

```python
# app.py — perto de db.init_db()/db.init_users() (~linha 34):
db.init_favorites()

# app.py — no upload(), na chamada que cria o job (~linha 203), passe o dono:
db.create_job(job_id, file.filename, min_duration, max_duration, local_path, session["user"])
```

---

## 2. Helper de Project (app.py)

```python
# app.py
def _project_from_job(job: dict) -> dict:
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
        "created_at": job.get("created_at"),          # já ISO 8601 (_now())
        "status": status,
        "thumbnail_url": None,                          # opcional (ver "lacunas")
        "clip_count": len(items) if job["status"] == "done" else 0,
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "duration": None,                               # duração do vídeo não é armazenada hoje
    }
```

---

## 3. As rotas (app.py)

```python
# app.py — usa db, json, session, _clip_objects (já existem)

@app.route("/api/projects")
@login_required
def list_projects():
    jobs = db.list_jobs(session["user"])              # ou db.list_jobs() para global
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
        jid, _, idx = cid.rpartition(":")             # clip_id = "job_id:index"
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
            c = dict(arr[idx]); c["favorite"] = True
            clips.append(c)
    return jsonify({"clips": clips})


# ATENÇÃO: clip_id contém ":" (ex.: "uuid:3"). Use <path:...>. O front manda encodeURIComponent.
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
```

---

## 4. Contratos de JSON (o que o front consome)

### Clip  (já produzido por `_clip_objects`)
```json
{
  "id": "JOBID:3",
  "url": "https://.../clip.mp4",     // URL presignada, pronta p/ <video> e download
  "title": "A regra de ouro...",      // pode ser null
  "reason": "Gancho forte...",        // pode ser null
  "score": 8.4,                        // 0..10, pode ser null
  "duration": 42                       // segundos, pode ser null
}
```
Campos opcionais que o front aproveita se existirem: `thumbnail_url`, `favorite` (bool), `project_id`.

### Project
```json
{
  "id": "JOBID", "title": "ep14.mp4", "filename": "ep14.mp4",
  "created_at": "2026-06-18T12:00:00+00:00", "status": "done",
  "thumbnail_url": null, "clip_count": 9, "avg_score": 8.1, "duration": null
}
```
`status`: `done` | `processing` | `error`.

### `GET /api/projects` → `{ "projects": Project[], "total": int }`
### `GET /api/projects/:id` → `Project` + `{ "clips": Clip[] }`
### `GET /api/stats` → `{ videos_processed, clips_generated, avg_score, processing_count, recent: Project[] }`
### `GET /api/clips/favorites` → `{ "clips": Clip[] }`

Em **não autenticado**, todas devem responder `401 {auth:false}` (o `@login_required` já faz isso) — o
front trata voltando ao login.

---

## 5. Como testar (sem subir o front)

A sessão é por cookie; teste batendo direto no Flask em `127.0.0.1:5001` (loopback é confiável).

```bash
# 1) login (guarda o cookie de sessão)
curl -s -c /tmp/sb.txt -X POST http://127.0.0.1:5001/api/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"voce@seubone.com","password":"SUA_SENHA"}'

# 2) chamar as rotas com o cookie
curl -s -b /tmp/sb.txt http://127.0.0.1:5001/api/stats            | python3 -m json.tool
curl -s -b /tmp/sb.txt http://127.0.0.1:5001/api/projects         | python3 -m json.tool
curl -s -b /tmp/sb.txt http://127.0.0.1:5001/api/projects/<JOBID> | python3 -m json.tool
curl -s -b /tmp/sb.txt http://127.0.0.1:5001/api/clips/favorites  | python3 -m json.tool

# 3) favoritar / desfavoritar (clip_id tem ":" -> URL-encode como %3A)
curl -s -b /tmp/sb.txt -X POST   "http://127.0.0.1:5001/api/clips/JOBID%3A3/favorite"
curl -s -b /tmp/sb.txt -X DELETE "http://127.0.0.1:5001/api/clips/JOBID%3A3/favorite"
```

Depois, reinicie o serviço (ex.: `systemctl restart opussb`) e confirme que o badge "Modo demonstração"
sumiu no navegador (logado).

---

## 6. Atenção (gotchas)
- **`clip_id` tem `:`** → rota de favorito precisa de `<path:clip_id>`.
- **Escopo por usuário:** decida entre `db.list_jobs(session["user"])` (por usuário, recomendado, exige a
  coluna `user_email`) ou `db.list_jobs()` (global). Sem a coluna, todos veem os projetos de todos.
- **`avg_score`/`title`/`reason` dependem de `result_urls` guardar objetos** `{key,title,reason,score,duration}`.
  O `_clip_objects` aceita o formato antigo (só a chave) também, mas aí os campos saem `null` (cards
  funcionam sem badge de nota). Garanta que o processor/N8N grave a metadata no `result_urls`.
- Não há HTTPS ainda; nada novo depende disso.

---

## 7. Segunda onda (depois — não apaga o badge, completa as ações)
Cada uma mostra um toast "aguarda backend" no front até existir.
- `DELETE /api/projects/<id>` → `200 {ok:true}` (apaga job + arquivos/S3).
- `GET /api/projects/<id>/download_all` → zip dos clipes **ou** `{ "url": "<zip presignado>" }`.
- `POST /api/jobs/<id>/cancel` → `200 {ok:true}` (encerra job ativo; libera a vaga do `MAX_CONCURRENT_JOBS=1`).
- `POST /api/projects/<id>/reprocess` body `{min_duration,max_duration,captions}` → `202 {job_id}`.
- `GET|PUT /api/preferences` (hoje as prefs vivem no localStorage do front; PUT recebe
  `{defaultMin,defaultMax,aspect,language,captions,autoplayPreview}`).
- `POST /api/upload`: ler os campos opcionais `captions` / `language` / `aspect_ratio` (já chegam em
  `request.form`) e repassar ao pipeline, se quiser honrá-los.

### Lacunas de dados que melhoram a UI (opcional)
- `thumbnail_url` por clipe/projeto (poster do card; hoje cai no player/placeholder).
- `duration` do vídeo original no `jobs` (hoje só guardamos min/max do clipe).
