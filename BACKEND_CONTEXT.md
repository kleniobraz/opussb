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

11. **Nunca comece um chat usando inglês** Inglês apenas para aplicações necessárias em sistema, bash ou qualquer coisa dessa natureza, mas para conversar com Klenio e mostrar o que está acontecendo, nunca usar inglês, apenas se ele disser "fale em inglês comigo".    

---

## 2. Infraestrutura

- **VPS**: Hetzner CPX21, 3 vCPU, 4GB RAM, swap 4GB. IP público `87.99.159.235`.
  Volume separado de 79GB em `/mnt/opussb-jobs` (é o `JOB_DIR`, trabalho em disco).
- **App**: Flask + Gunicorn (`/opt/opussb/venv/bin/gunicorn`), **1 worker / 4 threads
  (gthread)**, `--timeout 3600`. systemd unit `opussb.service`
  (`WorkingDirectory=/opt/opussb`, `EnvironmentFile=/opt/opussb/.env`,
  `Restart=on-failure`). **Bind duplo**: `127.0.0.1:5001` e `172.17.0.1:5001`
  (gateway da bridge Docker). **Nunca** escuta em `0.0.0.0`.
- **Nginx** (HTTPS 443, ver §14; redirect http→https): proxy de `/` pro **Next**
  (`opussb-web` em 127.0.0.1:3000, ver FRONTEND_CONTEXT §2) e de `/api`+`/health` pro Flask.
  `client_max_body_size 15G` + `proxy_request_buffering off` no `/api`. Devolve **404 para
  `/api/callback` e `/api/internal`** (rotas internas não acessíveis de fora). [OBSOLETO: a
  frase "porta 80 / serve SPA estático de `static/`" valia antes do cutover pro Next (2026-06-21).]
- **N8N** (Docker, porta 5678): orquestra RunPod + Claude. Container alcança o Flask
  pelo gateway `172.17.0.1:5001` **direto** (não via Nginx). Workflow id
  `<no painel do N8N>`, editável pela API REST (`X-N8N-API-KEY`). Key em
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

Fonte alternativa: **`POST /api/upload/youtube`** cria o job e dispara `run_youtube_thread1`,
que adiciona um passo **`downloading`** (baixa via yt-dlp) ANTES do `extracting_audio` e daí
cai no mesmo `run_thread1`. Ver §17.

O **N8N** roda RunPod (Whisper → SRT) + Claude (scoring → clips_json) e chama
`POST /api/callback` com `{job_id, transcript (SRT), clips_json}`.

O callback grava transcript+clips_json e dispara a **thread2**:
- `cutting` (corta cada clipe com ffmpeg) → `reframing` (reenquadra 9:16) →
  `uploading_clips` (sobe os clipes + grava segmentos de legenda) → `done`.

Estados: `queued, downloading (só fonte YouTube, §17), extracting_audio, uploading_audio,
transcribing, cutting, reframing, uploading_clips, done, error`.

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
- **`captions.py`** — puro (só `re`/`math`, sem ffmpeg). `parse_srt`, `segments_for_clip`
  (recorta o SRT por clipe → dado editável), `build_ass_from_segments` (rende o .ass
  no estilo SB), **`snap_clip_bounds`** (2026-06-21: ajusta in/out do corte pra não
  cair no meio da fala — ver abaixo). Constantes do estilo no topo (ver seção 6).
  - **Auto-snap do corte (2026-06-21):** o `run_thread2` chama `captions.snap_clip_bounds`
    em cada clipe **antes** de `_cut_clips`, usando o SRT da transcrição. Estende o FIM até
    o fim do trecho falado em curso (teto +2,5s) e puxa o INÍCIO pro começo do trecho (teto
    1s); em silêncio/sem fala no ponto de corte ou sem transcript, mantém igual. Como roda
    antes do corte, o ffmpeg, os segmentos de legenda (que herdam `item["meta"]`) e a duração
    usam os limites ajustados. Função pura, coberta no `test_backend.py` (`test_snap_clip_bounds`).
    Resolve "clipe termina em cima da fala" só pra jobs NOVOS (o original some no cleanup).
- **`s3_client.py`** — `upload_file`, `download_file`, `exists`, `get_presigned_url`,
  `delete_object`, `delete_prefix`.
- **`manage_users.py`** — CLI: `add/reset/list/delete` usuários (scrypt,
  `must_change=True` por padrão). Ex.: `./venv/bin/python manage_users.py add
  fulano@seubone.com --password '<senha-inicial>'`.
- **`static/`** — SPA vanilla (sem build), servida pelo Nginx. **Domínio do chat do
  front**; o backend só expõe o contrato (`BACKEND_INTEGRATION.md`).

---

## 5. Modelo de dados (SQLite)

- **`jobs`**: `job_id, filename, status, progress, min/max_duration, local_path,
  audio_s3_url, runpod_job_id, transcript (SRT), clips_json, result_urls (JSON),
  error, created_at, updated_at, user_email (dono; NULL=legado visível a todos),
  captions (0/1, default 1), thumbnail_key (chave S3 do frame do card; NULL=sem
  frame, jobs antigos degradam pro fallback de onda)`.
- **`users`**: `email, password_hash (scrypt), must_change_password, status
  (active|pending|rejected|inactive; só active loga), is_admin (0/1), auth_provider
  (zoho|manual|bootstrap), timestamps`. Ver §14.
- **`favorites`**: `(user_email, clip_id)` — `clip_id = "<job_id>:<index>"`.
- **`preferences`**: `user_email, data (JSON)`.
- **`clip_captions`**: `clip_id, segments (JSON [{start,end,text}] relativo ao
  clipe), updated_at, caption_y (REAL: posição vertical 0..1 ou NULL=padrão, §8),
  caption_style (TEXT JSON {size,tracking,color,shadow} ou NULL=padrão SB, §16)`.
  Legenda editável + posição + estilo de fonte por clipe.
- **`clip_music`**: `clip_id, music_key (S3), volume (0..1), updated_at`. Música de
  fundo por clipe (opcional).
- **`sessions`**: `id (uuid4, PK; também no cookie), user_email, ip (best-effort, só
  exibição), user_agent, created_at, last_active`. Sessão server-side (lista + revogação
  na página de Segurança). Índice `(user_email, last_active)`.

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

- Sessão via cookie assinado (Flask `secret_key` obrigatória) + **estado server-side**
  na tabela `sessions` (ver seção 13): `login_required` e `/api/me` validam o `sid` no
  banco a CADA request, então dá pra listar e revogar sessões. Custa 1 SELECT por
  request autenticado (lookup por PK) + 1 UPDATE de `last_active` no máximo 1x/min por
  sessão. Trocar a senha encerra as OUTRAS sessões (mantém a atual). Senhas com
  **scrypt** (Werkzeug). `must_change_password` força troca no primeiro acesso (gate no
  upload).
- **TLS ativo (2026-06-22, ver §14):** HTTPS em https://sbclips.space (Let's Encrypt),
  `SESSION_COOKIE_SECURE=True`. `SameSite=Lax` mantido de propósito (o cookie precisa
  viajar no retorno top-level do OAuth do Zoho).
- Defesa das rotas internas em 3 camadas: Flask não escuta público + Nginx 404 +
  gate por subnet da bridge.
- Senha de onboarding por convenção: `!SenhaPadrãoMudar` (NÃO é hardcoded; é o que se
  passa no `manage_users.py add`). Não dá pra recuperar senha (só hash); resetar via
  CLI.

---

## 8. Operação

- **Restart/deploy**: `systemctl restart opussb`. Mudança em `.py` exige restart;
  mudança em `static/` não (Nginx serve direto, `Cache-Control: no-cache`).
- **Atenção (sessões server-side)**: a primeira subida da feature de sessões desloga
  todos os usuários ativos uma vez (cookie antigo não tem `sid`); o front cai sozinho
  na tela de login (`auth:false`) e basta logar de novo. Efeito de uma vez só.
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

- `POST /api/projects/<id>/reprocess`: o front já chama, **backend ainda não implementou** (cai em
  toast). `GET /api/projects/<id>/download_all`: **FEITO (§23)**.
- Campos extras do upload `language`/`aspect_ratio`: ignorados (saída sempre 9:16).
  `captions` é honrado (default ligado).
- ~~TLS/HTTPS~~ **FEITO** (§14): HTTPS em https://sbclips.space.
- Música: hoje é upload por clipe (sem biblioteca de presets).
- ~~Mini editor no front~~ **FEITO**: editor de clipe completo (FRONTEND §7) + posição
  (`caption_y`, §8) + estilo de fonte (`caption_style`, §16) + corte ajustável (handles, §18).
- ~~Opção C (estender o corte manualmente, estilo OpusClip)~~ **FEITA (2026-06-23, §18)** via
  handles (sobra de 10s/lado na base), sem reter o vídeo original.
- **YouTube (§17): manutenção recorrente** — cookies expiram (reexportar), `yt-dlp`/`Deno`
  precisam de update conforme o YouTube muda; bloqueio anti-robô do IP de datacenter é
  intermitente mesmo com cookies (proxy residencial adiado).
- PENDENTE (de §14): atualizar `ultralytics` (RCE); parar de servir/blindar `static/` (XSS legado).

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

---

## 12. Thumbnail real do card (2026-06-21)

O card de projeto agora mostra um frame de verdade em vez da capa generativa (onda).
Front já estava pronto: renderiza `<img src={thumbnail_url}>` quando vier não-nulo.

- **Geração** (`processor.py`, `run_thread2`, ANTES do `rmtree`, arquivos ainda em
  disco): `_best_clip` escolhe o clipe de maior score; `_generate_thumbnail` extrai 1
  frame dele com ffmpeg (`-ss 1 ... -frames:v 1 -vf scale=640:-2 -q:v 4`) **no aspecto
  ORIGINAL do corte** (`item['path']`, não o `_base.mp4` 9:16, pra casar com o card
  16/10), sobe em `jobs/{job_id}/thumb.jpg` (reusa `_s3_upload_with_retry`) e a key vai
  no `db.update_job(... status='done', thumbnail_key=...)`. **Best-effort**: falhar não
  derruba o job (key fica NULL, card cai no fallback).
- **Servir** (`app.py`): `GET /api/projects/<job_id>/thumb`, `@login_required` + dono,
  302 pra URL presignada; 404 sem key. Mesmo padrão da rota de vídeo.
- **Contrato**: `_project_from_job` devolve `thumbnail_url` = `/api/projects/<id>/thumb`
  só quando há `thumbnail_key` (senão `null`). `_clip_objects` repassa a mesma URL por
  clipe (helper `_thumb_url`). Jobs antigos sem key continuam `null` (degradação
  graciosa, nada quebra).
- **Validado**: `test_backend.py` (seleção do melhor + contrato) + e2e (geração real
  640px, rota 302, 401 sem sessão) com cleanup. Migração da coluna no padrão
  `_ensure_column`.

---

## 13. Sessões ativas server-side (2026-06-21)

Página de Segurança lista os dispositivos logados e permite encerrá-los. Antes a sessão
era só um cookie assinado stateless (não dava pra listar nem revogar); agora há estado
server-side na tabela `sessions` (seção 5). Front já estava pronto; backend só implementou.

**Rotas** (todas `@login_required`, escopo = só o usuário logado):
- `GET /api/sessions` devolve `{sessions:[{id, current, browser, os, location|null,
  last_active}]}`. `current=true` na sessão de quem pediu (o front não mostra botão de
  encerrar nela). `browser/os` vêm de um parser de user-agent próprio (`_parse_ua`, sem
  dependência externa); `last_active` vem humanizado (`_humanize_pt`, espelha o
  `ui.timeAgo` do front: agora / há N min / há N h / há N d até 30 dias / depois a data);
  `location` é o IP (ou null).
- `DELETE /api/sessions/<id>` encerra UMA (idempotente, sempre `200 {ok:true}`, escopada
  ao dono; encerrar a PRÓPRIA atual é no-op pra não se deslogar sozinho).
- `POST /api/sessions/revoke_all` encerra todas menos a atual (um único DELETE).

**Como funciona**:
- No `login`: gera `sid=uuid4`, grava a linha (ip + user_agent) e põe `session['sid']`.
  Re-login no mesmo navegador REUSA o sid (não cria linha órfã). Reap oportunista das
  expiradas (`SESSION_DAYS`).
- `login_required` e `/api/me` validam `db.get_session(sid)` a CADA request (sem cache),
  então `DELETE`/`revoke_all`/troca de senha cortam o acesso no request seguinte. Em
  sid ausente/revogado: `session.clear()` + `401 {auth:false}` (mesmo formato de sempre).
- `last_active` é atualizado com throttle de 60s, usando o próprio valor gravado (sem
  dict em memória: nada de lock/poda/vazamento, sobrevive a restart).
- `logout` apaga a linha do sid atual. `manage_users delete` faz cascade das sessões na
  mesma transação; `manage_users reset` força re-login em todos os dispositivos do usuário.

**IP / X-Forwarded-For (cuidado)**: o `_client_ip` só confia no XFF quando o peer é o
loopback do Nginx, e pega o ÚLTIMO hop (o Nginx usa `$proxy_add_x_forwarded_for`, que
anexa o IP real no fim). Loopback e a subnet da bridge viram `null`. O IP é SÓ exibição,
NUNCA entra em decisão de auth/throttle. Não usar `ProxyFix` (quebraria o
`_is_trusted_caller` das rotas internas, que precisa ver `127.0.0.1`).

**Decisões adiadas** (fora do escopo): sem max-age absoluto (janela deslizante de 7
dias); `SESSION_COOKIE_SECURE` vira `True` quando subir TLS; throttle de login segue por
email global (sem ProxyFix, todo tráfego proxiado é `127.0.0.1`).

**Validado**: design e implementação passaram por crítica multi-lente + revisão
adversarial (veredito SHIP, zero blockers). `test_backend.py` cobre parser de UA,
humanização, `_client_ip` (último hop + XFF forjado ignorado + peer não confiável),
ownership do delete, reuso de sid, reap, revogação imediata entre dois navegadores,
no-op da sessão atual, troca de senha revogando as outras, e que `/api/callback` segue
aberto (não regrediu). E2e ao vivo na produção com usuário TEST e cleanup total.

---

## 14. Domínio, HTTPS, login Zoho + cadastro/aprovação, segurança e disco (2026-06-22)

### Domínio e HTTPS (a frase "sem TLS" da §7 está OBSOLETA)
- Produção atende em **https://sbclips.space** (e www). Domínio comprado na Hostinger; DNS
  no hPanel (registro A `@` para `87.99.159.235`; `www` é CNAME pra raiz).
- Certificado **Let's Encrypt** via `certbot --nginx`. Cert em
  `/etc/letsencrypt/live/sbclips.space/`. Redirect http para https automático. `certbot.timer`
  ativo (renova sozinho; `certbot renew --dry-run` passou).
- `SESSION_COOKIE_SECURE=True`. `SameSite=Lax` mantido de propósito (o cookie precisa viajar
  no retorno top-level do OAuth do Zoho).
- nginx `server_name sbclips.space www.sbclips.space _` (o `_` preserva acesso por IP).
  CUIDADO: backup de config NÃO vai em `/etc/nginx/sites-enabled/` (o include é `*`, vira
  server block duplicado e dá "conflicting server name"). Backups ficam em `/root/nginx-backups/`.

### Auth: Zoho SSO + cadastro manual + aprovação por admin
Três vias que abrem a MESMA sessão server-side (a tabela `sessions` da §13 não mudou):
1. **Zoho SSO (padrão):** OAuth2/OIDC restrito por domínio (`ALLOWED_EMAIL_DOMAINS` =
   seubone.com, grupoquatro5.com). Entra direto, auto-aprovado (`active`). O e-mail vem do
   `id_token` (`email` + `email_verified`); o `iss` é conferido pelo hostname exato do DC.
   Scope `openid email profile`, DC `.com` (`accounts.zoho.com`). App no api-console.zoho.com
   (Server-based App); redirect URI exato `https://sbclips.space/api/auth/zoho/callback`.
2. **Cadastro manual (`POST /api/signup`):** qualquer e-mail cria conta `status=pending`; só
   loga após um admin aprovar. Resposta neutra (mesma mensagem se o e-mail é novo ou já existe)
   pra não permitir enumeração de contas.
3. **Senha (break-glass):** segue funcionando, mas só conta `active` loga.

Estados em `users.status`: `active` | `pending` | `rejected` | `inactive` (só `active` loga).
Colunas novas em `users`: `status`, `is_admin`, `auth_provider` (zoho/manual/bootstrap). Migração
aditiva (`_ensure_column`, defaults `active`/0); usuários legados viram `active`. Admin inicial
semeado no boot por `config.ADMIN_EMAILS` (`klenio.braz@seubone.com`) via `db.ensure_admin`
(garante `is_admin=1` + `active`, sem senha utilizável; ele entra via Zoho).

### Endpoints novos (`app.py`)
- `POST /api/signup` (cria pending; reusa o throttle de login como rate-limit).
- `GET /api/auth/providers` (público; o front decide se mostra o botão Zoho).
- `GET /api/auth/zoho/login` (gera `state` anti-CSRF na sessão, redireciona pro Zoho).
- `GET /api/auth/zoho/callback` (troca code por token, lê e-mail do `id_token`, valida
  `email_verified` + `iss` + domínio, provisiona `active`, abre sessão). O nome
  `/auth/zoho/callback` é de propósito (o bloco `^/api/(callback|internal)` do nginx NÃO casa).
- Admin (decorator `admin_required`, valida `is_admin` + `active` no servidor; e-mail no body):
  `GET /api/admin/users`, `POST /api/admin/approve|reject|set_active|set_admin`. Guardas: não dá
  pra se auto-recusar/desativar/rebaixar, nem derrubar o ÚLTIMO admin ativo (`db.count_active_admins`).
- `login_required` e `/api/me` checam `status=='active'` a CADA request (conta desativada cai no
  request seguinte). Helper `_open_session` extraído (login por senha e callback Zoho usam).

### config.py (vars novas, todas OPCIONAIS)
`ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REDIRECT_URI`, `ZOHO_ACCOUNTS_BASE`
(default `accounts.zoho.com`), `ALLOWED_EMAIL_DOMAINS` (default seubone/grupoquatro5),
`ADMIN_EMAILS`, `ZOHO_ENABLED` (= tem client id+secret). Sem as creds o Zoho fica desligado e o
app sobe normal. Valores reais ficam no `.env` (fora do repo).

### Segurança (varredura do Aikido) e headers
- Headers no nginx (server block 443, com `always`): HSTS, CSP, X-Content-Type-Options,
  X-Frame-Options, Referrer-Policy, Permissions-Policy. `server_tokens off` (esconde a versão).
  CSP compatível: `script`/`style` em `self`+`unsafe-inline` (Next injeta inline), `img`/`media`
  liberam `https:`/`blob:`/`data:` pra não quebrar thumbnail nem player de clipe.
- gunicorn 26 está acima do range do CVE de request smuggling (já mitigado).
- PENDENTE: atualizar `ultralytics` (achado de RCE; usado no `reframer.py`); `static/js/views.js`
  (XSS legado na SPA antiga: parar de servir ou blindar). Nenhum é janela de risco aberta.

### Disco e upload (correção)
Upload grande dava `OSError: [Errno 28] No space left on device`: o werkzeug bufferizava o
multipart no `/tmp` do disco LOCAL (38G) antes de salvar no volume. O `JOB_DIR` já apontava pro
volume de 80G (`/mnt/opussb-jobs`), mas faltava o temp. Corrigido: `TMPDIR=/mnt/opussb-jobs/tmp`
no `.env` (mesmo filesystem do `JOB_DIR`, então o `.save()` vira rename) + `proxy_request_buffering
off` no `/api` do nginx (stream direto pro Flask, sem 2a cópia local). Caches (pip/npm/apt/journal)
limpos liberaram ~4G. (`yolov8 para GPU/RunPod` segue como otimização adiada, não urgente.)

---

## 15. Projetos compartilhados (uso interno) (2026-06-22)

Decisão de produto do dono: pro uso interno do time, a biblioteca de projetos deixa de ser
por usuário e passa a ser COMPARTILHADA. Todo usuário ativo vê e edita qualquer projeto/clipe
(o caso "Zion precisa exportar um clipe que o Klenio subiu"). Quem subiu continua sendo o dono;
só o dono e os admins podem destruir. Escopo confirmado: muda SÓ a biblioteca de Projetos;
Home/Recentes e o contador "Sua produção" seguem PESSOAIS (continuam usando `list_jobs(user)`).

Mudanças (todas em `app.py`, sem migração de banco; `user_email` do job já existia como dono):
- `GET /api/projects` agora chama `db.list_jobs()` (sem argumento, traz tudo ordenado) em vez de
  `db.list_jobs(session["user"])`. A lista vira global.
- Removida a trava de dono (`job.user_email and job.user_email != session.user -> 404`) de
  `get_project`, `project_thumb` e do helper `_clip_lookup`. Como `_clip_lookup` cobre vídeo,
  base, legenda (GET/PUT), música (POST/PUT/DELETE), `caption_y` e export, todo usuário ativo
  passa a acessar e EDITAR qualquer clipe. Atenção: legenda/música são propriedade do clipe
  (compartilhadas), então a edição de um usuário vale pra todos (esperado no modelo de time).
- `_project_from_job` passa a expor `owner_email` (= `job.user_email`, NULL pra legado), pro
  front exibir "enviado por X" e decidir se mostra o botão Excluir.
- Novo helper `_can_manage(job)`: True se o usuário da sessão for admin OU o dono do job. Job
  legado (dono NULL) não tem dono pra reivindicar, então só admin gerencia.
- `DELETE /api/projects/<id>` e `POST /api/jobs/<id>/cancel` passam por `_can_manage`: 404 se o
  job não existe, 403 se existe mas o usuário não é dono nem admin. O gate é no servidor (o
  front esconder o botão é só cosmético).
- Favoritos e preferências seguem INDIVIDUAIS (são do usuário, não fazem sentido globais).

Validação: `test_backend.py` passa (nenhum teste dependia da trava de dono; os de "ownership"
são de sessões). Verificação e2e descartável (DB temporário + test client, monkeypatch do
`s3_client.delete_prefix` pra não tocar a rede): B vê e abre o projeto de A, recebe 403 ao
excluir/cancelar, o projeto sobrevive; dono e admin excluem com 200; legado só pelo admin.
Deploy: backup (db + código) + `systemctl restart opussb` (active_jobs=0) + build do front +
restart do `opussb-web`. Sem push na master (regra 9).

---

## 16. Personalização de fonte da legenda por clipe (2026-06-22)

Editor ganhou controles básicos de fonte (tamanho, espaçamento, cor, intensidade da sombra) por
clipe, queimados sob demanda. Aditivo, sem mudar o estilo SB padrão de quem não personaliza.

- **`db.py`**: coluna `caption_style` (TEXT JSON) em `clip_captions` (migração `_ensure_column`,
  igual ao `caption_y`). `get/set_clip_caption_style`. NULL = padrão SB.
- **`captions.py`**: `build_ass_from_segments(segments, caption_y=None, style=None)` aplica os
  overrides; helpers `_hex_to_ass` (#RRGGBB -> &H00BBGGRR) e `_resolve_style` (mescla com o padrão,
  clampa faixas; **shadow 0..1 = opacidade do glow**, alpha = (1-shadow)*255; 0.86 ≈ o GLOW_ALPHA
  0x24 padrão). Retrocompatível (style=None mantém o estilo SB).
- **`app.py`**: `GET/PUT /api/clips/<id>/captions` leem/gravam `style` (validado por
  `_validate_caption_style`); o cache `_cap.mp4` é invalidado no PUT (já era). A queima em
  `clip_video` passa o `style` pro `build_ass_from_segments`.
- Validado: `test_caption_style` (size/tracking/cor/sombra + clamp + `_hex_to_ass`) + e2e com job
  TEST (PUT/GET persistem e normalizam, queima reflete, cor inválida 400, style:null limpa).

## 17. Importar do YouTube (yt-dlp) (2026-06-22)

> **STATUS: DESATIVADO na UI (2026-06-22).** O front esconde o botão "Link do YouTube" (selo
> "Em breve", flag `YOUTUBE_ENABLED=false` em `upload-console.tsx`, ver FRONTEND §14) por causa
> da instabilidade do bloqueio anti-robô do IP de datacenter. O endpoint `/api/upload/youtube`
> e TODA a stack abaixo (cookies, Deno, bgutil, retry) seguem funcionais e deployados; reativar
> é só o flip do flag no front + rebuild. Decisão de produto: liberar de novo provavelmente exige
> um proxy residencial pra confiabilidade.

Além do upload, o usuário cola um link do YouTube (VOD; live em andamento é recusada). Baixa no
servidor e cai no MESMO pipeline do upload.

- **`config.py`**: `YOUTUBE_MAX_DURATION` (default 7200s = 2h, env-tunável) = teto de duração
  (controla custo de transcrição e tempo que um job segura o único slot).
- **`processor.py`**: seção YouTube. `youtube_probe(url)` (metadados, sem baixar), `_download_youtube`
  (baixa pra `original.mp4`, `--max-filesize 12G`, `--socket-timeout 30`, `--merge-output-format mp4`),
  `run_youtube_thread1(job_id, url)` (estado novo **`downloading`** -> `run_thread1`). yt-dlp chamado
  por subprocess (lista de args, sem shell) via path absoluto do venv. `_yt_error_hint` traduz erros.
  **Heartbeat** (`db.heartbeat_job(job_id,'downloading')` a cada 60s durante o download) impede o
  watchdog de 30min de ceifar um download longo (que liberaria a vaga -> 2 jobs em paralelo). Cookies
  opcionais: se existir `/opt/opussb/.youtube_cookies.txt`, passa `--cookies` numa **CÓPIA descartável**
  (`_cookie_copy_args` + `_rm_quiet`), porque o yt-dlp REESCREVE o arquivo `--cookies` a cada execução
  e, quando o YouTube invalida a sessão, grava de volta um jar degradado que DESTRÓI o master (foi o que
  matou os cookies em produção 2026-06-22: SID/LOGIN_INFO stripados). A cópia preserva o master exportado.
- **`db.py`**: `heartbeat_job(job_id, expected_status)` (UPDATE só do `updated_at`, com `WHERE status`
  pra não ressuscitar cancelado).
- **`app.py`**: `POST /api/upload/youtube` (`@login_required`). Valida URL (`_is_youtube_url`, só
  hosts youtube, anti-bypass), duração/min-max, probe (recusa `is_live`/`is_upcoming`/**`post_live`**),
  teto de duração, ANTES de reservar a vaga. Probe roda sob `_yt_probe_sem` (BoundedSemaphore(2),
  429 se exceder) pra não esgotar as 4 threads do gunicorn. Helper `_pref_captions_default` (DRY
  com a rota de upload).
- **Dependências de SISTEMA (já instaladas na VPS, NÃO no repo):** `yt-dlp` + `yt-dlp-ejs` no venv;
  **Deno** em `/usr/local/bin` (runtime JS sandboxed pro desafio do YouTube; node 20 do nvm é velho
  demais, exige >= 23.5). O `opussb.service` enxerga `/usr/local/bin` (PATH padrão do systemd, `.env`
  não seta PATH). Cookies em `/opt/opussb/.youtube_cookies.txt` (600, gitignored).
  **Provedor de PO token (bgutil):** desde 2026-06-22 o YouTube exige um GVS PO token (sem ele o
  download dá 403, mesmo com cookies+Deno). Resolvido com o provedor padrão da comunidade: container
  Docker `brainicism/bgutil-ytdlp-pot-provider` (nome `bgutil-provider`, `-p 127.0.0.1:4416:4416`,
  `--restart unless-stopped`) + plugin `bgutil-ytdlp-pot-provider==1.3.1` no venv (**as versões do
  server e do plugin TÊM que casar**). O yt-dlp auto-descobre o provedor em 127.0.0.1:4416 e gera o
  token (`[pot:bgutil:http] Retrieved a gvs PO Token`). Persiste no boot (docker enabled + restart
  unless-stopped). Stack completa do YouTube: cookies + Deno (n-sig) + bgutil (PO token) + cópia
  descartável dos cookies.
- **Realidade operacional (manutenção):** baixar do YouTube é alvo móvel. (1) O IP de datacenter
  (Hetzner) leva "confirme que não é um robô" de forma INTERMITENTE mesmo com cookies+Deno+PO token; a
  maioria passa. Quando falha, `_run_ytdlp` **retenta automaticamente** (3x, `_YT_RETRIES`, só no bot-block,
  com cópia nova de cookies a cada tentativa) porque é intermitente. Blindagem total só com proxy
  residencial (decisão adiada). (2)
  Os cookies EXPIRAM (reexportar de uma conta Google dedicada de tempos em tempos). (3) `yt-dlp` e o
  Deno precisam de updates conforme o YouTube muda os desafios. (4) Achados da revisão adversarial
  corrigidos (heartbeat/slot, semáforo de probe, post_live, max-filesize, socket-timeout). (5) O
  container `bgutil-provider` (PO token) é mais uma peça que quebra/precisa update quando o YouTube
  muda; se o download voltar a dar **403**, checar `docker ps` do `bgutil-provider`, atualizar a imagem
  + o plugin (versões casadas), e reexportar cookies. (6) Bug já corrigido: o yt-dlp REESCREVIA o
  arquivo `--cookies` e destruía o master (SID/LOGIN_INFO); agora usa cópia descartável (`_cookie_copy_args`).
- Validado: `test_youtube_helpers` (URL anti-bypass + tradução de erro). E2e do download real (32MB
  em ~3s com cookies+Deno). Deploy: backup + restart `opussb` (active_jobs=0) + build/restart `opussb-web`.

## 18. Corte ajustável na timeline com "handles" (margem) (2026-06-23)

Pedido do dono: a IA às vezes erra o corte (seco demais ou no tempo errado) e o editor não deixava
ajustar (o clipe saía cortado exato em `[start,end]` e o original é apagado no cleanup). Em vez de
reter o vídeo inteiro no S3 (caro; era a "Opção C" adiada da §9), cada clipe agora é cortado e
reenquadrado com uma SOBRA de ~10s de cada lado, e o editor abre/fecha o corte dentro dela (alças
na timeline). Decisão fechada com o dono: handle 10s/lado, simétrico, configurável. Deploy: backup +
restart `opussb` + build/restart `opussb-web`.

**Modelo (a âncora mudou):** o `clip_NN_base.mp4` deixa de ser o corte exato e passa a ser a JANELA
`[start-H, end+H]` (clamped a `[0, dur_vídeo]`; `H=config.CLIP_HANDLE_SECONDS`, default 10),
reenquadrada 9:16. Por clipe, persistimos em `clip_captions` (4 colunas novas via `_ensure_column`):
`window_start` (s absolutos no original), `window_dur` (duração real do base, via ffprobe do
produzido), `in_point`/`out_point` (corte oficial, relativos à janela). Os **segmentos de legenda
passam a ser relativos à janela** (`segments_for_clip(transcript, win_start, win_end)`, cobrindo as
sobras), então estender o corte traz a legenda nova de graça. `NULL` nas 4 colunas = clipe sem
handles (legado ou `H=0`): comporta-se como antes (sem trim).

**Onde foi mexido:**
- `config.py`: `CLIP_HANDLE_SECONDS` (default 10, env-tunável; 0 desliga e volta ao corte exato).
- `db.py`: 4 colunas em `clip_captions` + `get_clip_window`/`set_clip_window`/`set_clip_inout`.
- `processor.py`: `_cut_clips(..., handle)` corta a janela e guarda `win_start`; `run_thread2` mede
  `window_dur` (`_probe_duration`), persiste window/in/out, ancora a legenda na janela;
  `_generate_thumbnail` extrai o frame de DENTRO do corte (não da sobra-cabeça).
- `app.py`: queima com **trim** em `_build_burn_cmd`/`_burn_clip`/`clip_video`; `clip_video` lê a
  janela, calcula `trim_needed`, suporta `?nocap=1` (corte cru, cache `_trim.mp4`); `_invalidate_cap`
  apaga `_cap.mp4` E `_trim.mp4`; GET `/captions` expõe `bounds`, PUT aceita `in`/`out` (clamp +
  mínimo de 1s, 409 sem handles). `_MIN_CUT_SECONDS=1`.
- `test_backend.py`: 4 casos de `_build_burn_cmd` COM trim + `test_clip_window`.

**Mecânica do trim na queima (VALIDADA empiricamente com o ffmpeg 4.4.2 da VPS, NÃO assumida):** a
queima corta `[in,out]` da janela via **output seek** (`-ss <in> -t <dur>` DEPOIS do `-i`, com
`-avoid_negative_ts make_zero`). Ponto contraintuitivo (uma proposta inicial errou): o filtro
`subtitles` enxerga o **PTS da JANELA** (não rezerado) e o corte/reset acontece depois; então a
legenda **NÃO é deslocada** (fica na escala da janela) e casa frame a frame. A música em loop é
limitada ao corte pelo `amix duration=first` (segue `[0:a]`, a fala), sem `-t` próprio. A regra de
áudio antiga não regrediu (`-map 0:a?` + `-c:a copy` no caso só-legenda; deslize de áudio <= 1 frame
AAC, imperceptível). Experimento: vídeo com contador queimado + legenda em tempo conhecido, cortado e
inspecionado frame a frame (a legenda deslocada SUMIA; a da janela casava). E2e real: `run_thread2`
num vídeo horizontal de teste produziu a janela 9:16 e o corte queimado bateu (trecho certo + legenda
sincronizada + áudio), com cleanup total.

**Front:** ver FRONTEND_CONTEXT §15 (alças de trim + máscara das sobras; play vira prévia do corte;
"Exportar sem legenda" usa `/video?nocap=1`).

**Limites honestos:** corte só dentro da sobra de 10s (erro maior = reprocessar); clipe sem fala
segue "Edição indisponível"; clipes ANTIGOS (sem janela) não têm alças (só encurtam dentro do que há).
Custo aceito: reframe mais longo por clipe de origem horizontal (proporcional à sobra; origem já
vertical pula o reframe) e ~40-50% mais storage por clipe (ainda ~50x menos que reter o original).

## 19. Estabilidade da queima + reframe mais rápido e travado (2026-06-23)

Três frentes nascidas de um incidente e de feedbacks dos cortes. Tudo deployado (backup db+código
em `/opt/opussb_backups/`, restart com `active_jobs=0`, `test_backend.py` + `tests/test_reframer.py`
verdes). Memória do projeto: `reframe-burn-perf-and-gpu-plan`.

### 19.1 Queda do site sob carga: semáforo global de queima (crítico)
**Sintoma:** ao terminar um job e abrir vários clipes, o site ficava "fora do ar" (nginx/Next de
pé, mas `/api` e `/health` em timeout). **Causa raiz:** a queima sob demanda (`clip_video`) só tinha
lock POR CLIPE (`_clip_burn_lock`); clipes diferentes queimavam em paralelo (uma por thread do
gunicorn), cada ffmpeg libx264 usando os 3 vCPUs -> load ~10, RAM no swap, threads sem CPU pra
responder. NÃO é falta de hardware: é concorrência (nenhum tamanho de box remove o bug).
**Correção (`app.py`):** semáforo GLOBAL `_burn_sem = BoundedSemaphore(config.BURN_CONCURRENCY)`
(default 1) adquirido em `clip_video` antes do `_burn_clip`, liberado SEMPRE no `finally` (blindado:
`tmp=None` + release garantido mesmo se o `mkdtemp` falhar, senão vazaria slot). Se o slot não vier
em `BURN_QUEUE_TIMEOUT` (default 120s), serve a BASE sem legenda (clipe sempre toca, re-tenta depois)
em vez de prender a thread. Config nova em `config.py`: `BURN_CONCURRENCY`, `BURN_QUEUE_TIMEOUT`.
Teste: `test_burn_semaphore` (nunca concede além do teto). Diagnóstico no `journalctl`: load alto +
vários `ffmpeg ... burn_*` simultâneos.

### 19.2 Reframe em encode ÚNICO (mais rápido, sem regressão)
**Antes:** o `reframer.py` fazia DOIS encodes: cv2.VideoWriter gravava um `.tmp.mp4` em **mp4v** e
depois o `_mux_audio` re-encodava tudo em libx264 **sem `-preset`** (ou seja, `medium`, lento).
**Agora:** `_pipe_reframe_encode` envia os frames recortados direto pro ffmpeg via stdin (rawvideo
bgr24 1080x1920) num ÚNICO encode libx264 `-preset veryfast -crf 20 -pix_fmt yuv420p`, com o áudio
do original (`-map 1:a:0?`) e `-shortest`. Remove um encode inteiro + o temp em disco + a perda de
geração do mp4v. **Cuidado de implementação:** stderr do ffmpeg vai pra ARQUIVO temporário (não PIPE;
um PIPE não drenado travaria o ffmpeg em deadlock enquanto escrevemos no stdin); BrokenPipe/timeout
-> levanta e o `run_thread2` cai no `_center_crop_fallback`. `_mux_audio` foi REMOVIDO (substituído).
Passe 1 do YOLO agora usa `grab()` nos frames pulados + `retrieve()` só nos amostrados (menos
conversão BGR). `_encode_with_subtitles` (caso já-9:16) também ganhou `-preset veryfast -crf 20`.

### 19.3 Enquadramento TRAVADO (feedback "se mexe muito, ideal fixo no rosto")
O rastreamento por YOLO balançava. Causas: pulava pro CENTRO quando perdia a detecção; pegava a
maior confiança do frame (saltava entre pessoas); seguia cada gesto sem zona morta nem limite de
velocidade. **Correção (`reframer.py`):** câmera virtual TRAVADA.
- `_detect_subject_cx` devolve o CENTRO do sujeito (ou None se não há pessoa >= `REFRAME_MIN_CONF`);
  com sujeito anterior, escolhe o box mais PRÓXIMO do anterior (continuidade), não o mais confiante.
- No passe 1, None = "sem detecção" -> SEGURA a última posição (não pula pro centro).
- `_build_locked_trajectory`: monta o centro desejado por frame (segura o último válido) e roda uma
  câmera virtual com ZONA MORTA (`REFRAME_DEADZONE_FRAC`, 35% da largura: dentro dela não mexe) +
  PAN LIMITADO (`REFRAME_MAX_PAN_FRAC`, 18%/s: quando precisa andar, vai devagar, sem salto) +
  polimento por média móvel. Sem nada detectado no clipe inteiro -> corte central fixo.
- Config nova: `REFRAME_DEADZONE_FRAC`, `REFRAME_MAX_PAN_FRAC`, `REFRAME_MIN_CONF` (env-tunáveis:
  deadzone maior + pan menor = mais fixo). Só vale pra clipes NOVOS. Testes:
  `tests/test_reframer.py` (sujeito parado = corte fixo, continuidade segue o mais próximo, salto =
  segue devagar) + reframe real validado (1080x1920 + áudio). **NÃO voltar a deixar o corte
  "perseguir" o movimento.**

### 19.4 Log de tempo por etapa (instrumentação)
`run_thread2` loga `[timing] corte: ...`, `[timing] reframe clip N: ...` e `[timing] reframe total:
...` (via `time.monotonic`). Com isso o próximo job mostra no `journalctl -u opussb | grep timing`
quanto é corte vs reframe, pra decidir com número se o reframe vai pra GPU (RunPod serverless;
plano na memória `reframe-burn-perf-and-gpu-plan`). O corte ainda usa OUTPUT seek (`-ss` depois do
`-i`, decodifica do zero a cada clipe); otimizar pra INPUT seek ficou pendente (timing-sensível pelos
handles, decidir com o dado do log).

## 20. Reframe na GPU (RunPod serverless, 2º endpoint) (2026-06-23)

Offload do reframe (a etapa mais pesada) pra um SEGUNDO endpoint RunPod serverless (GPU), separado
do Whisper (que roda via N8N). Os clipes reenquadram EM PARALELO na GPU (fan-out), derrubando o
reframe de dezenas de min pra ~1-3 min, com custo de centavos/vídeo e $0 ocioso. **Atrás de flag,
default DESLIGADO** (sem mexer no comportamento atual até o endpoint existir e ser testado). Fallback
pra CPU por clipe sempre ativo: a GPU falhar nunca derruba o job. GPU escolhida: **L4 24GB** (A5000
fallback). Decisão e plano: memória `reframe-burn-perf-and-gpu-plan`; GEX44 dedicada rejeitada
(cara pro volume). Status no deploy desta seção: **Fase A (código, flag off)**; o endpoint ainda
não foi criado. **[ATUALIZADO 2026-06-23: HOJE ESTÁ EM PRODUÇÃO E LIGADO. Ver §21. O endpoint foi
criado (`wni4gr0w1j76t3`), a imagem foi pro DOCKER HUB (não GHCR: Actions bloqueado por billing), e
a flag está ON. As frases "Fase A / GHCR / pra LIGAR pendente" desta §20 estão OBSOLETAS; §21 vale.]**

**Lógica compartilhada (sem divergência VPS vs GPU):** `reframe_core.py` (novo) tem a matemática PURA
do reframe (`detect_subject_cx`, `build_locked_trajectory`, `moving_avg`, e `reframe_to_file` com o
pipe de frames pro ffmpeg, **codec parametrizado**). Sem `import config` (params entram por arg).
`reframer.py` virou fino (delega ao core com libx264 veryfast); o worker GPU usa o MESMO core com
`h264_nvenc`. Câmera travada (§19.3) idêntica nos dois. `reframer._filter_escape` reexportado pro
`processor.py`. Testes de câmera migraram pra `tests/test_reframer.py` apontando o `reframe_core`.

**Fluxo (`processor.py`, `_reframe_clips`/`_reframe_clips_gpu`):** com `REFRAME_GPU_ENABLED`, por clipe
sobe o corte pro S3 (`jobs/{job}/tmp/cut_NN.mp4`), gera presigned GET (corte) + presigned PUT
(`clip_NN_base.mp4`), e SUBMETE todos (fan-out) ao endpoint. O worker baixa, reenquadra (NVENC) e faz
PUT da base direto na key final, devolvendo `{"duration"}`. O `run_thread2` faz poll, pega `window_dur`
do retorno (não há arquivo local pra ffprobe), e pula o upload (a base já está no S3). Falha/timeout
de um clipe -> reframe LOCAL de CPU desse clipe. Cortes temporários do S3 são apagados no fim. Thumbnail
segue do corte local (`item['path']`). Helpers anotam em cada item: `final` (path local ou None),
`gpu_base_key` (key S3 ou None), `window_dur`.

**Peças novas:**
- `reframe_core.py`: lógica pura compartilhada.
- `runpod_client.py`: `submit_reframe` (POST /run, executionTimeout em ms) + `poll` (GET /status,
  COMPLETED/FAILED, tolera blip de rede, output com `error` vira falha). Bearer `RUNPOD_API_KEY`.
- `s3_client.get_presigned_put_url(key, content_type, expiry)`: o worker sobe sem credencial de longa
  duração. **Content-Type do PUT TEM que casar com o assinado, senão 403** (gotcha nº 1).
- `gpu_reframe/handler.py` + `Dockerfile` + `requirements.txt`: worker do SDK `runpod`. Base
  `pytorch/...-cuda12.1-...-runtime` + `apt ffmpeg` (já traz `h264_nvenc`) + `libgl1` (opencv); modelo
  YOLO baixado no build e carregado UMA vez por worker; `ultralytics==8.4.66` pinada (mesma da VPS).
- [OBSOLETO] Existiu um `.github/workflows/build-reframe-image.yml` (CI -> GHCR), mas a rota de CI foi
  ABANDONADA (Actions bloqueado por billing) e o arquivo foi REMOVIDO do repo em 2026-06-23. A imagem é
  buildada na VPS e publicada no Docker Hub (ver §21).
- `config.py`: `RUNPOD_REFRAME_ENDPOINT_ID` ("" = desligado), `REFRAME_GPU_ENABLED` (exige a flag E o
  endpoint id), `REFRAME_GPU_TIMEOUT` (1800s), `REFRAME_PUT_CONTENT_TYPE` ("video/mp4").

**Pra LIGAR (Fase B/C, pendente):** (1) deixar o pacote do GHCR público (ou dar PAT read:packages à
RunPod); (2) criar o endpoint apontando pra imagem, GPU L4 (A5000 fallback), maxWorkers ~10,
executionTimeout ~1800s, idle ~30s, active 0, FlashBoot on; (3) testar o endpoint isolado (script
descartável: sobe corte, submete, poll, ffprobe); (4) pôr `RUNPOD_REFRAME_ENDPOINT_ID` + `REFRAME_GPU_ENABLED=true`
no `.env`, restart, rodar 1 job real e comparar `[timing]`. Rollback = derrubar a flag.

**Validação (Fase A):** `test_backend.py` (presigned PUT + `runpod_client` mockado: submit/poll/erro)
+ `tests/test_reframer.py` (7, câmera no `reframe_core`) + reframe real CPU (1080x1920 + áudio, flag
off, comportamento inalterado) + py_compile do worker.

## 21. GPU reframe EM PRODUÇÃO + cap de CPU da queima + corte input-seek (2026-06-23)

Continuação direta da §19/§20, tudo DEPLOYADO (restart com `active_jobs=0` + backup). **ATENÇÃO: o
cap da queima (21.2) e o corte input-seek (21.3) estão deployados mas AINDA NÃO commitados** (ver
"Estado de commit" no fim).

### 21.1 Reframe na GPU foi pra PRODUÇÃO (atualiza o status da §20)
- **Endpoint LIVE:** `wni4gr0w1j76t3` (RunPod serverless, nome "sbclips-reframer"). `.env`:
  `RUNPOD_REFRAME_ENDPOINT_ID=wni4gr0w1j76t3` + `REFRAME_GPU_ENABLED=true`. Fallback CPU por clipe ativo.
- **Imagem foi pro DOCKER HUB, não GHCR.** A rota de CI via GitHub Actions foi ABANDONADA (estava bloqueada
  por billing da conta GitHub); o workflow `build-reframe-image.yml` foi REMOVIDO do repo em 2026-06-23
  (ficava auto-falhando a cada push em `gpu_reframe/`, gerando notificação à toa). A imagem é **buildada NA
  PRÓPRIA VPS** (os ~17GB livres do disco comportam a CUDA de ~11GB; é transitório, `docker image prune -af`
  recupera depois) e publicada em **`kleniobraz/opussb-reframe:latest` (Docker Hub, PÚBLICO)**. Pra o pull
  autenticado (fugir do rate-limit anônimo do Docker Hub) o endpoint tem uma **registry credential** (user
  `kleniobraz` + token); o token está em `/root/.dockerhub_token` (600). **Rebuild =** `docker build -f
  gpu_reframe/Dockerfile -t kleniobraz/opussb-reframe:latest .` na VPS + `docker push` + `docker rmi`/prune.
- **GPU types (CUIDADO, incidente real):** a RunPod escalou workers em **RTX PRO 6000 Blackwell (MIG 24GB,
  sm_120)**, que o nosso PyTorch 2.4.1/cu121 (suporta sm_50-90) NÃO inicializa → workers "unhealthy",
  paralelismo morto (só 1 worker 4090 saudável pegava os 7 clipes em fila). **Correção: nos Enabled GPU
  types do endpoint, DESMARCAR "PRO 6000 MIG 24GB".** Manter L4, A5000, 3090, 4090, A4000/A4500, RTX
  2000/4000 Ada (sm_86/sm_89, suportados). NÃO adianta mexer no "Minimum CUDA" (o problema é o kernel do
  torch, não o driver). Pra usar Blackwell um dia: rebuild com torch >= 2.6/cu124. Config do endpoint:
  maxWorkers **7** (cota da conta = 10, Whisper usa 3), executionTimeout 1800s, idle 30s (irrelevante:
  "queue endpoint" escala a zero na hora), active 0, FlashBoot on.
- **Validado em produção:** um job real reenquadrou **7/7 na GPU** (sem fallback). Naquele run demorou
  ~11,5 min SÓ porque a Blackwell crippou o paralelismo; com ela desmarcada, roda em paralelo. Teste
  isolado do endpoint: COMPLETED ~15s, saída 1080x1920 + áudio + NVENC.

### 21.2 Cap de CPU da queima (conserta "site trava quando o job termina")
A §19.1 (semáforo) impediu N queimas EM PARALELO, mas **uma única queima libx264 sozinha satura os 3
vCPUs (300%)** e sufoca o web app quando um job termina e o front abre os clipes (estampida). Correção
(`app.py` `_build_burn_cmd`/`_burn_clip`, `config.py`):
- `BURN_FFMPEG_THREADS=2` → `-threads 2` no ffmpeg da queima (deixa >=1 núcleo pro web).
- `BURN_NICE=15` → `_burn_clip` roda o ffmpeg com `preexec_fn=lambda: os.nice(15)` (cede CPU às
  requisições web; o site responde mesmo durante a queima).
- Teste: `test_backend.py` (`-threads` presente). **Os mesmos knobs são reusados no corte (21.3).**

### 21.3 Corte com INPUT-seek (conserta "corte demorando ~40 min")
Resolve a pendência da §19.4. `_cut_clips` (`processor.py`) trocou OUTPUT-seek (`-ss` DEPOIS do `-i`, que
decodificava a fonte inteira desde o zero pra cada clipe = o gargalo de ~40 min numa fonte de 1h38) por
**INPUT-seek** (`-ss` ANTES do `-i` + `-t`), que pula direto pro ponto via índice. Continua **frame-accurate**
(validado: frame inicial idêntico ao método antigo; `win_start` = o pedido, então a matemática dos handles
§18 NÃO muda). Roda **niced + threads-capped** (BURN_NICE/BURN_FFMPEG_THREADS) pra não travar o site durante
o corte. Medido: 6x+ mais rápido em fonte curta; em fonte longa cortando perto do fim, ~10-15x. Estimativa:
~40 min → ~2-4 min.

### Estado de commit e segurança
- Já em `ccf4886` + `2daaac9` (pushed): semáforo, reframe single-pass/lock, timing, handles, e a Fase A do
  reframe GPU (código + worker + CI).
- **Commitado nesta sessão (2026-06-23):** cap da queima + corte input-seek (`app.py`, `config.py`,
  `processor.py`, `test_backend.py`, `.dockerignore`) + docs `§21`/FRONTEND `§16-17`. **Pushado em 2026-06-23**
  (`origin/master` em `dbef94d`, autorizado pelo dono).
- **Segurança:** token do Docker Hub **rotacionado em 2026-06-23**. O antigo (exposto em chat) foi revogado no
  Docker Hub; a registry credential do endpoint na RunPod e o `/root/.dockerhub_token` foram atualizados, e o
  login foi validado com o antigo JÁ revogado (prova de que o arquivo tem o token novo).

## 22. Heartbeat no corte e reframe (watchdog) (2026-06-23)

Resolve a pendência "etapas pesadas não mandam heartbeat" (handoff #5). O watchdog (`reap_stale_jobs`,
`JOB_STALE_MINUTES`=30min) marca como `error` qualquer job sem `updated_at` fresco; o corte (`cutting`) e o
reframe (`reframing`) podem rodar minutos sem update intermediário, então um upload novo que dispare o reap
no meio poderia ceifar um job VIVO. Correção (`processor.py`):
- Novo context manager `_heartbeat(job_id, status, interval=_YT_HEARTBEAT_SECONDS=60)`: sobe uma thread
  daemon que chama `db.heartbeat_job(job_id, status)` a cada 60s até sair do `with`. O `status` esperado no
  `heartbeat_job` (WHERE status=...) garante que um cancelamento concorrente (status virou `error`) NÃO
  ressuscita o job. Generaliza o antigo `_download_heartbeat` (removido; o download do YouTube agora usa o
  mesmo `with _heartbeat(job_id, "downloading")`).
- `run_thread2` envolve o corte com `with _heartbeat(job_id, "cutting")` e o reframe com
  `with _heartbeat(job_id, "reframing")`. Cobre tanto o reframe na GPU (poll longo) quanto o fallback CPU.
- `test_backend.py` (puro) verde + `py_compile`; deploy com `active_jobs=0` + backup (DB + tarball) e restart
  do `opussb`. **Pushado em 2026-06-23** (`origin/master` em `dbef94d`).

## 23. "Baixar todos os clipes" (download_all) (2026-06-23)

Implementa o `GET /api/projects/<id>/download_all` que o front já chamava (antes caía em toast). SEM zip no
servidor (custo de CPU/RAM e travaria o `/health`): a rota lista os clipes e o front baixa um a um; as
queimas seguem serializadas pelo semáforo (§19.1/§21.2). Deployado + pushado (`origin/master`).

- **`GET /api/projects/<id>/download_all`** (`app.py`, `@login_required`, biblioteca compartilhada): devolve
  `{clips: [{id, filename, url}], count}`; `url = /api/clips/<id>/video?dl=1`. 404 se o job não existe; lista
  vazia se ainda não há clipes (job processando).
- **`clip_video` aceita `?dl=1`**: deriva um nome de arquivo amigável (`_clip_download_name`, do título do
  `clips_json` -> "NN titulo.mp4", senão "clip_NN.mp4") e aplica em TODAS as redireções via o helper novo
  `_clip_redirect(key, download_name)`. Sem `dl`, comportamento idêntico ao de antes.
- **`s3_client.get_presigned_url(key, expiry, download_name=None)`**: com `download_name`, embute
  `ResponseContentDisposition: attachment; filename="..."` na presignada -> o S3 devolve `Content-Disposition:
  attachment` e o navegador BAIXA (não toca inline). É necessário porque o `/video` faz 302 pro S3
  (cross-origin), e aí o atributo `download` de um `<a>` é ignorado; a disposition na própria URL presignada
  resolve, sem alterar o objeto nem o player.
- Helpers novos (`app.py`): `_safe_filename` (NFKD->ASCII, só `[A-Za-z0-9 ._-]`, limita), `_clip_download_name`,
  `_clip_redirect`. Teste `test_download_all` (helpers + presigned com/sem disposition + rota 200/404/shape).
- Front: FRONTEND §19. Validado e2e (usuário TEST) + `test_backend.py` verde.

## 24. Reframe: troca de câmera por GEOMETRIA (Fase 0, anti-vazio multicam) (2026-06-24)

**Problema (feedback de cliente):** em podcast multicam (3 câmeras, MESMO cenário), o auto-reframe caía no
"vazio" (cenário/letreiro/mesa) nas TROCAS de câmera, principalmente no plano aberto (dois apresentadores
afastados não cabem no 9:16). Diagnóstico (workflow + 2 clipes reais): a câmera virtual travada (§19.3) NÃO
re-adquiria o sujeito após um corte; e quando o YOLO não detectava ninguém, segurava a posição do plano
anterior (= o vazio).

**Decisão (dono):** detectar a troca por GEOMETRIA, NÃO por cor (cor não distingue câmeras do mesmo cenário)
nem por identidade (muda a cada episódio). O layout é FIXO: **host sempre à esquerda, convidado sempre à
direita.**

**`reframe_core.py` (puro, compartilhado CPU+GPU; a trava de câmera + a segmentação por plano do §19.3
ficaram INTOCADAS, mudou só o SINAL de troca e a escolha do sujeito no pass-1):**
- `detect_people`: todas as pessoas (conf, cx, box_w) ordenadas por cx (generaliza o antigo `detect_subject_cx`,
  removido).
- `_merge_boxes`: funde caixas duplicadas (o YOLO conta a mesma pessoa 2x) -> contagem estável (senão oscilava
  2/3/4 e quebrava a classificação de plano).
- `_classify`: 1 pessoa = CLOSE (enquadra nela); 2+ = PLANO ABERTO, default = CONVIDADO (mais à direita), com
  HOLD quando ele some um frame (não pula pro host/vazio); 0 = CENTRO. (Trocou a "continuidade pelo lado do
  último close", que era cara-ou-coroa num close centralizado, pelo default convidado-direita.)
- `_cuts_from_kinds`: a troca de câmera sai do histórico de tipos com DEBOUNCE (>=3 amostras) -> absorve o YOLO
  piscando (num clipe real, 6 cortes falsos viraram 0).
- `_motion_bias_wide`: STUB (TODO Fase 2: viés por movimento esquerda/direita no plano aberto pra escolher quem
  fala; o keyframe da Fase 1 cobre por enquanto).
- Knob `REFRAME_CUT_DETECT` (`config.py`, default `true`; `false` = um plano só / rollback). Fiação: `reframer.py`
  (CPU), `gpu_reframe/handler.py` + `processor.py` (param `detect_cuts` no submit do RunPod).

**Validação:** `tests/test_reframer.py` (`_merge_boxes`, `_classify`, `_cuts_from_kinds`, `detect_people`,
segmentação) + `test_backend.py` verdes; e empírica em 2 clipes reais do podcast (closes perfeitos; plano aberto
segue o convidado de forma estável, sem vazio nem cortes falsos). Resíduo honesto: se for o HOST falando no plano
aberto, mostra o convidado, e é o keyframe (Fase 1) que corrige.

**Deploy (2026-06-24):** restart `opussb` (active_jobs=0 + backup) pro caminho CPU/processor; **REBUILD da imagem
Docker GPU na VPS** (`docker build -f gpu_reframe/Dockerfile -t kleniobraz/opussb-reframe:latest .` + `docker push`
+ `docker builder prune -af`) pra a GPU herdar a geometria; git push (`origin/master` em `fc1c9c3`). **CUIDADO
RunPod:** workers serverless idle NÃO atualizam num push de `:latest` (cache); pra o próximo job usar a imagem
nova, LIBERAR os workers idle no painel (Manage -> Release workers). Só afeta clipes NOVOS.

**Roadmap Fase 1 (crop manual por KEYFRAME, não-destrutivo; aguarda OK de storage do dono):** o auto vira a
trajetória base; o usuário fixa keyframes só onde errar (ex.: host falando no plano aberto), aplicado por
RE-RENDER via `reframe_core` (NÃO crop variável no tempo na queima, que seria caro/estampida). Exige reter o
corte 16:9 por clipe no S3 (~100GB steady com TTL 30d, ~10% de 1TB); o corte 16:9 JÁ é produzido e no caminho
GPU já sobe pro S3 (bastaria parar de deletar). **DECISÃO do dono (2026-06-24): a Fase 1 será desenvolvida e
validada em SERVIDOR DE TESTES/staging primeiro, NÃO direto em produção, pra não arriscar o que está no ar**
(montar o staging é a Fase 0 do plano de usabilidade; ver FRONTEND §20). Memória do projeto:
`reframe-void-fix-and-crop-editor`.

## 25. Estado atual e próximos passos (fim da sessão 2026-06-24)

Resumo vivo pra retomar rápido depois de um clear. Produção saudável (`active_jobs=0`, `opussb` + `opussb-web`
ativos; https://sbclips.space).

**EM PRODUÇÃO (`origin/master` em `fc1c9c3`):** tudo das §1-24. Destaques recentes: reframe por GEOMETRIA (§24,
deployado + imagem GPU rebuildada/pushada + validado em 2 clipes reais), `download_all` (§23), cap da queima +
corte input-seek (§21), reframe na GPU (§20-21), e a rotação do token Docker Hub (§21, antigo revogado).

**LOOSE ENDS / PRÓXIMOS PASSOS:**
1. **2 commits de DOC locais NÃO pushados** (regra 9): `a7409a3` (docs download_all + reframe) e `34534c6`
   (docs Fase 1 staging + plano do editor §20). Rodar `git push origin master` quando o dono autorizar.
2. **RunPod:** liberar os workers idle do endpoint `wni4gr0w1j76t3` (Manage -> Release workers) pra o próximo
   job puxar a imagem nova da geometria (worker idle pode ter a imagem antiga em cache; §24).
3. **Validar** o reframe novo no próximo job multicam real: gerar contact sheets do 9:16 (close + plano aberto
   seguindo o convidado, sem vazio nem cortes falsos). Receita usada na sessão: baixar o `clip_NN_base.mp4` do
   S3, `ffmpeg fps+tile` num mosaico, e ler a imagem.
4. **Usabilidade do editor:** plano em fases em FRONTEND §20 (nada implementado). Próximo passo concreto =
   **Fase 0** (fixar as dores específicas COM o dono + montar o STAGING). **Decisão do dono:** a Fase 1 do crop
   (§24) e todo o trabalho de editor serão feitos em STAGING primeiro, NÃO em produção.
5. **Pendências antigas que seguem:** `reprocess` (§9), waveform/filmstrip reais (CORS no S3 ou server-side, ver
   FRONTEND §20 Fase 2), atualizar `ultralytics` (achado de RCE, §14), parar/blindar o `static/` legado (§14).

## 26. Legenda ancorada no tempo por PALAVRA (word_timestamps) (2026-06-25)

Conserta a queixa do dono ("legenda adiantada, nao respeita a fala"). Causa: usavamos so tempo por
FRASE (SRT) + reparticao char-proporcional (`_layout_segment`), entao a legenda entrava antes da fala
(pior em clipe que comeca no meio de uma frase: o metodo antigo jogava as palavras ja faladas antes da
janela no inicio do clipe).

**DESCOBERTA CHAVE (probe real, ver memoria `whisper-word-timestamps`):** o worker Whisper do RunPod
(`RUNPOD_ENDPOINT_ID=fczh1t4sx02j1s`, model turbo) JA devolve `output.word_timestamps` (lista plana
`{start,end,word}`), de graca, e o N8N DESCARTAVA (so repassava `output.transcription`, o SRT). O
`segments[].words` por segmento NAO existe nesse worker; o tempo por palavra so vem no top-level
`output.word_timestamps`. A gente ja manda `input.word_timestamps=true` no "Submit RunPod Job".

- **`captions.py`**: `words_for_clip(words, srt_text, clip_start, clip_end, max_gap=0.6)` monta os
  eventos ancorados na PALAVRA real (quebra por MAX_LINE_CHARS, por fronteira de frase do SRT, e por
  pausa > max_gap). Modo DUAL: sem words retorna [] e o chamador cai no `segments_for_clip` (sem
  regressao). `clip_words_window(words, clip_start, clip_end)` = palavras CRUAS relativas a janela
  (pro editor word-level). Helpers `_phrase_bounds`/`_phrase_index_at`.
- **`processor.py`** (`run_thread2`): no INICIO da thread2 (logo apos `transcript`), busca o
  `word_timestamps` via `runpod_client.fetch_whisper_output(job["runpod_job_id"])` (o N8N ja PATCHa o
  runpod_job_id pra ca). RETENCAO do RunPod e CURTA (job de minutos vivo; de 2 dias da 404), por isso
  busca cedo. Best-effort: falha -> metodo por frase. Ao gravar, usa `words_for_clip` se ha words,
  senao `segments_for_clip`; e guarda `clip_words` por clipe.
- **`runpod_client.py`**: `fetch_whisper_output(runpod_job_id)` (GET /status no endpoint Whisper; output
  dict se COMPLETED, senao None; 404 -> None; nunca levanta).
- **`db.py`**: coluna aditiva `clip_captions.words` (JSON, relativo a janela) + get/set_clip_words.
- A QUEIMA nao mudou (`build_ass_from_segments` so recebe segmentos melhor cronometrados). Vale pra
  clipes NOVOS (antigos mantem o char-proporcional; backfill = reprocessar ou re-submeter o audio que
  persiste no S3). Validado: probe + `test_words_for_clip` + BURN real frame-a-frame. **Commit `8f4378c`
  (local, NAO pushado). Deployado (backup + restart opussb, active_jobs=0).**

## 27. Poster por clipe (rota /thumb) (2026-06-25)

Pro card do clipe nao ficar PRETO antes do play (a queima/legenda continua so no play).
- **`GET /api/clips/<id>/thumb`** (`app.py`): 302 pro `clip_NN_thumb.jpg` cacheado no S3; gera na 1a vez
  extraindo 1 FRAME da base com SEEK POR RANGE (`-ss <in_point+1> -i <presigned>`, NAO baixa o video
  todo, ~2-3s), sob semaforo PROPRIO `_thumb_sem` (`config.THUMB_CONCURRENCY=2`, `THUMB_QUEUE_TIMEOUT=20`)
  + lock por clipe + nice/threads. `_thumb_key_for` deriva a chave (cobre `_base.mp4` e clipe antigo).
  Best-effort: sem slot/falha -> 404 (card cai no fundo). `_clip_objects` passa `thumbnail_url` por clipe.
- e2e validado (1a ~3s gera+302+cache, 2a 0,12s). **Commit `cda4dcc` (local). Deployado.**

## 28. Waveform por clipe (rota /waveform) (2026-06-25)

A "camada de audio" do editor (era neutra por CORS do S3).
- **`GET /api/clips/<id>/waveform`** (`app.py`): devolve JSON `{peaks:[0..1]}` (NAO 302 -> tem que ser
  MESMA ORIGEM, senao o fetch CORS-bloqueia no S3 igual antes). Gera no servidor (`_compute_peaks`:
  ffmpeg -> PCM mono 8kHz -> 400 buckets via `array`), cacheia em `clip_captions.waveform`. Reusa
  `_thumb_sem` + nice. ~3,6s na 1a, instantaneo depois. Best-effort: falha -> `{peaks:[]}`.
- **`db.py`**: coluna `clip_captions.waveform` + get/set_clip_waveform.
- Front: `use-waveform.ts` busca essa rota; `clip-editor` chama `useWaveform(editable ? clipId : null)`.
- e2e validado (1a 4,2s, 2a 0,004s cache). **Commit `b1e7f07` (local). Deployado.**

## 29. Tela final / outro (CLUBCAST) — FEITO no codigo, deploy pendente de OK (2026-06-25)

Anexa uma tela final (.mp4 ~5s) ao fim do clipe. "Dividir" (cortar trechos do meio) DESCARTADO por ora.
Asset real (subido pelo dono): `/opt/opussb/video/TELA FINAL CLUBCAST.mp4` (4,59s, 1080x1920, h264
yuv420p, 24000/1001 fps, aac 48k), **seedado no S3 em `assets/end_screens/clubcast.mp4`** (binario NAO
commitado).

**DECISAO DO DONO (esta sessao):** escopo **POR PROJETO** (nao global): a marca Clubcast e do cliente
Carbone, e nao ha campo de cliente no modelo, entao default-on-global estamparia clipe de qualquer
cliente. Solucao: `jobs.end_screen` = tela padrao do projeto (NULL=nenhuma), os clipes herdam, com
override por clipe (`clip_captions.end_screen`: NULL=herda, "none"=removida, "<id>"=forcada). Default
global = NULL: nada muda em producao ate alguem ligar a tela num projeto. E entra em **TODO entregavel**
(player, "Baixar", "Baixar todos", "Exportar com" E "sem legenda").

**MECANICA (re-validada empiricamente esta sessao, ver scratchpad/validate_endscreen.sh):** concat-copy
CRU corrompe os timestamps com fps diferente (no teste, final 19,6s em vez de 12,6); concat FILTER
explode em frames duplicados sem normalizar fps (descartado). O caminho certo: **re-encodar SO os ~5s do
outro pra CASAR os params reais do clipe** (fps + `-video_track_timescale` + `-ar`/`-ac`, ~0,7s) e
**concat-copy do clipe longo** (sem re-encodar). Resta 1 warning de DTS no audio que o ffmpeg
auto-corrige; o final decodifica limpo (validado).

**FEITO (codigo, testes verdes, e2e real com cleanup):**
- `db.py`: `jobs.end_screen` (default do projeto) + `clip_captions.end_screen` (override do clipe,
  semantica de heranca) + get/set_clip_end_screen.
- `config.py`: `END_SCREENS` (biblioteca id->{key,label}; hoje so `clubcast`), `END_SCREEN_PREFIX`,
  `END_SCREEN_TIMEOUT`.
- `app.py`: `_resolve_end_screen_id` (override do clipe > default do projeto), `_endscreen_key`,
  `_probe_av_signature`, `_ensure_matched_outro` (casa o outro aos params do clipe, cacheia no S3 por
  assinatura em `assets/end_screens/_matched/<id>_<sig>.mp4`), `_append_end_screen` (concat-copy +
  faststart), `_produce_endscreen` (sob o `_burn_sem`, re-check do final, degrada honesto), `_serve_clip`
  (302 pro `<content>_es.mp4`, anexando a tela; falha -> serve content sem tela), `_ensure_burn`
  (extraido do `clip_video`: garante o content e SEMPRE libera o semaforo antes do anexo, evitando
  deadlock). `_invalidate_cap` agora inclui os finais `_*_es.mp4`; `_invalidate_endscreen` apaga so os
  finais (toggle nao re-queima a legenda). Rotas: `GET /api/end_screens`, `PUT /api/clips/<id>/end_screen`,
  `PUT /api/projects/<id>/end_screen`; `end_screen`/`end_screen_effective`/`project_end_screen`/`end_screens`
  no `GET /captions`; `end_screen` exposto no projeto.
- `test_backend.py`: `test_end_screen` (chave, heranca/override, invalidacao novo+antigo, todas as rotas).
- E2e real (job TEST, S3+ffmpeg, cleanup total): /video=cap_es dur=base+outro decode limpo + frame
  Clubcast no fim; /video?nocap=1=base_es; matched cacheado; toggle 'none' serve sem tela; herda de volta.

**FALTA:** so o **DEPLOY** (restart `opussb`), bloqueado ate o dono autorizar (backup ja feito em
`/opt/opussb_backups/*-20260625-183122.*`, active_jobs=0). Front correspondente FEITO (ver FRONTEND §23),
falta `next build` + restart `opussb-web` (junto). Nada commitado/pushado ainda (regra 9).

## 30. Estado atual e proximos passos (fim da sessao 2026-06-25) [SUPERSEDE §25]

Producao saudavel (`active_jobs=0`, opussb + opussb-web ativos, https://sbclips.space).

**DEPLOYADO nesta sessao** (tudo em `master` LOCAL): §26 legenda por palavra (`8f4378c`), §27 poster /thumb
+ projetos "Ver mais" + loading lazy (`cda4dcc`), §28 waveform (`b1e7f07`), e 2 fixes de front: painel para
de seguir o video ao editar (`9bae09e`), dialogo do clipe maior + pausa o card ao abrir (`891b7b3`).
Ver FRONTEND §21.

**GIT (IMPORTANTE):** `origin/master` em `9a9b01e` (3 commits de DOC, pushados no inicio da sessao).
`master` LOCAL esta **5 commits A FRENTE, SEM PUSH** (regra 9): `9bae09e`, `8f4378c`, `891b7b3`, `cda4dcc`,
`b1e7f07`. **Pushar quando o dono autorizar.** Ha tambem WIP NAO commitado no working tree: a **tela final
CLUBCAST inteira** (§29: `db.py`, `config.py`, `app.py`, `test_backend.py` + os 4 arquivos do front) + estes
docs. Tudo testado (`test_backend.py` verde) e validado por e2e real, **mas NAO deployado** (restart
bloqueado ate o dono autorizar).

**STAGING:** git worktree em `/opt/opussb-staging` (branch `staging`, em `8f4378c`, atras do master) pro
futuro editor OpusClip. Tem um `.env` copiado (gitignored). Rebaseline com `git -C /opt/opussb-staging merge master`
antes de comecar o editor.

**PROXIMOS PASSOS:**
1. **Tela final CLUBCAST (§29): FEITA no codigo + testes + e2e; falta so o DEPLOY** (autorizar o restart do
   `opussb` + `next build` + restart do `opussb-web`, juntos, com active_jobs=0; backup ja feito). Depois:
   ligar a tela final NOS PROJETOS DA CARBONE (seletor na pagina do projeto) e validar um clipe real
   (verificar a junção do audio num clipe vindo do reframe, nao so no base sintetico do e2e).
2. **Editor estilo OpusClip** (FRONTEND §22): texto corrido + karaoke por palavra + pilulas de pausa, como
   RE-SKIN (nao rewrite), em STAGING, com o `clip_words` que ja guardamos. Decisao fechada nesta sessao
   (painel multi-lente + probe). "Remover pausa/jump-cut" = v2.
3. **Push dos 5 commits + o commit da tela final** quando autorizado.
4. Pendencias antigas: `reprocess`, filmstrip real (mesma ideia sob demanda do /thumb e /waveform),
   ultralytics RCE, static/ legado.
