# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Idioma: fale com o Klenio SEMPRE em português (inglês só em código, bash, logs e nomes técnicos).
> Este repositório (código, comentários e docs) é todo em português por decisão do dono.

## O que é

OpusSB ("SB Clips") é um clone self-hosted do OpusClip, de **uso interno** do Grupo Quatro5 / SB
(não é aberto ao público). Recebe um vídeo longo, transcreve (Whisper via RunPod), o Claude pontua
os melhores momentos (via N8N), e o sistema corta + reenquadra pra 9:16 + queima legenda no estilo
da casa. **Trabalha-se direto na VPS de produção em `/opt/opussb`** (não há ambiente local do dev).

## Regras inegociáveis (constraints do dono — ver BACKEND_CONTEXT.md §1)

Estas SOBREPÕEM qualquer comportamento padrão:

1. **Nunca use travessão** (nem `—` nem `-` como travessão) em texto gerado.
2. **Nunca edite um `.py` do servidor com nano/vim deixando arquivo pela metade.** Edição por
   substituição exata (Edit) é ok; o proibido é entregar arquivo cortado/incompleto.
3. **Push na `master` exige autorização explícita do Klenio.** Commit local pode; push, pergunte.
4. **Decisão de arquitetura ou custo: pergunte/responda ANTES de executar.** Toda mudança que
   toca caixa ou custo recorrente (chamadas a Claude/RunPod, storage) sinalize o impacto antes.
5. **Não troque o modelo de scoring** (Haiku) pra Sonnet/Opus sem justificativa de qualidade medida.
6. **Respeite a máquina de estados** do pipeline (abaixo). Não invente estados nem pule etapas.
7. **Nunca apague o vídeo de origem antes de confirmar que os clipes subiram pro S3** (o `rmtree`
   de cleanup só no fim do bloco de sucesso).
8. **Testes nunca tocam dados reais.** Fabrique jobs/usuários com prefixo `TEST` e limpe tudo no fim.
9. **Antes de mexer no N8N**, confira o nome EXATO dos nós e faça backup do workflow antes de cada PUT.

## Comandos

```bash
# Testes de backend — DOIS conjuntos, rode os dois:
./venv/bin/python test_backend.py          # suíte legada (pura, sem rede/DB real); usa DB temporário
./venv/bin/python -m pytest                 # suíte pytest (tests/); conftest.py isola DB/JOB_DIR em tmp
./venv/bin/python -m pytest tests/test_caption_review.py::nome_do_teste   # um teste só

# Lint (ruff não está no venv; roda ad-hoc):
uvx ruff check .            # ou pipx run ruff check .

# Rodar/operar o backend (produção):
systemctl restart opussb                    # aplica mudança em .py (só quando active_jobs=0 + backup)
journalctl -u opussb -f                      # logs (print() do worker fica buffered; use log.*/stderr)
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5001/health

# Frontend (Next.js) — Node 20 via nvm sob o root:
source ~/.nvm/nvm.sh && nvm use 20
cd frontend && npm run dev        # dev
npm run build && npm run lint     # build de produção + eslint

# Manutenção programada (tela 503 do nginx):
maintenance on|off|status         # break-glass SSH; mesmo flag que o botão do painel admin
```

**Deploy** (sem CI): backup (`cp opussb.db` + `tar czf` dos `.py` em `/opt/opussb_backups/`),
`systemctl restart opussb` **com `active_jobs=0`**, e pro front `npm run build` + `systemctl restart opussb-web`.
Migrações de banco são **aditivas** (código antigo ignora coluna/tabela nova); rollback = `git checkout <commit> -- <arquivos>` + restart, ou restaurar o tarball.

## Arquitetura (resumo inline, não precisa abrir doc nenhum)

**Serviço:** Flask + Gunicorn (**1 worker / 4 threads gthread**, `--timeout 3600`), systemd unit
`opussb.service`, bind duplo `127.0.0.1:5001` + `172.17.0.1:5001` (gateway Docker), **nunca `0.0.0.0`**.
Nginx (HTTPS 443) faz proxy de `/` pro Next (`opussb-web`, 127.0.0.1:3000) e de `/api`+`/health` pro
Flask; devolve **404 pra `/api/callback` e `/api/internal`** (rotas internas só alcançáveis pela subnet
Docker, não de fora). Config única em `config.py` (lê `.env`; vars sem default levantam KeyError de propósito).

**Pipeline (máquina de estados), 3 threads daemon:**
- `POST /api/upload` (ou `/api/upload/youtube`, que adiciona `downloading` via yt-dlp) → **thread1**
  (`processor.run_thread1`): `extracting_audio` → `uploading_audio` (áudio pro S3) → `transcribing` →
  dispara o webhook do N8N.
- **N8N** (Docker) roda RunPod (Whisper → SRT) + Claude (scoring Haiku → `clips_json`) e chama
  `POST /api/callback`.
- O callback dispara a **thread2** (`processor.run_thread2`): `cutting` → `reframing` →
  `uploading_clips` → `done`. `MAX_CONCURRENT_JOBS=1` (um job por vez); `reap_stale_jobs` (watchdog >30min)
  libera vaga de job preso; cancelamento é cooperativo (`_Cancelled`).

**Legenda editável + queima sob demanda (o núcleo do produto):** a thread2 **NÃO queima** — sobe o
clipe **sem legenda** (`clip_NN_base.mp4`) e grava os segmentos em `clip_captions`. A queima acontece
sob demanda em `GET /api/clips/<id>/video` (baixa a base, gera `.ass`, queima, cacheia `clip_NN_cap.mp4`,
302 pra URL presignada). Tem **lock por clipe**, timeout no ffmpeg, e **fallback** que serve a base sem
legenda se a queima falhar (o clipe sempre toca; nunca deixa `_cap.mp4` corrompido). Editar legenda/música
**invalida o cache** (apaga o `_cap.mp4`). A queima roda em `app.py` (`_build_burn_cmd`/`_burn_clip`), NÃO
no reframer, pra não carregar o YOLO no worker web. **Armadilha do áudio:** com legenda e sem música o
`-map [v]` desliga o auto-map do ffmpeg; tem que mapear `-map 0:a?` ou o clipe sai mudo (regressão travada
pelo `test_burn_cmd`).

**Concorrência defensiva (VPS de 4 vCPU sem swap):** semáforos separados por tarefa mantêm o site de pé —
`BURN_CONCURRENCY` (queima ffmpeg, + `nice` + threads limitadas), `THUMB_CONCURRENCY` (poster), e a **lane
própria** `CAPTION_REVIEW_CONCURRENCY` (independente de `MAX_CONCURRENT_JOBS`: uma revisão nunca trava a
geração de clipes). Tudo env-tunável em `config.py` (os comentários lá explicam o porquê de cada default).

**Reframe 9:16:** `reframe_core.py` é a matemática PURA de câmera (sem `config`, tudo por argumento),
compartilhada entre a VPS/CPU (`reframer.py`, libx264 + YOLO) e o worker GPU (`gpu_reframe/handler.py`,
h264_nvenc) pra o enquadramento ser IDÊNTICO nos dois. Offload pra GPU é opcional (RunPod 2º endpoint,
flags `REFRAME_GPU_ENABLED`/`RERENDER_GPU_ENABLED`, default off; fallback pra CPU por clipe sempre ativo).

**Persistência:** SQLite em `opussb.db` (contém hashes — **nunca commitar**). `db.py` centraliza todo
o acesso; cada tabela tem seu `init_*` (chamados no boot de `app.py`) e migrações aditivas via
`_ensure_column`. S3 (Hetzner Object Storage via boto3) em `s3_client.py`: toda key passa por `_k()`,
que prefixa com `S3_KEY_PREFIX` — vazio em produção, `staging/` no staging, isolando ambientes no MESMO
bucket (ver STAGING.md). `app.py`/`processor.py` sempre constroem keys SEM prefixo.

**Auth:** sessão via cookie assinado + **estado server-side** na tabela `sessions` (validada a cada
request, dá pra listar/revogar). Zoho SSO opcional (`ZOHO_ENABLED`) com aprovação por domínio; senha
com scrypt; `must_change_password` gateia o upload. Rotas internas defendidas em 3 camadas (Flask não
escuta público + Nginx 404 + gate por subnet `_is_trusted_caller`).

**Cliente Claude (novo, branch `feat/revisor-legendas`):** `anthropic_client.py` é o cliente único da
Messages API (`call`/`text_block`/`image_block`/`price`), com structured output + parse tolerante. Antes
disto o Claude só era chamado via N8N. O **Revisor de legendas** (`caption_reviewer.py`, daemon thread)
usa Sonnet 5 em duas chamadas: visão LÊ o texto queimado dos frames (extraídos + deduplicados) e depois
REVISA contra o glossário (tabela `glossary`) + ortografia. Mídia é efêmera (apagada no fim).

## Docs de contexto (REFERÊNCIA sob demanda, NÃO ler por padrão)

**Regra de contexto (economia de tokens):** por padrão NÃO abra `BACKEND_CONTEXT.md`,
`FRONTEND_CONTEXT.md`, os planos nem os specs. Trabalhe apenas com os arquivos diretamente
envolvidos na tarefa pedida. Esses docs são longos (o BACKEND sozinho passa de 30k tokens) e
carregá-los infla o contexto sem necessidade em tarefas pequenas.

**Quando (e só quando) consultar:** se o Klenio pedir explicitamente, OU se faltar uma regra/decisão
que não está aqui no CLAUDE.md. Nesse caso, use `grep -n` pra achar a seção específica (as seções são
numeradas, ex.: `grep -n '§7' BACKEND_CONTEXT.md`) e leia SÓ aquele trecho com `Read offset/limit`.
Nunca leia o arquivo inteiro.

**Não conflita com as regras inegociáveis:** as constraints do dono lá em cima citam `BACKEND_CONTEXT.md §1`
como fonte detalhada. Se precisar do detalhe de uma constraint (ex.: nuance da máquina de estados, §1/§3),
ler AQUELA seção específica via grep é permitido e NÃO viola a regra de economia. A economia proíbe o
carregamento amplo e proativo, não a consulta pontual de uma regra.

Mapa do que cada doc cobre (pra saber onde procurar quando precisar):
- **`BACKEND_CONTEXT.md`** — fonte de verdade do backend (§1 regras, §3 pipeline, §5 modelo de dados,
  §7 auth, §8 operação, histórico §11+ até §43). Seções mais recentes SUPERSEDEM as antigas.
- **`FRONTEND_CONTEXT.md`** — fonte de verdade do front Next.js (migração da SPA `static/`, stack, telas).
- **`STAGING.md`** — worktree `/opt/opussb-staging` (branch `staging`), isolamento via `S3_KEY_PREFIX`.
- **`N8N_WORKFLOW.md`** / **`SECURITY_AUDIT.md`** — orquestração N8N e auditoria de segurança.
- **`docs/superpowers/`** — specs e planos de implementação (grandes; só sob pedido explícito).
- **`frontend/AGENTS.md`** — aviso: o Next.js aqui é versão nova; docs em `node_modules/next/dist/docs/`.
- Memória do projeto (persiste entre sessões): `/root/.claude/projects/-opt-opussb/memory/`.
