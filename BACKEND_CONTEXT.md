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

- **VPS**: Hostinger (Campinas/SP, Brasil, AS47583), 4 vCPU, 16GB RAM, **sem swap** (a Hetzner
  tinha 4GB de swap). IP público `187.127.43.75`. Migrada da Hetzner em 2026-07-01→02 (§40/§42).
  Disco único de 200GB; `/mnt/opussb-jobs` é PASTA no disco raiz (é o `JOB_DIR`, trabalho em
  disco), NÃO volume dedicado.
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
Linux; arquivo rastreado em `fonts/inter/Inter-Medium.ttf` no repo, licença SIL
OFL, instalado em `/usr/local/share/fonts/inter` pelo `setup_vps.sh`. **Histórico
2026-07-14**: por ficar fora do git de propósito, essa fonte sumiu na migração
Hetzner→Hostinger sem ninguém notar até a legenda queimada sair em DejaVu Sans
de fallback; passou a ser versionada exatamente pra não repetir isso), **tamanho
70**, **uma linha só** sempre, branco, **glow** escuro (halo, simula a sombra blur 155 do
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
  no hPanel (registro A `@` para `187.127.43.75` — VPS Hostinger, cutover 2026-07-01; `www` é
  CNAME pra raiz).
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
  da VPS (observado na Hetzner; a Hostinger também é datacenter) leva "confirme que não é um robô" de forma INTERMITENTE mesmo com cookies+Deno+PO token; a
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

**DEPLOYADO + PUSHADO (2026-06-25):** restart `opussb` + `next build` + restart `opussb-web` feitos
(active_jobs=0, backup). Front correspondente FEITO (ver FRONTEND §23). Commit `02821f9`, pushado.
Clubcast semeada no DB de producao. **Pendente so a validacao do dono num clipe real da Carbone.**

## 30. Estado atual e proximos passos (fim da sessao 2026-06-25) [SUPERSEDE §25]

Producao saudavel (`active_jobs=0`, opussb + opussb-web ativos, https://sbclips.space).

**DEPLOYADO + PUSHADO nesta sessao** (`origin/master` em `09c5d6e`): §26 legenda por palavra (`8f4378c`),
§27 poster /thumb + "Ver mais" + lazy (`cda4dcc`), §28 waveform (`b1e7f07`), 2 fixes de front (`9bae09e`,
`891b7b3`), §29 tela final por projeto (`02821f9`), §31.1 camada de video/filmstrip (`0e72eb8`), §31.2/31.3
biblioteca de telas finais + encurtar (`09c5d6e`). Ver FRONTEND §21/§23/§24.

**GIT:** `origin/master` == `master` local == `09c5d6e` (TUDO pushado). Working tree limpo, EXCETO `.gitignore`
(o dono adicionou `.env.example`; deixado pra ele commitar). Sem WIP pendente.

**STAGING:** git worktree em `/opt/opussb-staging` (branch `staging`, atras do master) pro futuro editor
OpusClip. Tem um `.env` copiado (gitignored). Rebaseline com `git -C /opt/opussb-staging merge master` antes
de comecar o editor.

**PROXIMOS PASSOS:**
1. **Validar a tela final num clipe REAL da Carbone** (unica pendencia das §29/§31): ligar a tela no projeto
   pelo seletor, abrir um clipe vindo do reframe e conferir a emenda (audio/video) no fim. O e2e usou base
   sintetico; falta a prova num clipe de producao.
2. **Editor estilo OpusClip** (FRONTEND §22): texto corrido + karaoke por palavra + pilulas de pausa, como
   RE-SKIN (nao rewrite), em STAGING, com o `clip_words` que ja guardamos. "Remover pausa/jump-cut" = v2.
3. Pendencias antigas: `reprocess`, ultralytics RCE, static/ legado.

## 31. Camada de video (filmstrip) + biblioteca DINAMICA de telas finais + encurtar (2026-06-25)

Continuacao da §29 (tela final), atendendo 3 pedidos do dono no editor.

### 31.1 Camada de VIDEO no editor (filmstrip server-side)
A trilha de video ficava vazia (capturar frames no navegador e bloqueado pelo CORS do S3, igual a
waveform era). Resolvido igual /thumb e /waveform: **`GET /api/clips/<id>/filmstrip`** gera um SPRITE de
N=12 frames lado a lado (ffmpeg `fps=N/dur,scale=80:-2,tile=Nx1`, presigned como input, niced + 1 thread,
semaforo `_thumb_sem`), cacheia no S3 (`clip_NN_strip.jpg`) e retorna `{url: presigned}`. O front estica o
sprite na largura da trilha (o nº exato de frames nao importa). `_strip_key_for`. Front: `use-filmstrip`
busca a rota (clipId). **Deployado + commit `0e72eb8` (pushado).** e2e: sprite 960x142, cache na 2a.

### 31.2 Biblioteca DINAMICA de telas finais (upload/trocar/excluir)
A tela final deixou de ser hardcoded no config e virou uma **tabela `end_screens`** (id, label, s3_key,
duration). O config.END_SCREENS agora so SEMEIA a clubcast no boot (`db.ensure_end_screen`); a fonte de
verdade e o DB. `_resolve_end_screen_id`/`_end_screen_options`/`_produce_endscreen` leem do DB.
- **`POST /api/end_screens`** (multipart `file`, opcional `label`): valida formato + duracao <=
  `END_SCREEN_MAX_SECONDS`(10) via ffprobe, **re-encoda/normaliza** (h264/aac/yuv420p) e sobe pro S3
  (`assets/end_screens/<uuid>.mp4`), grava no DB. Rejeita >10s (400). `END_SCREEN_MAX_BYTES`(60MB).
- **`DELETE /api/end_screens/<id>`**: apaga do DB + asset no S3 + os `_matched/<id>_*` cacheados. Clipes
  que referenciavam degradam pra "sem tela" (o resolve ignora id inexistente).
- O asset NAO precisa ser 1080x1920: o `_ensure_matched_outro` agora **escala+pad** pra 1080x1920
  (`force_original_aspect_ratio=decrease` + `pad`), entao upload de qualquer resolucao casa no concat-copy.

### 31.3 Encurtar a tela final por clipe (trim, arrastar na timeline)
Coluna `clip_captions.end_screen_dur` (REAL, NULL=tela inteira). `_ensure_matched_outro` recebe `dur` e
corta o outro nos primeiros N s (`-t`), com a duracao na assinatura do cache do matched. O
`PUT /api/clips/<id>/end_screen` aceita `dur` (clampado a [0,5s .. duracao do asset/teto]); `GET /captions`
expoe `end_screen_dur` + a `duration` de cada opcao. Front: alca arrastavel na 4a trilha.

**Validacao:** `test_end_screen` ampliado (biblioteca via DB, dur/clamp, DELETE) + 3 e2e reais (render
sem-trim, trim=2s -> final=base+2, upload 720x1280 -> render final 1080x1920 decode limpo). `tsc` limpo.
**STATUS: TUDO DEPLOYADO + PUSHADO** (31.1 em `0e72eb8`; 31.2/31.3 + front em `09c5d6e`). So falta a
validacao do dono num clipe real da Carbone.

## 32. Painel de saldos (creditos) no admin (2026-06-25)

Pedido do dono: admins acompanharem os saldos de credito (RunPod e Claude) pelo painel.
- **`GET /api/admin/credits`** (`app.py`, `@admin_required` = sessao admin, NAO e a chave): saldo da
  RunPod via GraphQL (`https://api.runpod.io/graphql`, query `myself { clientBalance currentSpendPerHr }`,
  Bearer RUNPOD_API_KEY, VALIDADO por probe real). Cache em memoria de 5 min (`_credits_cache`/lock),
  `?refresh=1` forca. Best-effort: falha -> campo null, nao quebra. Resposta:
  `{runpod:{balance,spend_per_hr}|null, anthropic:{configured:bool}, cached:bool}`.
- **Claude/Anthropic:** so o saldo APARECE no Console; nao ha endpoint publico garantido pro saldo
  prepago. O relatorio de CUSTO precisa da Admin API key (`sk-ant-admin...`, != ANTHROPIC_API_KEY).
  `config.ANTHROPIC_ADMIN_KEY` (opcional, vazio hoje) ja existe; quando o dono gerar a key, integrar o
  cost report (`/v1/organizations/cost_report`) e VALIDAR com dado real (nao deixei codigo nao testado).
- Front: aba "Saldos" no /painel (FRONTEND §25). Decisao de custo (sessao): cache de prompt NAO ajuda o
  scoring (transcricao muda sempre + vidoes espacados); o custo pesado e a RunPod, nao a Claude (Haiku,
  ~US$0,03/video). Saldos no momento: RunPod ~US$8,58, Claude ~US$2,35 (baixo, recarregar).
- e2e: gate 401/403/200 + saldo real + cache. **DEPLOYADO; commit ainda nesta leva (ver git).**

## 33. Crop por KEYFRAME + multi-apresentador: DECISOES + arquitetura (2026-06-26)

Editor de enquadramento manual estilo OpusClip. REFINA o roadmap "Fase 1" do §24 e a "Fase 3" do
FRONTEND §20. **Status: SO DECISOES + arquitetura acordada. NADA implementado. Tudo em STAGING
(worktree /opt/opussb-staging), decisao do dono.** O dono mostrou prints do OpusClip (caixa 9:16
arrastavel sobre o 16:9, "Rastreador: DESATIVADO", "Ativar layout" multi-apresentador).

**DECISOES DO DONO (2026-06-26):**
1. Reter o corte **16:9 por clipe** no S3 por **30 dias** (custo de storage OK; ~100GB em regime, §24).
2. Crop **SO horizontal** (pan). Sem zoom/scale.
3. **Multi-apresentador INCLUIDO** (split / 3 / 4) no escopo (mas e mais pesado -> fase posterior).
4. **Tudo em STAGING** primeiro.
5. Modelo: **keyframe = primitivo unico**; toggle Auto/Manual por clipe; manual SEMEADO do auto.

**ARQUITETURA ACORDADA:**
- O corte **16:9 vira a fonte de verdade**; o `clip_NN_base.mp4` (9:16) passa a ser um RENDER DERIVADO
  dela. O 16:9 JA e produzido hoje; no caminho GPU JA sobe pro S3 (e deletado no fim) -> reter = parar
  de deletar + TTL 30d. (No caminho CPU e local; precisaria subir.)
- O **auto-reframe (reframe_core) vira a TRAJETORIA BASE**, persistida (cx por frame / por segmento).
- **Keyframe = primitivo unico.** Auto = keyframes gerados pela maquina (semeados nos cortes de camera
  §24, com keyframe em "DEGRAU" no corte pra PULAR em vez de varrer a cena). Manual = keyframes do
  usuario, **semeados a partir do resultado do auto** (ai "corrige so onde erra"). **Toggle Auto/Manual
  por clipe** (igual "Rastreador: DESATIVADO" do OpusClip); NAO rodar os dois juntos.
- **Aplicar = RE-RENDER via reframe_core** (CPU + GPU), substitui o base 9:16. **NAO** crop variavel na
  queima (seria caro/estampida, §19.1). Legenda queima por cima como hoje. **Compoe com handles/trim
  §18** (mesma janela) e a tela final §29.
- **Preview no editor = caixa 9:16 arrastavel sobre o video 16:9, recorte via CSS** (instantaneo, sem
  re-render). Render so no "Aplicar".

**A FAZER (fazer um design pass + implementar em fases pequenas/reversiveis, STAGING):**
- Data model: persistir trajetoria/keyframes (provavel coluna nova em `clip_captions`, JSON tipo
  `{mode:"auto|manual", keyframes:[{t,cx,hold?}], layout}`) + key S3 do 16:9 retido + reter no pipeline.
- reframe_core: aceitar uma **trajetoria custom** (cx-por-frame interpolado dos keyframes) em vez de so a
  auto; passar isso pro **GPU submit** (gpu_reframe/handler.py + runpod_client) e pro CPU.
- Rota pra servir o **16:9** ao editor (302 pro S3, tipo `/src169`) + rotas pra ler/gravar keyframes +
  disparar o re-render daquele clipe.
- Multi-apresentador: compor 2+ recortes em split/3/4 (fase POSTERIOR; mais pesado).
- Front: editor com preview 16:9 + caixa arrastavel (so horizontal) + trilha de keyframes + toggle layout.

**RISCOS A VALIDAR no design (empiricamente, nao assumir):** frame-accuracy da trajetoria vs a janela e
os handles §18; passagem da trajetoria pra GPU; custo/estampida do multi-apresentador (varios recortes);
mecanica do TTL 30d no S3; isolamento do staging (DB/S3/unit proprios). Memoria: `reframe-void-fix-and-crop-editor`.

## 34. Estado atual e proximos passos (fim da sessao 2026-06-26) [SUPERSEDE §30]

Producao saudavel (`active_jobs=0`, opussb + opussb-web ativos, https://sbclips.space).

**DEPLOYADO + PUSHADO nesta sessao** (`origin/master` em `62b7be4`):
- §29 tela final CLUBCAST por projeto (`02821f9`); §31.1 camada de video/filmstrip (`0e72eb8`);
  §31.2/31.3 biblioteca de telas finais (upload/trocar/excluir) + encurtar arrastando (`09c5d6e`);
  §32 painel de saldos (RunPod) no admin (`62b7be4`). Front correspondente: FRONTEND §23/§24/§25.

**GIT:** `origin/master` == local == `62b7be4` (tudo pushado). Working tree: so docs (estes) +
`.gitignore` (o dono adicionou `.env.example`, deixar pra ele). Sem WIP de codigo.

**SALDOS (do painel novo):** RunPod ~US$8,58 (custo pesado, ok). Claude ~US$2,27 (BAIXO, recarregar pra o
scoring nao parar). Decisao: cache de prompt NAO ajuda o scoring; custo pesado e a RunPod, nao a Claude.

**ANTHROPIC ADMIN KEY (custo da Claude no painel): PARADO.** A conta e "Klenio Braz's Individual Org" =
org INDIVIDUAL, e a Admin API **nao existe** pra org individual (a pagina /settings/admin-keys da 404).
Pra liberar, teria que "Converter para organizacao Team" (Settings > Organization) -> decisao de
negocio/billing do dono, nao vale so pelo numero de custo (que e minusculo). `config.ANTHROPIC_ADMIN_KEY`
ja existe vazio; quando virar Team, e so setar no .env que eu ligo o cost report (validando com dado real).

**PROXIMOS PASSOS:**
1. **Validar a tela final num clipe REAL da Carbone** (§29/§31): ligar a tela no projeto, abrir um clipe
   vindo do reframe, conferir a emenda (audio/video). O e2e usou base sintetico.
2. **Editor de crop por KEYFRAME + multi-apresentador (§33): proxima grande frente.** So decisoes hoje;
   proximo passo = design pass (mapear o stack de reframe/render + projetar fases) e implementar em STAGING.
3. **Editor estilo OpusClip texto-corrido** (FRONTEND §22, staging).
4. Pendencias antigas: `reprocess`, ultralytics RCE, static/ legado.

## 35. Crop por KEYFRAME: IMPLEMENTADO (Fase 0-2b) (2026-06-30) [IMPLEMENTA §33]

Editor de enquadramento manual estilo OpusClip, NAO-destrutivo, por keyframes. Implementa o plano da §33.
Feito 100% em STAGING (worktree, branch `staging`), validado empiricamente, depois mesclado pra prod (§36).
Memoria: `reframe-void-fix-and-crop-editor`, `gpu-image-build-disk`.

**Data model (db.py):** coluna nova `clip_captions.keyframes` TEXT (via `_ensure_column`, aditivo). JSON
`{mode:"auto"|"manual", kf:[{t, cx}], auto?:[...]}` onde `t`=segundos (relativo a janela do clipe) e
`cx`=CENTRO horizontal em FRACAO 0..1 da largura (resolucao-independente). `get_clip_keyframes`/
`set_clip_keyframes` (upsert). O campo `auto` preserva a trajetoria da maquina pro toggle reverter.

**16:9 retido (fonte de verdade):** o corte 16:9 que vira o `clip_NN_base.mp4` passa a ser RETIDO em
`source16x9/{job_id}/clip_NN.mp4` (prefixo dedicado, 1-based; `app._src169_key_for`). Caminho GPU sobe
direto pra essa key (flag `item["src169_retained"]`, zero upload extra) e NAO deleta mais; caminho CPU sobe
no loop do `run_thread2`. TTL de 30d via lifecycle do bucket (ver config). `_cleanup_job_s3` limpa o
prefixo no cancel/`_fail_job`; o delete de projeto tambem.

**reframe_core.py (matematica pura, CPU+GPU):**
- `reframe_to_file(...)` agora DEVOLVE a trajetoria `{per_frame_cx, fps, width, crop_w, cuts}` (ou None se
  ja-vertical). `per_frame_cx` = BORDA ESQUERDA do recorte em px por frame.
- `keyframes_from_trajectory(per_frame_cx, fps, width, crop_w, cuts, interval_s)` -> reduz a trajetoria a
  keyframes esparsos `{t, cx-fracao-centro}` (amostra a cada `interval_s` + ancora nos cortes de camera =
  "degrau"). E o AUTO-SEED.
- `trajectory_from_keyframes(kf, fps, total_frames, width, crop_w)` -> inverso: expande pra `per_frame_cx`
  denso (1/frame), interpolacao LINEAR + clamp em [0, width-crop_w] (pan-only).
- `reframe_with_keyframes(input, output, kf, codec, subtitles)` -> render 9:16 SEM YOLO (abre o 16:9 so pra
  dims/fps, interpola, chama `_pipe_encode`).
- `_pipe_encode`: no broken-pipe agora ANEXA o tail do stderr do ffmpeg (a causa real - args/NVENC/driver -
  se perdia).
- `reframer.py` (CPU): `reframe_clip` propaga a trajetoria; `reframe_clip_with_keyframes` (casca sem YOLO).

**processor.py:** `_reframe_clips_gpu` retem o 16:9, passa `keyframe_interval_s` nos params e COLHE
`out["keyframes"]` do worker -> `produced[i]["keyframes_json"]`; `_reframe_clips` (CPU) semeia do `traj`;
`run_thread2` grava via `set_clip_keyframes`. Worker velho sem o campo -> sem seed (gracioso).

**Worker GPU (gpu_reframe/handler.py), ADITIVO/retrocompativel:** dois modos. AUTO (default) reframa com
YOLO e DEVOLVE os keyframes esparsos no output (`{"keyframes":{mode,kf}}`) pro auto-seed. RE-RENDER (input
traz `keyframes`) roda `reframe_with_keyframes` sem YOLO. **Retry com backoff** no `_download`/`_upload`
(5xx/429/rede): o Hetzner Object Storage devolve 502/504 transitorios (~10% nos smoke) que derrubavam o
reframe pro fallback CPU; agora reabsorvidos. Reporta o nome da GPU no output (diagnostico).
`runpod_client.submit_rerender` (keyframes no input, key temporaria); `submit_reframe` carrega o intervalo.

**Rotas (app.py):**
- `GET /api/clips/<id>/src169` -> 302 pro 16:9 retido (404 se ausente = clipe legado).
- `GET/PUT /api/clips/<id>/keyframes` -> le/grava `{mode,kf,auto}` + estado do re-render. PUT valida
  (`_validate_keyframes`: t>=0, cx clampado [0,1], ordena por t), aceita `{reset:true}` (volta ao auto
  semeado), dispara `_do_rerender` em thread daemon, responde 202.
- `GET /captions` expoe `keyframes` + `crop_editable` (= `_base.mp4 in base_key` AND 16:9 retido existe;
  INDEPENDENTE de ter legenda).
- **`_do_rerender`** (sob `_rerender_sem`, 1 por vez): LE os keyframes ATUAIS do DB (PUTs concorrentes
  convergem pro ultimo), produz a base por GPU (`_rerender_gpu`: worker escreve key TEMPORARIA, app baixa e
  promove) ou CPU (fallback sempre ativo); a promocao pra base + invalidacao de derivados (cap/trim/es +
  thumb/strip) roda sob **`_clip_burn_lock`** (atomico contra a queima). NAO mexe na janela/segmentos: so
  muda o pan, a legenda segue valida.

**config.py (knobs novos):** `S3_KEY_PREFIX` (isola staging na borda do `s3_client._k()`; "" em prod = zero
efeito); `SOURCE16X9_PREFIX`/`SOURCE16X9_TTL_DAYS`(30)/`SOURCE16X9_LIFECYCLE_ENSURE`(default off, liga o
ensure no boot); `KEYFRAME_SEED_INTERVAL_S`(0.75); `RERENDER_GPU_ENABLED` (re-render na GPU, default FALSE,
exige `REFRAME_GPU_ENABLED` - trava de deploy: app novo + worker velho daria reframe ERRADO no re-render).
`s3_client.ensure_lifecycle_rule` (idempotente, merge por rule_id namespaceado por ambiente).

**Validado EMPIRICAMENTE no metal (endpoint RunPod real, sem clipe de teste no app):** reframe + auto-seed
confiavel em RTX 4090 e A5000; as falhas intermitentes ("ffmpeg morreu cedo / broken pipe") eram **502/504
transitorios do Hetzner no DOWNLOAD** do input pelo worker (NAO NVENC, NAO tipo de GPU) - PRE-EXISTENTES -
agora absorvidos pelo retry (smoke 4/4 limpo no 2b2). Lifecycle do Hetzner honra o TTL (head_object devolve
x-amz-expiration). Multi-apresentador (split/3/4) segue como fase POSTERIOR (nao implementado).

**Imagem do worker:** rebuild via **BuildKit descartavel com estado no volume sdb** (`/mnt/opussb-jobs`); o
disco de sistema (sda, 38G) NAO comporta a CUDA ~11G e NAO da pra mover o data-root do Docker ao vivo (mata
o n8n de prod). Ver `gpu-image-build-disk`. `--provenance=false` (manifest single-arch). RunPod serverless
NAO atualiza worker existente num push de `:latest` -> usar **tag NOVA** por release (`:2b1`, `:2b2`) e
"New release" no painel.

## 36. Estado atual e proximos passos (fim da sessao 2026-06-30) [SUPERSEDE §34]

Producao saudavel (`active_jobs=0`, opussb + opussb-web ativos, https://sbclips.space).

**DEPLOYADO + PUSHADO nesta sessao** (`origin/master` em `7eff531`):
- **§35 crop por keyframe (Fase 0-2b)** LIVE em prod: editor de enquadramento, auto-seed (CPU+GPU), 16:9
  retido 30d, re-render GPU/fallback CPU. Front correspondente: FRONTEND §26.
- **Excluir usuario no painel admin** (commit `086bb5b`, era trabalho do dono nao-commitado no working tree
  de prod, subiu junto): `POST /api/admin/delete` (`admin_required`): so exclui conta `rejected`/`inactive`
  (regra reforcada no servidor), nunca a si mesmo/ativo/pendente; `db.delete_user` limpa dados pessoais
  (sessions/favorites/preferences), preservando jobs/projetos (biblioteca compartilhada por `owner_email`).
- Worker GPU publicado em `kleniobraz/opussb-reframe:2b2` + `:latest` (dono fez o New release com `:2b2`).

**.env de prod (Fase 6):** `SOURCE16X9_LIFECYCLE_ENSURE=true` (regra `opussb-source16x9-ttl-prod` Enabled no
bucket, prefix `source16x9/`, 30d - CONFIRMADO) + `RERENDER_GPU_ENABLED=true`. Backups de rollback na VPS:
`opussb.db.bak-pre-fase6-20260630` (+copia em /mnt/opussb-jobs), `.env.bak-pre-fase6`. Rollback do codigo:
`git reset` do master pro `f6e953f` + rebuild front + restart (worker e retrocompativel).

**GIT:** `origin/master` == local == `7eff531`. Merge de `staging` (Fase 0-2b) sobre o `086bb5b` (admin).
A branch `staging` e o worktree `/opt/opussb-staging` seguem existindo (uteis pra proxima feature).

**Cosmetico (PRE-EXISTENTE, nao e do deploy):** `Control server error: Permission denied /var/www/.gunicorn`
no log do boot do gunicorn (47 ocorrencias historicas); worker boota e atende normal.

**PROXIMOS PASSOS:**
1. **Validacao visual do crop num PODCAST real** (dono vai subir): o 1o upload pos-deploy retem o 16:9 +
   auto-semeia; conferir o toggle Enquadramento, arrastar a caixa, keyframes e o "Aplicar" (re-render GPU).
2. **Multi-apresentador (split/3/4)** - fase posterior do §33, nao implementado.
3. **Hardening residual** (Fase 5, §35): status do re-render sem epoch (pode piscar 'done' cedo com PUTs
   concorrentes; o entregavel converge); TOCTOU menor no fast-path do `_ensure_burn`.
4. Validar a tela final num clipe REAL (§34 #1, ainda pendente); editor texto-corrido (FRONTEND §22).
5. Pendencias antigas: `reprocess`, ultralytics RCE, static/ legado.

## 37. Crop: custo/perf MEDIDO + opcoes de otimizacao (DECIDIDAS PENDENTES) (2026-06-30)

Apos o deploy (§35/§36), o dono subiu um podcast real e questionou o custo de RunPod. Medicoes reais no
metal (executionTime via API da RunPod), NADA otimizado ainda (decisao do dono: medir primeiro). Os 2
pontos de codigo abaixo foram verificados por workflow (analise + refutacao adversarial, 0 refutado).

**Re-render do "Aplicar" = 1 job de GPU por clipe, INDEPENDENTE do nº de keyframes.** Cadeia: `_do_rerender`
-> `_rerender_gpu` -> `submit_rerender` (1 POST /run com a lista inteira) -> worker `reframe_with_keyframes`
-> `trajectory_from_keyframes` EXPANDE os N keyframes esparsos numa trajetoria DENSA por-frame -> 1
`_pipe_encode` (1 ffmpeg, loop por FRAME nao por keyframe). 5 trocas de camera = 1 job. Cada "Aplicar"
salvo (PUT /keyframes) = 1 render (por SALVAMENTO, nao por keyframe); `_rerender_sem` serializa.

**Re-render NAO invoca YOLO** (`reframe_with_keyframes` nao recebe `model`; YOLO so em `reframe_to_file`/
`detect_people`). Ressalva de custo: o worker CARREGA o YOLO no import (handler.py:46) mesmo num re-render,
entao um worker FRIO paga esse load no startup (nao no compute).

**Custo medido:** re-render ~**1 a 2 centavos/clipe** (clip7=98s ~US$0,016; clip12=68s ~US$0,011 em A5000
@ ~US$0,00016/s). Whisper de 1 podcast ~119s ~US$0,03. **O GROSSO e o reframe INICIAL de TODOS os clipes no
upload** (~US$0,21 pra 16 clipes ~ US$0,013/clipe); com 6-7 podcasts/dia ~ **US$30-45/mes** de RunPod. O
custo NAO esta no re-render (centavos) e sim no reframe inicial por upload.

**Perf:** o "Aplicando" leva ~90s wall-clock = cold start (~18s, worker escala a 0) + ~68s de compute. O
compute e dominado pelo **loop Python/cv2 quadro-a-quadro** do `_pipe_encode` (decodifica+recorta cada
frame), NAO pelo NVENC (segundos) -> **a GPU quase nao acelera o re-render vs CPU**.

**Como medir:** `journalctl -u opussb | grep -i rerender` (linhas `[rerender-gpu] submetido job <id>` +
`[rerender] clip ... ok (gpu|cpu)`); pegar o `<id>` e consultar `executionTime` em
`/v2/<endpoint>/status/<id>`; custo EXATO no dashboard RunPod (aba Requests). Re-render NAO emite `[timing]`
(so `[rerender]`) - se quiser timing ao vivo no log, falta 1 linha de instrumentacao no `_do_rerender`.

**OPCOES DE OTIMIZACAO (identificadas, DECIDIDAS PENDENTES - dono adiou pra depois de medir):**
1. **Re-render na CPU** (`RERENDER_GPU_ENABLED=false`): custo US$0 (VPS fixa), tempo ~igual (gargalo e o cv2
   loop, nao o codec). Lever de graça pros ajustes. (Hoje em prod esta `true` = GPU.)
2. **Reframe sob demanda (lazy):** so reenquadrar os clipes que o usuario ABRIR/exportar, nao os N no upload.
   Corta ~60-70% do custo recorrente. Mudanca media (miniatura sai do 16:9, nao quebra a grade).
3. **Render so na exportacao (metadado):** "Aplicar" so grava keyframes (instantaneo, US$0); o 9:16 final
   renderiza 1x no export (como a legenda). Mata o "Aplicando" E o custo-por-ajuste. Refatoracao maior.
- VPS com GPU propria: REJEITADO (US$180-360/mes fixos >> US$30-45 RunPod no volume atual).

**Upload (investigado, sem mudanca):** o video sobe ATRAVES da VPS (`POST /api/upload` -> `request.files` ->
disco `JOB_DIR`), nao direto pro S3. A VPS NAO e gargalo: nginx faz streaming
(`proxy_request_buffering off`, limite 15G, timeout 1h); disco da Hostinger medido em **1,8 GB/s** de
escrita direta (2026-07-02; a "entrada 3-5 Gbit/s" e o "disco 177 MB/s" eram medicoes da VPS Hetzner).
O limite e o UPLOAD da internet do dono + a distancia. VPS agora = **Hostinger Campinas/SP (Brasil)** —
otima pro usuario BR; o lado longe passou a ser o S3 `fsn1` (Alemanha): RTT VPS->S3 ~183ms medido em
2026-07-02 (ver §42).

## 38. Editor timeline-first + layout Premiere: toques de backend (2026-06-30) [front em FRONTEND §28]

Redesign do editor de clipe estilo OpusClip (roteiro corrido + timeline + layout Premiere), feito em
STAGING (worktree `/opt/opussb-staging`) por partes e DEPLOYADO em prod. O backend foi tocado SO em 3 pontos,
todos ADITIVOS e seguros (sem mudar schema; contrato de save intocado):

- **`words` (clip_words) exposto no `GET /api/clips/<id>/captions`** (`app.py`, `clip_captions_get`): o tempo
  por PALAVRA que a §26 ja gravava em `clip_captions.words` (relativo a janela) passa a sair na resposta
  (`"words": [{start,end,word}] | null`, parse igual ao de `keyframes`). **SOMENTE LEITURA**: o `PUT /captions`
  segue aceitando so `{start,end,text}` (via `_validate_segments`); o save NAO mexe em words. null = clipe
  legado/sem fala. Destrava o karaoke por palavra no front (o consumo VISUAL e a Parte 5, ainda pendente; o
  DADO ja flui). Confirmado em prod: 39/48 clipes ja tinham `words` gravado.
- **Rota nova `GET /api/clips/<id>/music/audio`** (`app.py`, `clip_music_audio`, `@login_required`): 302 pra
  URL presignada do `music_key` do clipe (404 se nao tem musica). So pra o preview AO VIVO do editor tocar a
  musica em paralelo ao video base, via um `<audio>` no front (sem re-queimar). Era por isso que parecia que
  "a musica nunca funcionou": o preview tocava o `/base` (sem musica); a musica so existia no video QUEIMADO.
- **Musica mantida no "Exportar sem legenda" (`?nocap=1`)** (`app.py`, `clip_video`): o branch do nocap deixou
  de descartar a musica (era `do_caps = do_music = False`); agora `do_caps=False; do_music=use_music`, entao o
  `_trim.mp4` queima COM musica (e trim, se houver). So serve a base crua quando NAO ha trim NEM musica.
  `_invalidate_cap` ja apaga `_trim.mp4` quando a musica muda, entao a regeneracao e automatica.

Sem mudanca de schema (`clip_captions.words` ja existia desde a §26). `_validate_segments`, `set_clip_captions`
e `_build_burn_cmd`/`_ensure_burn` (que ja mixava a musica via `-stream_loop -1` + `[1:a]volume[m]` +
`amix`) inalterados. Validado no staging (upload de musica 200, `/music/audio` 302, `/captions` com `words`).

**Deploy:** merge ff `staging -> master` (`964d265 -> 4c87293`, 11 commits), `next build` + restart
`opussb`/`opussb-web` com `active_jobs=0` + backup (`/opt/opussb_backups/*-20260630-211141*`; rollback ref =
`964d265`; banco NAO precisa de rollback, sem mudanca de schema). Push autorizado pelo dono (regra 9).
**GIT: `origin/master` == `4c87293`** [SUPERSEDE o git da §36]. Smoke server-side ok (/health, paginas, rota
nova 401). Validacao visual (gaveta Ajustes, musica no preview, crop) com o dono. Front: FRONTEND §28.

## 39. Pipeline paralelo de Podcast completo pra YouTube (16:9): DESIGN aprovado + Parte 1 em validacao (2026-07-01)

Feature NOVA, pipeline PARALELO ao de clipes 9:16 (nao mexe nele). Editar podcast inteiro pra YouTube: o dono
sobe um podcast 16:9 de 1-2h e escolhe uma vinheta; o sistema monta GANCHO (1-3 min do melhor momento) +
VINHETA + PODCAST COMPLETO e exporta 16:9 1920x1080, com o **fps NATIVO do arquivo** (NAO forcar 24 por fora;
ver Parte 1, o arquivo de teste real e 30fps), SEM legenda, SEM reframe (ja e horizontal). Design revisado e
aprovado pelo dono; implementacao NAO comecada (so a Parte 1, validacao isolada do concat, em andamento).

**Fluxo COM gate de aprovacao humana (pra nao montar gancho errado):** upload + escolhe vinheta -> transcreve
(RunPod, igual hoje) -> Claude sugere 3 OPCOES de gancho (inicio/fim + razao) -> o dono ve as 3, escolhe 1 e
AJUSTA os tempos (reusa o editor de trim, §18) -> SO DEPOIS o sistema monta e exporta.

**Decisao arquitetural central (a PAUSA):** o gate humano colide com 3 premissas do pipeline atual, e e o ponto
mais delicado. (1) `MAX_CONCURRENT_JOBS=1` travaria a fila com um job parado por horas; (2) `reap_stale_jobs`
(db.py:824) mata job sem update ha >30min; (3) o cleanup (`rmtree` no fim do `run_thread2`) apagaria o fonte.
SOLUCAO: quebrar em 2 fases com pausa persistente. Fase A (automatica, e "active job"): upload -> transcricao
-> Claude sugere 3 ganchos -> grava e vai pro estado novo `aguardando_aprovacao`. PAUSA: esse estado e
QUASE-TERMINAL (sai do `count_active_jobs`, liberando a fila, e e IGNORADO pelo `reap_stale_jobs`, nao morre);
o fonte 16:9 fica RETIDO local em `JOB_DIR` (sem subir GBs cross-region) com TTL de expiracao (ex: reaper limpa
`aguardando_aprovacao` com >14d). Fase B (automatica, disparada pela aprovacao): vira active job de novo, monta,
exporta, `done`.

**Bifurcacao minima:** coluna aditiva `jobs.job_type` (default `'clips'`, novo `'podcast'`); `run_thread2` e o
callback bifurcam por ela; o caminho `'clips'` fica byte-identico ao de hoje. Estados novos:
`analisando_gancho` -> `aguardando_aprovacao` (a pausa) -> `montando` -> `done`.

**Onde o Claude analisa 2h (DECISAO PENDENTE do dono, Parte 0):** hoje o Claude roda 100% no N8N (o Flask NAO
chama a Anthropic; `ANTHROPIC_API_KEY` existe em config.py:28 mas nunca e usado). Opcoes: (A, RECOMENDADA) Flask
chama a Anthropic direto (modulo novo `hooks.py`), pra nao tocar o workflow N8N de producao (regra 3) e porque a
logica de dividir/rankear 2h e trivial em Python; (B) branch no N8N por `mode=podcast`. Estrategia de tokens: 2h
de fala ~18 mil palavras, SRT cru ~60-70k tokens, CABE numa chamada (contexto 200k), entao o risco nao e
estourar contexto e sim QUALIDADE (o modelo se perde no meio) e um pouco de custo. Duas abordagens: chamada
unica compacta (`[mm:ss] texto` em blocos de ~30s, ~30k tokens, ~US$0,02 Haiku) OU map-reduce (~8 blocos de
15min, cada um propoe 2 candidatos, um passo final escolhe os 3, ~US$0,03-0,05, cobertura melhor). Sugestao:
comecar pela chamada unica (Parte 3, valida o fluxo) e evoluir pra map-reduce se a qualidade nao agradar; o gate
humano e a rede de seguranca. E analise NOVA (nao o scoring de clipes, nao fere a regra 6).

**Concatenacao 16:9 (o nucleo tecnico, a Parte 1):** a tecnica do §29 (tela final) VALE pra colar na FRENTE (e
agnostica a posicao): re-encoda so os pedacos pequenos casando os params + `concat-copy` do grande. CORRECAO
necessaria: `_ensure_matched_outro` (app.py:1380) tem `scale=1080:1920` (9:16) FIXO; este pipeline precisa de
uma funcao IRMA que escale pra 1920x1080. Mecanica: `ffprobe` no PODCAST define os params de verdade (reusa
`_probe_av_signature`, app.py:1352, estendido pra pegar tambem profile/level/SAR); o GANCHO (recorte do proprio
podcast) e re-encodado casando esses params + 1920x1080 (re-encode e preciso; 1-3 min de video, ~1-3 min de
CPU); a VINHETA idem (upload arbitrario, sempre normaliza; cacheia por assinatura como em `_matched/`);
`concat-copy` da lista `[gancho, vinheta, podcast]` (`-f concat -safe 0 -c copy -movflags +faststart`). O
PODCAST de 2h NUNCA e re-encodado. `snap_clip_bounds` (captions.py:81) reusa direto pro gancho comecar/terminar
em fronteira de fala.

**Vinheta:** biblioteca dinamica no DB espelhando as telas finais (§31.2, que e a tabela `end_screens` semeada
do config no boot): tabela nova `vinhetas` (id, label, s3_key, duration, created_at), rotas `GET/POST/DELETE
/api/vinhetas`, escolha POR PROJETO (`jobs.vinheta_id`), assets em `assets/vinhetas/<uuid>.mp4` no S3.

**Custo/tempo por podcast:** RunPod Whisper 2h ~US$0,05-0,10 (~3-8min); Claude gancho ~US$0,02-0,05; montagem
ffmpeg na VPS (SEM GPU) ~2-5min de CPU, US$0. Total incremental ~**US$0,10-0,15/podcast**. MAIS BARATO que o de
clipes (sem GPU de reframe, sem queima de legenda). Gargalo real = tempo humano de aprovar.

**Arquivos que mudam/nascem (previsto):** `db.py` (`jobs.job_type`, `jobs.hooks_json`, `jobs.hook_choice`,
`jobs.vinheta_id`; tabela `vinhetas` + CRUD; ajuste de `count_active_jobs`/`reap_stale_jobs` pra a pausa),
`config.py` (`VINHETA_*`, `HOOK_*`, `PODCAST_ENABLED`, `HOOK_APPROVAL_TTL_DAYS`), `app.py` (rotas
`/api/podcast/upload`, `/api/podcast/<job>/{hooks,hook,approve}`, CRUD `/api/vinhetas`, `_ensure_matched_1080p` +
`_compose_podcast`), `processor.py` (bifurcacao por `job_type`, analise pos-callback, `run_podcast_compose`),
NOVO `hooks.py` (cliente Anthropic + chunking). Front em FRONTEND §29.

**Partes (executar e validar por etapa, como o editor):** Parte 0 = decisoes (Claude Flask vs N8N, tokens);
**Parte 1 = validar ISOLADO a concat 16:9** (em andamento, abaixo); Parte 2 = `_ensure_matched_1080p` +
`_compose_podcast` plugados; Parte 3 = pipeline de analise (upload -> Whisper -> 3 ganchos -> pausa); Parte 4 =
aprovacao + montagem; Parte 5 = frontend.

**Riscos:** (1) a pausa humana vs fila/reaper/cleanup (o maior, mitigado pela seção da PAUSA); (2) `concat-copy`
exige params IDENTICOS (pix_fmt, profile, level, SAR, timebase de audio; mismatch quebra ou dessincroniza);
(3) VFR no podcast (concat-copy sofre deriva de PTS; validar cedo, NAO se aplica ao arquivo de teste que e CFR);
(4) qualidade do gancho (mitigado por map-reduce + gate humano); (5) storage do fonte retido na pausa (mitigado
por TTL); (6) timeout do RunPod pra audio de 2h; (7) priming/padding do AAC nas EMENDAS (micro-gap ao concatenar
AAC com -c copy; se dessincronizar, opcao e re-encodar SO o audio do conjunto, bem mais barato que o video).

**PARTE 1 (validacao isolada do concat) EM ANDAMENTO (2026-07-01):** plano aprovado. Feito ate aqui = baixado o
podcast REAL de teste do dono (Google Drive via `gdown` num venv isolado no volume) pra
`/mnt/opussb-jobs/_podcast_concat_test/podcast_teste.mp4` (11.203.661.228 bytes = 11GB, integro: ftyp mp42, sem
.part). **ffprobe do arquivo real:** video h264 **profile High, level 4.2**, **1920x1080**, **SAR 1:1**, DAR
16:9, **pix_fmt yuv420p**, **r_frame_rate == avg_frame_rate == 30/1 (CFR confirmado, NAO VFR; nb_frames 126695 =
dur x 30 exato)**, time_base 1/30000 (timescale 30000), duracao **4223.17s (~1h10m)**, ~20,9 Mbps; audio **aac
LC, 48000 Hz, estereo**; SO 2 streams (1 video + 1 audio, sem trilhas extras). **DESCOBERTA: o arquivo e 30fps,
NAO 24fps** (o dono tinha dito "meus podcasts sao 24fps"); o plano ja casa o fps NATIVO do ffprobe, entao cobre
os dois, mas a premissa "sempre 24" NAO vale (confirmar se as fontes variam). CFR = melhor cenario pro
concat-copy. **O concat AINDA NAO FOI RODADO** (o dono mandou parar pra documentar; o script de montagem nao foi
escrito ainda). Os artefatos (11GB + venv) seguem em `/mnt/opussb-jobs/_podcast_concat_test/` NO VOLUME DA VPS
ATUAL (nao migram sozinhos, ver §40; re-baixar do Drive na VPS nova se precisar). Ao retomar: escrever o script
de montagem (re-encode gancho + vinheta casando os params acima em 1920x1080, concat-copy do podcast,
inspecionar as DUAS emendas, testar re-encode so-audio se dessincronizar, medir tempo, apagar os artefatos no
fim). Vinheta de teste = `/opt/opussb/video/TELA FINAL CLUBCAST.mp4` (9:16, vira pillarbox; ok pra testar a
montagem, ja que a vinheta e re-encodada pros params-alvo de qualquer jeito).

## 40. Migracao de VPS (planejada, 2026-07-01): runbook. O S3 FICA, so a VPS muda [EXECUTADA em 2026-07-01→02; resultado e desvios na §42]

O dono vai TROCAR a VPS. **O bucket S3 da Hetzner (Object Storage, `fsn1`, `opussb-storage`) e MANTIDO** (e
externo a VPS; sobrevive a migracao), assim como RunPod, Anthropic e a imagem GPU no Docker Hub. So a maquina
muda. Este runbook lista o que TRANSFERIR, o que RE-INSTALAR e o CUTOVER. Confirmar cada passo contra o estado
real na hora; os detalhes de infra estao nas memorias `production-domain`, `networking-access-model`,
`github-backup`, `n8n-automation`, `gpu-image-build-disk`, que precisarao ser ATUALIZADAS pos-migracao.

**A) TRANSFERIR pra VPS nova (estado que NAO esta no S3 nem no git):**
- **Codigo:** `git clone git@github.com:kleniobraz/opussb.git` (repo privado, deploy key SSH). Gerar uma deploy
  key NOVA na VPS nova e adiciona-la no GitHub (a chave velha morre com a VPS velha). Ver `github-backup`.
- **`.env` de producao** (`/opt/opussb/.env`, ~1,7KB, FORA do git): TODOS os segredos (S3, RunPod, Anthropic, N8N
  webhook, Flask secret, Zoho). Copiar por canal seguro (scp direto entre as VPS).
- **Banco `opussb.db`** (`/opt/opussb/opussb.db`, ~1,8MB, default DB_PATH): usuarios, jobs, projetos,
  clip_captions, end_screens, etc. CRITICO. Copiar com o Flask PARADO (checkpoint do WAL antes; regra de restart).
- **`/root/.opussb_n8n_api_key`** (600, chave da API do N8N).
- **N8N (Docker):** o workflow (orquestra RunPod + Claude) e o dado mais delicado. Migrar o volume do container
  N8N, ou exportar/importar o workflow (ver `N8N_WORKFLOW.md` + `n8n-automation`, editavel por API). Conferir os
  nomes EXATOS dos nos (regra 3) e o callback FIXO de producao.
- **Assets locais:** `/opt/opussb/video/TELA FINAL CLUBCAST.mp4` (redundante com o S3 em
  `assets/end_screens/clubcast.mp4`, mas o local e a fonte). Backups em `/opt/opussb_backups/` (opcional).
- NAO precisa migrar os objetos do S3 (ficam onde estao) NEM os artefatos de teste do podcast
  (`/mnt/opussb-jobs/_podcast_concat_test/`, 11GB, descartaveis).

**B) RE-INSTALAR na VPS nova (infra que nao vem do git):**
- **Layout de disco:** replicar raiz pequena + VOLUME grande montado em `/mnt/opussb-jobs` (o `JOB_DIR` de prod;
  o default do config e `/opt/opussb/jobs`, mas o `.env` aponta pro volume). O volume tambem guarda o estado do
  BuildKit da imagem GPU (a raiz nao cabe a CUDA ~11G; ver `gpu-image-build-disk`).
- **Pacotes:** ffmpeg (hoje 4.4.2), Python 3.10 + venv (`/opt/opussb/venv`, recriar do `requirements`), Node 20
  via nvm (pro `frontend/`, `next build`), gunicorn, nginx, certbot, Docker (pro N8N), yt-dlp.
- **systemd:** `opussb.service` (Gunicorn, 1 worker/4 threads gthread, timeout 3600, bind DUPLO 127.0.0.1:5001 +
  172.17.0.1:5001) e `opussb-web.service` (`next start` em 127.0.0.1:3000). Copiar os unit files.
- **Docker + bridge:** instalar Docker ANTES de subir o Flask, senao o gateway `172.17.0.1` nao existe e o bind
  duplo falha (o N8N alcanca o Flask por `172.17.0.1:5001` direto). Subir o container do N8N. NAO reiniciar o
  dockerd a toa depois (derruba o N8N).
- **Nginx + HTTPS:** copiar o server block (proxy `/` -> Next 3000, `/api`+`/health` -> Flask 5001,
  `client_max_body_size 15G`, `proxy_request_buffering off`, 404 pra `/api/callback` e `/api/internal`).
  Re-emitir os certs Let's Encrypt (certbot) pra `sbclips.space` E `n8n.sbclips.space` DEPOIS que o DNS apontar
  pra VPS nova.
- **Firewall:** so 80/443 (e SSH) abertos ao mundo; 5678 (editor N8N) e as portas internas FECHADAS.

**C) CUTOVER (DNS):** na Hostinger, apontar os registros A de `sbclips.space` e `n8n.sbclips.space` pro IP NOVO.
So depois disso o certbot emite os certs. Validar: `/health`, login, um clipe editavel, o editor N8N. Manter a
VPS velha de pe ate a nova estar 100% (rollback = reverter o DNS).

**D) OPORTUNIDADE (decisao do dono):** hoje a VPS e Hetzner ASHBURN (US-East) e o S3 e `fsn1` (Falkenstein, DE),
ou seja, CROSS-REGION (§37). Se a VPS nova ficar na EUROPA (perto do S3), a latencia app<->S3 despenca E o upload
do fonte de 2h do pipeline de podcast (risco 5 da §39) fica barato; PORÉM Ashburn foi escolhida por ser boa pro
Brasil (~120-150ms). Trade-off de latencia (usuario BR vs S3) a decidir junto com a regiao da VPS nova.
Sinalizar impacto de caixa se o porte/plano da VPS mudar (regra 7).

## 41. Estado atual e proximos passos (2026-07-01) [SUPERSEDE §36]

Producao saudavel (`active_jobs=0`, `db:true`, opussb + opussb-web ativos, https://sbclips.space). **Nenhum
deploy de codigo nesta sessao** (so docs + design). O staging (5002/3001) esta PARADO.

**GIT:** `origin/master` == local. Nesta sessao: push do commit de docs `f0256cf` (BACKEND §38 / FRONTEND §28,
autorizado) e do handoff de design do editor `055cf59` (mockup HTML + assets). Estas atualizacoes de doc
(§39-41 + FRONTEND §29 + STAGING + memorias) vao pro proximo commit de docs.

**PROXIMOS PASSOS:**
1. **Migracao de VPS (§40):** proxima grande frente operacional. O S3 fica; so a maquina muda. Seguir o runbook.
2. **Pipeline de Podcast pra YouTube (§39):** design aprovado. Parte 1 (concat 16:9) em validacao: retomar
   escrevendo o script de montagem e rodando com o `podcast_teste.mp4` (30fps CFR). Decidir a Parte 0 (Claude no
   Flask vs N8N; estrategia de tokens) antes da infra.
3. Pendencias antigas ainda de pe: PARTE 5 do editor (karaoke por PALAVRA, o dado `words` ja flui, FRONTEND §28);
   validar tela final num clipe REAL da Carbone; multi-apresentador (split); `reprocess`; ultralytics RCE;
   remover a SPA `static/` legada.

## 42. Migracao pra Hostinger CONCLUIDA + estado atual (2026-07-02→03) [SUPERSEDE §41; executa o runbook §40]

Migracao executada em 2026-07-01→02, validando cada fase pelo IP antes do cutover de DNS. Producao saudavel
na VPS nova (`/health` db:true, opussb + opussb-web + nginx + docker ativos, https://sbclips.space no ar).
A Hetzner deixou de ser fallback (decisao do dono); rollback por DNS nao existe mais.

**Infra nova (verificada NA MAQUINA em 2026-07-02):**
- **Hostinger, Campinas/SP (Brasil, AS47583). 4 vCPU, 16GB RAM, SEM swap** (a Hetzner tinha 4GB), **disco
  unico de 200GB** (raiz ~194G, escrita direta medida **1,8 GB/s** vs 177 MB/s da Hetzner). IP `187.127.43.75`.
- **`/mnt/opussb-jobs` e PASTA no disco raiz, NAO volume dedicado** (desvio deliberado do §40-B: a raiz de
  200G comporta; revisitar se o BuildKit da imagem GPU (~11G) + jobs grandes apertarem).
- **N8N pinado em `docker.n8n.io/n8nio/n8n:2.25.7`** (mesma versao da Hetzner; volume `n8n_data` restaurado
  com a encryption key, sem migration de schema). `bgutil-provider` em pe. Ambos so em 127.0.0.1.
- **Certs Let's Encrypt re-emitidos** (sbclips.space+www e n8n.sbclips.space, expiram 2026-09-29);
  `certbot.timer` ativo com `renew_hook = systemctl reload nginx`. Zoho SSO no mesmo redirect URI.
- **Cross-region agora e Brasil<->Alemanha:** RTT VPS->S3 (`fsn1`) **~183ms** medido (era ~90-100ms de
  Ashburn). Na queima de legenda o S3 e so ~17% do tempo (download 24,7MB/s apesar do RTT); o gargalo e o
  ffmpeg (~83%). **`BURN_FFMPEG_THREADS` subiu de 2 pra 3 no `.env` (2026-07-03**, backup
  `.env.bak-burn3-20260703-002147`): ganho MEDIDO no mesmo clipe :7 = ffmpeg 47,4s -> 44,0s (**so -7%**, NAO
  os 25-40% estimados — o encoder nao consome o 3o thread; o limitador real e o decode + filtro `subtitles`
  do libass, SINGLE-THREAD, que alimenta o x264). Site 100% responsivo durante a queima (`/health` publico
  p50 7ms / p95 12ms / 0 falhas; idle 19%). Threads do encoder ESGOTOU como alavanca; ganho grande na queima
  = outra classe de mudanca (`BURN_CONCURRENCY=2`, agora comprovadamente seguro pro web, ou render so na
  exportacao — §37 opcao 3). `BURN_CONCURRENCY=1` mantido por ora;
  `S3_KEY_PREFIX` de producao = vazio (o prefixo `teste/` da validacao foi revertido e os artefatos apagados).

**LACUNAS da migracao (achadas na auditoria de 2026-07-02 — itens do §40-A/B que NAO vieram):**
1. **Firewall ufw** — RESOLVIDA em 2026-07-03: ufw ativo + enabled no boot, `deny incoming`; allow SO
   22/tcp, 80/tcp, 443/tcp e **`5001/tcp on docker0`** (regra ESSENCIAL descoberta na ativacao: o callback
   do N8N pro Flask em `172.17.0.1:5001` entra pela chain INPUT via docker0 — sem ela o pipeline de jobs
   quebraria silencioso). Ativado com anti-lockout (timer systemd de 5min, cancelado apos validacao);
   validado: SSH novo, 443/80 publicos, n8n->gateway:5001, /health durante queima.
2. **`/root/.opussb_n8n_api_key` NAO foi transferida** — edicao do workflow N8N via API REST INOPERANTE.
   ADIADA conscientemente (2026-07-03): o editor web https://n8n.sbclips.space cobre a operacao. Re-gerar a
   key se a automacao por API voltar a ser necessaria (ou exportar o workflow pelo editor web; ver a frente
   nova de REMOVER o N8N abaixo, que muda o calculo desta lacuna).
3. **Deploy key SSH do GitHub** — RESOLVIDA em 2026-07-02: chave nova `/root/.ssh/opussb_deploy` (ed25519,
   write access), remote de volta pra SSH (`git@github.com:kleniobraz/opussb.git`), known_hosts semeado da
   api.github.com/meta; `git ls-remote` ok. (Na migracao a chave NAO tinha sido gerada e o remote ficou em
   HTTPS sem credencial.)
4. **`/opt/opussb/video/TELA FINAL CLUBCAST.mp4` (fonte local) NAO veio** — ADIADA conscientemente
   (2026-07-03): a copia do S3 (`assets/end_screens/clubcast.mp4`) EXISTE (verificado) e e a que o app usa;
   repor a fonte local quando for conveniente.
5. `/opt/opussb_backups/` nao existe na VPS nova (era opcional; o historico ficou na Hetzner).

**FRENTE NOVA (2026-07-03, feedback do CTO Lucas): REMOVER o N8N do pipeline** — mover a orquestracao
(trigger -> RunPod Whisper -> scoring Claude -> callback) pro proprio Flask, eliminando o container N8N e sua
manutencao (encryption key, versao pinada, workflow editavel por API). E a PROXIMA GRANDE FRENTE.
**PRE-REQUISITO antes de qualquer codigo: EXPORTAR o workflow do N8N** (JSON completo, pelo editor web ja que
a API esta sem key — lacuna 2) e guarda-lo como backup/especificacao: ele e a fonte viva da orquestracao
(nomes de nos, corpo das chamadas RunPod/Claude, formato do callback; regra 3 e N8N_WORKFLOW.md).

**PROXIMOS PASSOS**: 1. **exportar o workflow do N8N e iniciar a frente de remocao do N8N** (acima);
2. lacunas ADIADAS (n8n api key, asset local) quando voltarem a doer; 3. pipeline de Podcast
pra YouTube (§39; a Parte 0 "Claude no Flask vs N8N" converge com a frente nova); 4. karaoke por PALAVRA
(FRONTEND §28); 5. pendencias antigas: tela final num clipe REAL da Carbone; multi-apresentador (split);
`reprocess`; ultralytics RCE; remover a SPA `static/` legada.

## 43. Manutenção programada: tela 503 + botão no painel admin + break-glass SSH (2026-07-03) [front em FRONTEND §30]

Tela de manutenção servida pelo **nginx** com HTTP **503**, ligada/desligada por um **flag file** (sem
reload, sem restart — o nginx testa a existência a cada request com `if -f`). Três formas de operar o
MESMO flag: **botão no painel admin** (Flask), **break-glass por SSH** (`maintenance on|off|status`), e
edição manual do arquivo.

**O flag:** `/var/lib/opussb/maintenance.on` (dono **www-data**). Vive em `/var/lib/opussb` (não em
`/etc/nginx`) porque o Flask roda como www-data e precisa criar/remover; o nginx só LÊ. O conteúdo é só
auditoria (quem/quando ligou); o que importa é a EXISTÊNCIA. `config.MAINTENANCE_FLAG` (env-tunável).

**Allowlist de IP:** `geo $sb_ip_allowed` em `/etc/nginx/conf.d/00-sb-maintenance.conf` (é a **fonte de
verdade**; IPs do dono: `177.89.14.174`, `206.42.63.129`). Sem CDN na frente → `$remote_addr` é o IP real.
Adicionar/remover IP = editar lá + `nginx -t && systemctl reload nginx` (isso é allowlist, NÃO o liga/desliga).

**Escopo HARD (decisão do dono 2026-07-03):** durante a manutenção, quem está fora da allowlist recebe 503
tanto nas **páginas** (`location /`) quanto na **/api** — nenhuma escrita entra pelo app na janela. Pra isso
o `location ~ ^/(api|health)` foi **dividido**: `location = /health` (prioridade máxima, NUNCA bloqueado —
monitoramento sempre vê o app; `proxy_intercept_errors off` deixa até um /health "degraded" 503 do Flask
passar como JSON, nunca a página) + `location ~ ^/api` (com `if ($sb_maint = "on") { return 503; }`). O
`location ~ ^/api/(callback|internal)` (404) continua casando antes. O IP liberado tem `$sb_maint=""` e passa
normal. A SPA legada `/static/` NÃO é gateada de propósito (sem /api ela não funciona mesmo).

**Rotas Flask** (app.py, `@admin_required` — valida is_admin+status=active no DB a cada request):
- `GET /api/admin/maintenance` → `{on, client_ip, ip_allowed, allowlist}`. Estado = existência do flag, então
  mudanças por fora (break-glass, outro admin) aparecem na hora. `client_ip` via `_client_ip()` (None pra
  loopback — acesso do próprio VPS mostra "desconhecido"; do browser real do dono vem certo). `ip_allowed`
  via `_ip_in_allowlist()` com **`ipaddress`** (cobre IP exato, CIDR e IPv6). Parse do geo com `errors="replace"`
  (byte não-UTF-8 num comentário não derruba o GET). Anti-lockout: informativo, nunca bloqueia o interruptor.
- `POST /api/admin/maintenance {"on": bool}` → estado DESEJADO, idempotente (não é toggle). `bool` ESTRITO
  (string não coage → 400). `_write_maintenance_flag()` é à prova de flag root-owned: se `open('w')` der EACCES
  (flag criado por root/break-glass), faz `unlink`+recria (o unlink só exige escrita no diretório, que é do
  www-data). Loga a ação (`log.info admin ... LIGOU/desligou ... ip`).

**Break-glass** `/usr/local/bin/maintenance` (cópia **root:root 755** do `maintenance.sh` do repo — NUNCA
symlink pro repo www-data, senão vira escalada www-data→root). `on` cria `/var/lib/opussb` se faltar
(`install -d`, cobre DR/restore, já que o backup é só db+código) e cria o flag como www-data. `off` = `rm -f`.
`status` mostra a auditoria. Instalação/atualização do script = re-`install` manual (deliberado).

**Página** (`/var/www/sb-maintenance/sb_maintenance.html`): documento único **self-contained** (0 request
externo). Identidade real do design system: fundo `#0a0a0b`, dourado `#f5c518` (+grad `#ffd75e→#e0a800`),
fonte **Space Grotesk** embutida como woff2 `data:` (subset latin do `.next`, variável 300–700), marca
(tesoura "Corte Viral") inline SVG, e um **fundo WebGL** (ondas douradas c/ aberração cromática, fallback pra
glows CSS se o WebGL falhar; respeita `prefers-reduced-motion`). Texto exato: "Opa, estamos em manutenção
programada! / Voltamos amanhã às 09:00 PT/BR :)". Servida via `error_page 503 /sb_maintenance.html` +
`location = /sb_maintenance.html` internal; os headers de segurança (CSP com `script-src/style-src
'unsafe-inline'` + `font-src data:`) são **re-declarados no location** (add_header no location cancela a
herança) — por isso o script/fonte/estilo inline da página passam no CSP.

**Deploy (2026-07-03):** backups em `/opt/opussb_backups/` (db via `.backup` online + tarball do código;
**diretório criado neste deploy** — não existia na VPS Hostinger, era lacuna 5 da §42). Migração do flag SEM
gap (`/etc/nginx/sb_maintenance.on` → `/var/lib/opussb/maintenance.on`): flag novo criado ligado ANTES do
reload, antigo removido depois. `test_admin_maintenance` (test_backend.py) **30/30**; `tsc --noEmit` 0;
`nginx -t` 0; E2E ponta-a-ponta OK (botão liga/desliga + break-glass no mesmo flag + /api hard + /health
sempre 200). Diff passou por verificação adversarial (3 lentes) antes de aplicar. **Falhas pré-existentes
(NÃO desta mudança, confirmado por git stash):** `test_backend.py` 1 fail `RERENDER_GPU_ENABLED default false`
(o `.env` tem `=true`); `pytest tests/` 11 fails em `test_app.py` (suíte antiga desatualizada do sistema de
login → 401).

**Rollback:** desligar = `maintenance off` (ou `rm -f` o flag). Reverter o mecanismo = `git revert` do código +
restaurar `/etc/nginx/sites-available/sbclips.space` do `.bak-maint-*` + `conf.d/00-sb-maintenance.conf` +
`nginx -t && reload`. Backups do deploy em `/opt/opussb_backups/opussb-20260703-115925.db` e `code-*.tar.gz`.
