# OpusSB — Design Spec

**Data:** 2026-06-12  
**Status:** Aprovado  
**Objetivo:** Pipeline de vídeo → clipes virais 9:16, rodando em infraestrutura própria (clone funcional do OpusClip)

---

## 0. ATUALIZAÇÃO DE ESTADO (2026-06-18)

> O corpo abaixo (seções 1 em diante) é o design original de 12/06, mantido como
> registro histórico. Esta seção 0 reflete o que está EM PRODUÇÃO hoje. Em caso de
> divergência, vale a seção 0.

### Infra real (corrige a seção 2)
- VPS Hetzner **CPX21**: 3 vCPU, 4GB RAM, swap 4GB. Volume de 80GB montado em **`/mnt/opussb-jobs`**.
- `JOB_DIR=/mnt/opussb-jobs` (não `/opt/opussb/jobs`).
- S3 endpoint real: `https://fsn1.your-objectstorage.com`, region `eu-central-1` explícita no boto3.
- `CLAUDE_MODEL` padrão = `claude-haiku-4-5-20251001` (Haiku, por custo).
- Nginx `client_max_body_size 15G` (não 4G). Transcodificação na entrada foi DESCARTADA: a redução de bitrate (10-15 Mbps, 30fps) é padrão de export na origem, fora do OpusSB.

### Autenticação (NOVO, não existia no design original)
- Login de sessão no próprio Flask. Tabela `users` (email PK, `password_hash` scrypt, `must_change_password`).
- Rotas: `POST /api/login`, `POST /api/logout`, `GET /api/me`, `POST /api/change_password`.
- Primeiro acesso força troca de senha antes de liberar upload (checado no banco, não na sessão).
- Gestão por CLI: `./venv/bin/python manage_users.py add|reset|list|delete`.
- Sessão por cookie assinado (`FLASK_SECRET_KEY` no `.env`), `httponly`, `samesite=Lax`, `SESSION_COOKIE_SECURE=False` até ter HTTPS.
- Frontend tem 3 telas: login, troca de senha (1º acesso) e o app, roteadas por `/api/me`.

### Segurança de acesso (NOVO)
- Gunicorn faz bind em **`127.0.0.1:5001` + `172.17.0.1:5001`**, NUNCA `0.0.0.0` (não expor o Flask na internet). Ver `opussb.service`.
- N8N (container `172.17.0.2`) chama o Flask direto em **`172.17.0.1:5001`**, sem passar pelo Nginx. A menção a `127.0.0.1:5001` no doc do N8N está errada (de dentro do container não funciona).
- Nginx devolve **404** para `/api/callback` e `/api/internal` na porta 80 (essas rotas só pelo `:5001`).
- Gate das rotas internas (`_is_trusted_caller`): loopback + subnet `172.17.0.0/16` (não IP fixo, pra não quebrar se o Docker recriar o container do N8N com outro IP).
- Permissões: `.env` 600, `opussb.db` 660.

### Robustez (NOVO)
- **Watchdog**: `db.reap_stale_jobs()` chamado no `/api/upload` marca como `error` job não terminal sem update há mais de `JOB_STALE_MINUTES` (30). Como `MAX_CONCURRENT_JOBS=1`, um job preso travaria a fila inteira; isto libera a vaga.
- Cleanup de disco: `shutil.rmtree` movido do `finally` para o bloco de SUCESSO (regra: nunca apagar source antes dos clipes no S3). Caminho de erro limpa via `_fail_job`.
- Campo `error` é limpo quando o job conclui (`done`).

### Backup
- Repo privado no GitHub: `kleniobraz/opussb`, via deploy key SSH. `git push` já funciona da VPS. `.env`, `opussb.db` e htpasswd nunca commitados.

### Backlog real (substitui a seção 11 como "próximos passos")
Concluído: cleanup de disco, segurança de acesso, login, watchdog, backup no GitHub, **race condition no `MAX_CONCURRENT_JOBS`** (admissão sob lock), **`MAX_CONTENT_LENGTH`** (16GiB, secundário ao Nginx).

Aberto, em ordem. O backend em código Flask está fechado; o que resta não é código nesta máquina:
1. **TLS/HTTPS** (precisa de domínio apontado pro IP; sem isso a senha de login trafega em texto puro na porta 80). Ao subir, virar `SESSION_COOKIE_SECURE=True`.
2. **Frontend de produto** (evolução do atual): player de vídeo embutido nos clipes em vez de só link, UX, responsivo mobile, identidade visual.
3. **N8N: subir o limite de polling** (config do workflow na UI). O nó IF compara `counter < 60`; com Wait de 10s isso mata o job em ~10min, mas a transcrição pode levar até 15min, matando áudio longo legítimo. Subir o Value 2 de `60` para `120` (20min, abaixo do watchdog de 30min). OBS: o PATCH de erro no estouro JÁ existe (Step 5 do N8N_WORKFLOW.md), não precisa adicionar. Conferir também que as URLs dos HTTP Request usam `172.17.0.1:5001` (o doc cita `127.0.0.1`, que não funciona de dentro do container). NÃO renomear nós (o POST Callback referencia o nó que extrai os clipes pelo nome exato).
4. Cadastrar os demais emails da equipe (`manage_users.py add`).

---

## 1. Contexto e Escopo

### Usuários
Equipe de audiovisual/marketing de 3 pessoas. Sem uploads simultâneos reais. `MAX_CONCURRENT_JOBS = 1` — se um job estiver em andamento, novos uploads recebem HTTP 429.

### Input esperado
- Vídeos longos (ex: podcast de 60 min), qualquer proporção
- Usuário define `min_duration` e `max_duration` dos clipes no momento do upload

### Output esperado
- 5–10 clipes em 9:16 (1080×1920) com os melhores momentos do vídeo
- Clipes prontos para postar no Instagram/TikTok/Reels
- Entregues via links S3 exibidos no frontend

### Gargalo conhecido nº 1
YOLOv8 em CPU na CPX11 (~35–65s por clipe de 60s). Aceitável para MVP. Caminho de escala: mover passos de corte + reframe para um segundo worker RunPod com GPU.

---

## 2. Infraestrutura Existente

| Componente | Detalhes |
|---|---|
| VPS | Hetzner CPX11 — 2 vCPU, 2GB RAM, ~40GB disco, Ubuntu 22.04, IP 87.99.159.235 |
| N8N | Rodando via Docker na VPS, porta 5678 |
| Object Storage | Hetzner bucket `opussb-storage`, endpoint `https://opussb-storage.fsn1.your-objectstorage.com` |
| RunPod worker | Faster Whisper 1.0.10, Endpoint ID `fczh1t4sx02j1s`, GPU 24GB Pro |

---

## 3. Arquitetura — Abordagem B (Python Job Server + N8N para APIs externas)

### Diagrama de fluxo

```
[Browser]
    │  POST /api/upload (video + min_duration + max_duration)
    ▼
[Nginx]  ──proxy /api/──►  [Flask :5001]
    │                           │
    │  GET /api/status/{job_id} │  cria job no SQLite
    ◄──────────────────────────  │  dispara background thread
                                 │
                    ┌────────────┘
                    ▼
             [processor.py] — background thread
                    │
                    ├─1─ FFmpeg: extrai audio.mp3 do vídeo
                    ├─2─ S3: upload audio.mp3
                    ├─3─ HTTP POST → N8N webhook
                    │         (job_id, audio_s3_url, min_duration, max_duration)
                    │
                    │    [N8N Workflow]
                    │         │
                    │         ├─4─ RunPod: submit transcription job
                    │         ├─5─ Poll RunPod até COMPLETED (max 40 tentativas / 20 min)
                    │         ├─6─ Claude API: score trechos → timestamps JSON
                    │         └─7─ POST → Flask /api/callback
                    │                   (job_id, transcript, clips_json)
                    │
                    ◄── callback recebido
                    │
                    ├─8─ FFmpeg: corta N clipes pelos timestamps
                    ├─9─ YOLOv8 + OpenCV + FFmpeg: reframe cada clipe → 9:16
                    └─10─ S3: upload clipes finais → status="done"

[Browser polling GET /api/status/{job_id}]
    ◄── { stage: "transcribing", progress: 40 }
    ◄── { stage: "done", clips: ["https://...clip_01.mp4", ...] }
```

### Componentes

| Componente | Onde roda | Responsabilidade |
|---|---|---|
| Nginx | VPS | Serve frontend estático, proxy para Flask, limite 4GB upload |
| Flask app | VPS :5001 | Upload, status, callback, job state, limite concorrência |
| SQLite | VPS | Estado persistente dos jobs |
| processor.py | VPS (thread) | FFmpeg áudio, trigger N8N, FFmpeg corte, reframe |
| reframer.py | VPS | YOLOv8 + OpenCV + FFmpeg → 9:16 |
| N8N | VPS :5678 | RunPod polling com retry, Claude API |
| RunPod worker | Cloud GPU | Faster Whisper — transcrição com timestamps |
| Hetzner S3 | Cloud | Áudio temporário + clipes finais permanentes |

---

## 4. Modelo de Dados (SQLite)

```sql
CREATE TABLE jobs (
    job_id        TEXT PRIMARY KEY,   -- uuid4
    filename      TEXT NOT NULL,
    status        TEXT NOT NULL,
    progress      INTEGER DEFAULT 0,
    min_duration  INTEGER,            -- segundos
    max_duration  INTEGER,            -- segundos
    local_path    TEXT,               -- caminho do vídeo original na VPS
    audio_s3_url  TEXT,
    runpod_job_id TEXT,               -- necessário para polling no N8N
    transcript    TEXT,               -- JSON completo da transcrição (≤300KB)
    clips_json    TEXT,               -- JSON dos timestamps escolhidos pela Claude
    result_urls   TEXT,               -- JSON array de URLs dos clipes finais
    error         TEXT,
    created_at    TEXT,
    updated_at    TEXT
);
```

### Máquina de estados

| status | progress | significado |
|---|---|---|
| `queued` | 5 | job criado, vídeo recebido |
| `extracting_audio` | 15 | FFmpeg extraindo áudio |
| `uploading_audio` | 25 | subindo áudio pro S3 |
| `transcribing` | 40 | RunPod processando |
| `scoring` | 60 | Claude analisando trechos |
| `cutting` | 70 | FFmpeg cortando clipes |
| `reframing` | 85 | YOLOv8 reframando clipes |
| `uploading_clips` | 95 | subindo clipes finais |
| `done` | 100 | entregue |
| `error` | — | falhou; campo `error` preenchido |

---

## 5. N8N Workflow

### Estrutura dos nós

```
[Webhook Trigger]
      │  recebe: { job_id, audio_s3_url, min_duration, max_duration }
      ▼
[HTTP: RunPod submit]
      │  POST /v2/fczh1t4sx02j1s/run
      │  body: { input: { audio_url: audio_s3_url } }
      ▼
[HTTP: Flask PATCH /api/internal/jobs/{job_id}]
      │  body: { runpod_job_id: "...", status: "transcribing" }
      ▼
[Wait: 30s] ◄──────────────────────────────┐
      ▼                                     │
[Code: contador de tentativas]              │
      │  if attempts > 40: throw Error      │
      ▼                                     │
[HTTP: RunPod status GET]                   │
      ▼                                     │
[IF: status == "COMPLETED"]                 │
      │ false ──────────────────────────────┘
      │ true
      ▼
[HTTP: Flask PATCH] → status: "scoring"
      ▼
[HTTP: Claude API]
      ▼
[Code: extrai JSON do response]
      ▼
[HTTP: Flask POST /api/callback]
      ▼
[Respond to Webhook: 200 OK]
```

### Código do nó contador (previne loop infinito)

```javascript
const attempts = $node["Wait"].context.attempts || 0;
if (attempts > 40) throw new Error("RunPod timeout após 20 minutos");
$node["Wait"].context.attempts = attempts + 1;
```

### Prompt Claude API

```
Você receberá a transcrição de um vídeo com timestamps por segmento.
Identifique os 5 a 10 melhores trechos para clipes virais.
Cada trecho deve ter duração entre {{min_duration}}s e {{max_duration}}s.

Critérios: insight forte, momento emocional, pergunta-resposta completa,
história curta, afirmação surpreendente. O trecho DEVE começar e terminar
em frase completa. Nunca corte no meio de uma ideia.

Retorne APENAS um JSON válido no formato:
[
  { "start": 123.4, "end": 187.2, "score": 9, "reason": "..." },
  ...
]

Transcrição:
{{transcript_segments}}
```

### Endpoints internos Flask usados pelo N8N

| Método | Rota | Corpo | Uso |
|---|---|---|---|
| `PATCH` | `/api/internal/jobs/<job_id>` | campos a atualizar | atualiza `runpod_job_id`, `status` |
| `POST` | `/api/callback` | `{job_id, transcript, clips_json}` | entrega resultado e spawna thread de pós-processamento |

> **Segurança:** endpoints `/api/internal/*` aceitam chamadas apenas de `127.0.0.1`. Nginx não expõe essas rotas externamente.

### Mecanismo de threading (dois threads, sem bloqueio)

O pipeline é dividido em dois threads independentes para evitar threads dormentes esperando callback:

- **Thread 1** (disparado no upload): passos 1–3 — FFmpeg áudio → S3 → trigger N8N → termina
- **Thread 2** (disparado pelo `/api/callback`): passos 8–10 — FFmpeg corte → reframe → S3 upload → status=done

O endpoint `/api/callback` salva `transcript` e `clips_json` no SQLite e imediatamente spawna o Thread 2 via `threading.Thread(target=run_postprocessing, args=(job_id,)).start()`. Sem polling, sem eventos de threading.

---

## 6. Reframing 9:16 (reframer.py)

### Algoritmo

**Input:** clipe FFmpeg em qualquer proporção (tipicamente 1920×1080)  
**Output:** 1080×1920 (9:16 portrait)  
**Janela de corte:** 608×1080 extraída do frame original, reescalada para 1080×1920

```
Para cada clipe:
  1. Inicializa crop_x = 656 (centro) — fallback ativo desde o frame 0
  2. Amostra 1 frame a cada 15 frames (~2fps de detecção)
  3. Cada frame amostrado: reduz para 640×360 → YOLOv8n detecta 'person' →
     reescala coordenadas → calcula crop_x centrado no corpo
  4. Interpola crop_x para todos os frames
  5. Aplica média móvel janela=45 (suavização temporal ~1.5s)
  6. Fallback: se nenhuma detecção, mantém último crop_x válido
  7. OpenCV VideoWriter (fourcc='mp4v') escreve frames cropados e redimensionados
  8. Validação:
       assert os.path.exists(clip_mute_path)
       assert os.path.getsize(clip_mute_path) > 1000
  9. FFmpeg mux: adiciona áudio original de volta (stream copy, sem re-encode)
```

### Modelo

- `yolov8n.pt` (nano, 6MB) — detecta classe `person`
- Escolha justificada: mais robusto que face detection para podcast
  (resiste a gestos, perfil, apresentador em pé)

### Performance estimada (CPU, CPX11)

| Etapa | Tempo por clipe 60s |
|---|---|
| Detecção YOLOv8n (~120 frames) | 10–20s |
| Escrita OpenCV (1800 frames) | 20–40s |
| FFmpeg mux áudio | 2–5s |
| **Total por clipe** | **~35–65s** |
| **10 clipes** | **~6–11 min** |

---

## 7. Estrutura de Arquivos

### VPS

```
/opt/opussb/
├── config.py           # ÚNICA fonte de configuração — carrega .env
├── .env                # nunca commitado; fica apenas na VPS
├── app.py              # Flask server
├── processor.py        # Background job (passos 1–3, 8–10)
├── reframer.py         # YOLOv8 + OpenCV + FFmpeg → 9:16
├── s3_client.py        # Wrapper boto3 para Hetzner S3
├── db.py               # Helpers SQLite
├── requirements.txt
├── static/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── jobs/               # Diretório de trabalho (limpo ao fim de cada job)
│   └── {job_id}/
│       ├── original.mp4
│       ├── audio.mp3
│       └── clips/
│           ├── clip_01.mp4
│           ├── clip_01_mute_9x16.mp4
│           └── clip_01_final.mp4
└── opussb.db
```

> **Regra:** nenhum arquivo fora de `config.py` pode conter string de configuração hardcoded (API keys, credenciais, URLs de serviço).

### Hetzner S3 (`opussb-storage`)

```
jobs/
└── {job_id}/
    ├── audio/
    │   └── audio.mp3        # URL pública para RunPod
    └── clips/
        ├── clip_01_9x16.mp4 # entregável permanente
        ├── clip_02_9x16.mp4
        └── ...
```

Ciclo de vida:

| Arquivo | VPS | S3 | Deletado quando |
|---|---|---|---|
| Vídeo original | ✅ | ❌ | fim do job |
| `audio.mp3` | ✅ temporário | ✅ | VPS: fim do job / S3: opcional após 24h |
| Clipes intermediários | ✅ temporário | ❌ | fim do job |
| Clipes finais 9:16 | ✅ temporário | ✅ permanente | VPS: fim do job / S3: nunca — acessados via presigned URLs (7 dias) |

### Nginx (ajustes críticos)

```nginx
client_max_body_size 4G;
proxy_read_timeout   300s;
```

---

## 8. Controle de Concorrência e Limites

```python
# config.py
MAX_CONCURRENT_JOBS = 1
```

No endpoint `POST /api/upload`, antes de criar o job:

```python
active = db.count_jobs_by_status(exclude=["done", "error"])
if active >= config.MAX_CONCURRENT_JOBS:
    return jsonify({
        "error": "Um vídeo já está em processamento! Vai tomar uma água, respirar e aguardar a conclusão antes de enviar novo vídeo :)"
    }), 429
```

---

## 9. Error Handling

**Princípio:** falhar no nível do clipe, não do job. Se o reframe do clipe 3 falhar, entregar clipes 1, 2, 4, 5 e registrar o erro parcial.

| Etapa | Falha | Comportamento |
|---|---|---|
| Upload | Formato inválido / >4GB | HTTP 400 imediato, job não criado |
| FFmpeg áudio | Vídeo corrompido | `status=error`, cleanup, mensagem clara |
| S3 upload áudio | Timeout/rede | Retry 3× com backoff → `status=error` |
| N8N webhook | N8N offline | Retry 3× → `status=error` |
| RunPod polling | >40 tentativas | N8N lança exceção → Flask recebe callback de erro |
| Claude API | Rate limit / timeout | Retry 2× → `status=error` |
| FFmpeg corte | Timestamps inválidos | Pula clipe, continua com demais |
| YOLOv8 / OpenCV | Detecção falha | Fallback center crop — **nunca falha o job** |
| S3 upload clipes | Timeout/rede | Retry 3× → marca clipe individual como falho |

**Cleanup garantido** (sucesso ou falha):

```python
finally:
    shutil.rmtree(f"/opt/opussb/jobs/{job_id}", ignore_errors=True)
```

**Formato de erro para o frontend:**

```json
{
  "status": "error",
  "error": "RunPod timeout após 20 minutos. Tente reenviar o vídeo.",
  "partial_clips": ["https://.../clip_01_9x16.mp4"]
}
```

---

## 10. Frontend (MVP)

- HTML estático servido pelo Nginx
- Formulário: campo de arquivo + `min_duration` + `max_duration` em segundos
- Ao submeter: POST `/api/upload` → recebe `job_id`
- Polling a cada 30s em `GET /api/status/{job_id}`
- Exibe barra de progresso (campo `progress`) + label do estágio atual (`status`)
- Ao `status=done`: exibe links de download dos clipes (presigned URLs S3 com 7 dias de expiração, geradas no momento da resposta GET /api/status)
- Ao `status=error`: exibe mensagem de erro com botão "Tentar novamente"

---

## 11. Ordem de Implementação

1. Nginx + frontend HTML estático (upload form + polling UI)
2. `config.py` + `.env` + `db.py` + `s3_client.py`
3. Flask app (`app.py`) — endpoints upload, status, callback, internal
4. `processor.py` — FFmpeg áudio + S3 + trigger N8N
5. N8N workflow — RunPod polling + Claude scoring + callback
6. FFmpeg clip cutting (dentro do `processor.py`)
7. `reframer.py` — YOLOv8 + OpenCV + FFmpeg → 9:16
8. Upload clipes finais S3 + status done
9. Teste end-to-end com vídeo real

---

## 12. Variáveis de Ambiente (.env)

```env
# S3
S3_ENDPOINT=https://opussb-storage.fsn1.your-objectstorage.com
S3_BUCKET=opussb-storage
S3_ACCESS_KEY=<gerado na Hetzner>
S3_SECRET_KEY=<gerado na Hetzner>

# RunPod
RUNPOD_API_KEY=<gerado no RunPod>
RUNPOD_ENDPOINT_ID=fczh1t4sx02j1s

# Claude
ANTHROPIC_API_KEY=<gerado na Anthropic>
CLAUDE_MODEL=claude-sonnet-4-6

# N8N
N8N_WEBHOOK_URL=http://localhost:5678/webhook/opussb

# App
FLASK_PORT=5001
MAX_CONCURRENT_JOBS=1
JOB_DIR=/opt/opussb/jobs
```
