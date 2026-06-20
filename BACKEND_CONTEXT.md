# OpusSB · Contexto de Backend (handoff pra um novo chat)

Leia isto antes de tocar em qualquer coisa. Resume o que o projeto é, como roda, o
que já foi feito e as regras que não podem ser quebradas. Há docs complementares no
repo: `BACKEND_INTEGRATION.md` (contrato front↔back), `FRONTEND_TODO.md` (pendências
de UI), `BACKEND_URGENT.md` (spec antiga de handoff do front).

---

## 0. O que é

OpusSB é um clone self-hosted do OpusClip, **uso interno do Grupo Quatro5 / SB**.
Recebe um vídeo longo, transcreve, o Claude pontua os melhores momentos, e o sistema
corta + reenquadra pra 9:16 + legenda queimada no estilo da casa. Não é aberto ao
público (risco financeiro): tudo atrás de login.

Trabalha-se **direto na VPS de produção** em `/opt/opussb`.

---

## 1. REGRAS INEGOCIÁVEIS (constraints do dono, Klenio)

1. **Nunca use travessão** (nem `—` nem `-` como travessão) em texto gerado.
2. **Nunca edite um `.py` do servidor com nano/vim.** Sempre reescreva o arquivo
   inteiro (entregue completo, nunca cortado). Editar com ferramenta de substituição
   exata é ok; o que não pode é deixar arquivo pela metade.
3. **Antes de mexer no N8N, confira o nome EXATO dos nós.** O nó `Extract Clips -
   Code` é referenciado pelo POST Callback pelo nome literal. Faça **backup do
   workflow antes de cada PUT** na API do N8N.
4. **Nunca apague o vídeo de origem antes de confirmar que os clipes subiram pro
   S3.** Cleanup (`rmtree`) só no fim do bloco de sucesso. Respeite isso à risca.
5. **Respeite a máquina de estados** (ver seção 3). Não invente estados nem pule
   etapas.
6. **Não troque o modelo de scoring** (Haiku) pra Sonnet/Opus sem justificativa de
   qualidade medida.
7. **Toda mudança que toca caixa ou custo recorrente, sinalize o impacto** antes.
8. **Decisão de arquitetura ou custo: responda/pergunte ANTES de executar.**
9. **Push na `master` exige autorização explícita do Klenio** (proteção de branch
   default do Claude Code). Commit local pode; push na master, pergunte.
10. **Testes nunca tocam dados reais.** Fabrique jobs/usuários com prefixo `TEST` e
    limpe tudo no fim (S3 + banco + usuário).

---

## 2. Infraestrutura

- **VPS**: Hetzner CPX21, 3 vCPU, 4GB RAM, swap 4GB. IP público `87.99.159.235`.
  Volume separado de 79GB em `/mnt/opussb-jobs` (é o `JOB_DIR`, trabalho em disco).
- **App**: Flask + Gunicorn (`/opt/opussb/venv/bin/gunicorn`), **1 worker / 4 threads
  (gthread)**, `--timeout 3600`. systemd unit `opussb.service`
  (`WorkingDirectory=/opt/opussb`, `EnvironmentFile=/opt/opussb/.env`,
  `Restart=on-failure`). **Bind duplo**: `127.0.0.1:5001` e `172.17.0.1:5001`
  (gateway da bridge Docker). **Nunca** escuta em `0.0.0.0`.
- **Nginx** (porta 80): serve o SPA estático de `static/` e faz proxy do `/api/`.
  `client_max_body_size 15G`. Devolve **404 para `/api/callback` e `/api/internal`**
  (defesa: rotas internas não acessíveis de fora).
- **N8N** (Docker, porta 5678): orquestra RunPod + Claude. Container alcança o Flask
  pelo gateway `172.17.0.1:5001` **direto** (não via Nginx). Workflow id
  `UGtxQOBzHsfI9Bpg`, editável pela API REST (`X-N8N-API-KEY`). Key em
  `/root/.opussb_n8n_api_key` (600, root only, fora do repo).
- **RunPod**: endpoint de Faster Whisper. Devolve o transcript em **SRT** (com
  timestamps por segmento, no eixo absoluto do vídeo).
- **Claude**: scoring viral, roda **dentro do workflow do N8N** (não no Flask).
  Modelo Haiku 4.5 (`CLAUDE_MODEL`, barato). Produz `clips_json`
  (`[{start,end,title,score,reason}]`).
- **S3**: Hetzner Object Storage via boto3. `S3_ENDPOINT=https://fsn1.your-objectstorage.com`,
  `S3_BUCKET=opussb-storage`, região `eu-central-1` (explícita no client).
- **GitHub**: repo privado `kleniobraz/opussb`, deploy key SSH. `git push` funciona
  da VPS (mas ver regra 9).

Config em `config.py` (lê `.env` via dotenv): `S3_*`, `RUNPOD_API_KEY`,
`RUNPOD_ENDPOINT_ID`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, `N8N_WEBHOOK_URL`,
`FLASK_SECRET_KEY`, `SESSION_DAYS=7`, `MAX_CONCURRENT_JOBS=1`,
`JOB_DIR=/mnt/opussb-jobs`, `JOB_STALE_MINUTES=30`. Vars sem default levantam KeyError
de propósito (não sobe com config faltando).

---

## 3. Pipeline e máquina de estados

`POST /api/upload` cria o job (`queued`) e dispara a **thread1**:
- `extracting_audio` → `uploading_audio` (sobe `jobs/<id>/audio/audio.mp3` no S3) →
  `transcribing` → dispara o webhook do N8N (com URL presignada do áudio + min/max).

O **N8N** roda RunPod (Whisper → SRT) + Claude (scoring → clips_json) e chama
`POST /api/callback` com `{job_id, transcript (SRT), clips_json}`.

O callback grava transcript+clips_json e dispara a **thread2**:
- `cutting` (corta cada clipe com ffmpeg) → `reframing` (reenquadra 9:16) →
  `uploading_clips` (sobe os clipes + grava segmentos de legenda) → `done`.

Estados: `queued, extracting_audio, uploading_audio, transcribing, cutting,
reframing, uploading_clips, done, error`.

- **MAX_CONCURRENT_JOBS=1**: um job por vez. Um job preso trava a fila.
- **Watchdog**: `reap_stale_jobs` marca como `error` jobs sem update há >30min
  (callback do N8N perdido), liberando a vaga. Roda na admissão de upload.
- **Cancelamento**: `POST /api/jobs/<id>/cancel` marca `error`; as threads checam
  cooperativamente (`_Cancelled`) entre etapas e param sem sobrescrever.

---

## 4. Arquivos do backend

- **`app.py`** — rotas Flask + boot (`db.init_*`). Auth de sessão (`login_required`),
  `/api/login|logout|me|change_password`, `/api/status/<id>`, `/api/projects[/<id>]`,
  `/api/stats`, favoritos, `/api/preferences`, `/api/upload`. Rotas internas
  (`/api/callback`, `/api/internal/jobs/<id>`) sem sessão, atrás de
  `_is_trusted_caller` (loopback + subnet `172.17.0.0/16`). Helpers `_clip_objects`
  (monta os Clips; `url` aponta pra rota de vídeo) e `_project_from_job`.
  **Editor/legenda/música**: ver seção 6.
- **`db.py`** — SQLite (`opussb.db`, fora do repo). Migrações idempotentes
  (`_ensure_column`). Tabelas na seção 5. `delete_job` faz cascade
  (favoritos + clip_captions + clip_music).
- **`processor.py`** — `run_thread1`, `run_thread2`, `_cut_clips`,
  `_center_crop_fallback`, `_Cancelled`/`_check_cancel`. **Cleanup só no fim do
  sucesso** (regra 4).
- **`reframer.py`** — reframe 9:16 (1080x1920) com YOLOv8n seguindo a pessoa; se o
  vídeo já é vertical, copia direto (sem recorte). `_filter_escape`,
  `_encode_with_subtitles`, `_mux_audio`. **cv2/ultralytics são pesados**: o app só
  importa o reframer dentro da thread2 (lazy), nunca no processo web só pra servir.
- **`captions.py`** — puro (só `re`, sem ffmpeg). `parse_srt`, `segments_for_clip`
  (recorta o SRT por clipe → dado editável), `build_ass_from_segments` (rende o .ass
  no estilo SB). Constantes do estilo no topo (ver seção 6).
- **`s3_client.py`** — `upload_file`, `download_file`, `exists`, `get_presigned_url`,
  `delete_object`, `delete_prefix`.
- **`manage_users.py`** — CLI: `add/reset/list/delete` usuários (scrypt,
  `must_change=True` por padrão). Ex.: `./venv/bin/python manage_users.py add
  fulano@seubone.com --password '!SenhaPadrãoMudar'`.
- **`static/`** — SPA vanilla (sem build), servida pelo Nginx. **Domínio do chat do
  front**; o backend só expõe o contrato (`BACKEND_INTEGRATION.md`).

---

## 5. Modelo de dados (SQLite)

- **`jobs`**: `job_id, filename, status, progress, min/max_duration, local_path,
  audio_s3_url, runpod_job_id, transcript (SRT), clips_json, result_urls (JSON),
  error, created_at, updated_at, user_email (dono; NULL=legado visível a todos),
  captions (0/1, default 1)`.
- **`users`**: `email, password_hash (scrypt), must_change_password, timestamps`.
- **`favorites`**: `(user_email, clip_id)` — `clip_id = "<job_id>:<index>"`.
- **`preferences`**: `user_email, data (JSON)`.
- **`clip_captions`**: `clip_id, segments (JSON [{start,end,text}] relativo ao
  clipe), updated_at`. Legenda editável.
- **`clip_music`**: `clip_id, music_key (S3), volume (0..1), updated_at`. Música de
  fundo por clipe (opcional).

`result_urls` guarda objetos `{key, title, score, reason, duration}` por clipe (jobs
antigos podem ter só a string da chave; o código trata os dois).

---

## 6. Feature de legenda + editor (o grosso do trabalho recente)

**Estilo SB** (extraído de um `.prtextstyle` do Premiere + painel): fonte **Inter
Medium** (substitui SF Pro Display, que é proprietária Apple e não pode ir pro
Linux; Inter instalada em `/usr/local/share/fonts/inter`), **tamanho 70**, **uma
linha só** sempre, branco, **glow** escuro (halo, simula a sombra blur 155 do
Premiere), tracking apertado, posição alta. Tudo em constantes no topo de
`captions.py` (`FONT_SIZE`, `TRACKING_PX`, `MARGIN_V`, `GLOW_*`, `MAX_LINES=1`).
**Armadilha**: o **libass ignora o campo `Spacing` do estilo .ass**; o aperto entre
letras é aplicado por evento via tag inline `\fsp`. `\fsp` em px na fonte 70: ~-3 a
-8 é a faixa usável; -35 vira borrão ilegível.

**Arquitetura "legenda editável + queima sob demanda"** (validada em produção):
- A thread2 **não queima** no processamento. Sobe o clipe **sem legenda**
  (`jobs/<id>/clips/clip_NN_base.mp4`) e grava os segmentos em `clip_captions`.
- A queima acontece **sob demanda** em `GET /api/clips/<clip_id>/video`: faz **302**
  pra URL presignada; na 1a abertura baixa a base, gera o .ass, queima, sobe o cache
  `clip_NN_cap.mp4` e redireciona; depois é cache. Tem **lock por clipe** (não
  queima em duplicata), **timeout de 180s no ffmpeg** (mata o processo travado), e
  **fallback**: se a queima falha, serve a base (clipe sempre toca) e **nunca deixa
  `_cap.mp4` corrompido** (só sobe após render local ok).
- **Correção**: `GET/PUT /api/clips/<id>/captions` (texto + tempo). O PUT valida
  (rejeita `end<=start`), grava e **invalida o cache** (apaga o `_cap.mp4`).
- **Música**: `POST/PUT/DELETE /api/clips/<id>/music` (upload/volume/remover). Mixada
  de fundo, em loop, com volume, no `_burn_clip` (no `app.py`). Também invalida o
  cache.
- **Clipes antigos** (pré-feature, `clip_NN_9x16.mp4` já queimado, sem
  `clip_captions`): servidos direto, **não editáveis**.
- A queima roda no `app.py` com ffmpeg via subprocess (não usa o reframer, pra não
  carregar YOLO no worker web). Helpers: `_build_burn_cmd` (monta o comando, função
  pura testável), `_burn_clip`, `_clip_burn_lock`, `_invalidate_cap`.
- **Mapeamento de áudio (cuidado)**: quando há legenda mas NÃO há música, o filtro
  `[0:v]subtitles[v]` faz a gente usar `-map [v]`, e isso DESLIGA o mapeamento
  automático do ffmpeg. Tem que mapear o áudio original explicitamente (`-map 0:a?`,
  o `?` deixa opcional), senão o clipe sai mudo. Era exatamente o bug do áudio sumido
  ao editar legenda. O teste `test_backend.py` trava essa regra (o áudio nunca pode
  sumir em nenhum dos 4 casos: só legenda, só música, ambos, nenhum).

Contrato completo dessas rotas: `BACKEND_INTEGRATION.md` seção 3.1.

---

## 7. Autenticação e segurança

- Sessão via cookie assinado (Flask `secret_key` obrigatória). Senhas com **scrypt**
  (Werkzeug). `must_change_password` força troca no primeiro acesso (gate no upload).
- Sem TLS ainda (`SESSION_COOKIE_SECURE=False`), **adiado até decidir domínio**.
  Quando subir HTTPS, virar `Secure=True`.
- Defesa das rotas internas em 3 camadas: Flask não escuta público + Nginx 404 +
  gate por subnet da bridge.
- Senha de onboarding por convenção: `!SenhaPadrãoMudar` (NÃO é hardcoded; é o que se
  passa no `manage_users.py add`). Não dá pra recuperar senha (só hash); resetar via
  CLI.

---

## 8. Operação

- **Restart/deploy**: `systemctl restart opussb`. Mudança em `.py` exige restart;
  mudança em `static/` não (Nginx serve direto, `Cache-Control: no-cache`).
- **Saúde**: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5001/health`.
- **Logs**: `journalctl -u opussb`. **Atenção**: `print()` no worker fica buffered no
  gunicorn; pra debug ao vivo use `flush=True, file=sys.stderr`. A verdade do estado
  é o banco + o S3, não o log.
- **Backups**: antes de deploy sensível, snapshot em `/opt/opussb_backups/`
  (`cp opussb.db ...` + `tar czf code-*.tgz *.py`).
- **Rollback**: as migrações de banco são aditivas (código antigo ignora tabela/coluna
  nova). Pra voltar código: `git checkout <commit> -- <arquivos>` + restart, ou
  restaurar o tarball de `/opt/opussb_backups/`.
- **Git**: branch `master`. `.gitignore` cobre `.env`, `opussb.db` (tem hashes!),
  `venv/`, `jobs/`, fontes Inter, `*.prtextstyle`. **Nunca commitar `.env` nem o
  banco.** Mensagens de commit terminam com a linha `Co-Authored-By`.
- **Memória do projeto** (contexto que persiste entre sessões do Claude Code):
  `/root/.claude/projects/-opt-opussb/memory/` (rede/acesso, backup GitHub,
  automação N8N, estilo de legenda).

---

## 9. Pendências e decisões em aberto

- `POST /api/projects/<id>/reprocess` e `GET /api/projects/<id>/download_all`: o front
  já chama, **backend ainda não implementou** (cai em toast).
- Campos extras do upload `language`/`aspect_ratio`: ignorados (saída sempre 9:16).
  `captions` é honrado (default ligado).
- TLS/HTTPS: bloqueado na decisão de domínio.
- Música: hoje é upload por clipe (sem biblioteca de presets).
- Mini editor no front: backend pronto/testado; a UI está sendo polida pelo chat do
  front (ver `FRONTEND_TODO.md`).

---

## 10. Como validar sem quebrar (receita usada)

Fabrique um job de teste, exercite via HTTP com um usuário descartável, confira no
S3 + banco, e **limpe tudo**:
```
./venv/bin/python manage_users.py add teste@local --password 'X' --active  # já ativo, sem troca
# criar job TEST*, subir base no S3, set clip_captions, logar via curl, bater nas rotas
db.delete_job("TESTxxx"); s3_client.delete_prefix("jobs/TESTxxx/")  # cleanup
./venv/bin/python manage_users.py delete teste@local
```
Frames de vídeo dá pra inspecionar extraindo PNG com ffmpeg e abrindo a imagem.

**Testes unitários** (puros, sem rede, não tocam dados reais): `./venv/bin/python
test_backend.py`. Usam um DB_PATH temporário (definido antes de importar o app) e
cobrem o builder do comando de queima (regressão do áudio), validação de segmentos,
parsing do clips_json e o round-trip SRT -> .ass.

---

## 11. Onda de correções e endurecimento (2026-06-20)

Validado em produção (restart + `test_backend.py` + teste e2e da queima com job TEST
e cleanup). Backups em `/opt/opussb_backups/` (db + tarball do código) antes da troca.

**Bug corrigido (crítico):** áudio sumia ao editar a legenda. A queima só-legenda
montava `-map [v] -c:a copy` sem mapear o áudio (ver seção 6). Corrigido com
`-map 0:a?` e isolado em `_build_burn_cmd` com teste de regressão.

**Endurecimento aplicado (sem mexer em arquitetura, custo, N8N ou modelo de scoring):**
- **db.py**: WAL + `busy_timeout=30s` + `synchronous=NORMAL` + rollback em exceção no
  `_conn()` (fecha o "database is locked" com 1 worker / 4 threads). Índices em
  `jobs(user_email, created_at)` e `jobs(status)`. (WAL cria `opussb.db-wal/-shm`,
  já no `.gitignore`; backup com `cp` deve checkpointar antes pra não pegar estado
  parcial.)
- **s3_client.py**: cliente boto3 cacheado (singleton sob lock) em vez de recriar a
  cada chamada; retries nativos (`max_attempts=3`) cobrem blips transitórios.
- **processor.py**: timeouts em TODO ffmpeg (rede de proteção contra processo
  travado); parsing tolerante do `clips_json` (`_parse_clips_json`, não levanta);
  validação de start/end no corte (pula item ruim em vez de derrubar o job);
  `-preset veryfast` + `+faststart` nos cortes; timeout (connect, read) no trigger do
  N8N (falha rápido se o host morrer).
- **reframer.py**: timeouts no mux/encode; `+faststart`; `-map 1:a:0?` (áudio
  opcional, não quebra se a fonte não tiver trilha).
- **captions.py**: `build_ass_from_segments` pula segmento com tempo inválido
  (NaN/inf, end<=start) em vez de gerar `.ass` quebrado.
- **app.py**: throttle de login em memória (anti força bruta, por IP+email); headers
  de segurança (`after_request`); erros de `/api/*` sempre em JSON (errorhandlers);
  `/health` com ping no banco + jobs ativos; validação reforçada de segmentos
  (rejeita NaN e intervalo que some no arredondamento); limites de duração no upload;
  validação de conteúdo de áudio na música (ffprobe); `put_preferences` com checagem
  de tipo; `clip_video` reusa `_clip_lookup` (DRY).
- **Logging**: `print()` trocado por `logging` no logger `opussb` com handler em
  stderr (resolve o buffer do gunicorn citado na seção 8; agora aparece no
  `journalctl` na hora, com nível e timestamp).
- **manage_users.py**: flag `--active` cria/reseta usuário já ativo (sem troca
  obrigatória), útil pra testes e scripts.

**Pré-existente, não tocado:** gunicorn 26 loga `Control server error: Permission
denied: '/var/www/.gunicorn'` no boot (HOME do www-data). É inofensivo (recurso
opcional do gunicorn) e não tem a ver com o código Python; fica de nota pra um ajuste
de unit/permissão no futuro, se incomodar.
