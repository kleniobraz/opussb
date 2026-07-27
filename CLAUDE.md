# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Idioma: fale com o Klenio SEMPRE em português (inglês só em código, bash, logs e nomes técnicos).
> Este repositório (código, comentários e docs) é todo em português por decisão do dono.

## O que é

**SB Clips é a plataforma própria de produção audiovisual do Grupo Quatro5** (uso interno).
Transforma podcast em clipes e em episódios prontos pra publicar: transcreve (Whisper via RunPod),
o Claude pontua os melhores momentos pelo ICP de cada empresa (via N8N), corta + reenquadra pra
9:16 + queima legenda no estilo da casa. Também faz o pipeline de podcast pro YouTube (gancho
aprovado por humano, corte de início com precisão de frame, vinheta e tela final da casa), editor
estilo Premiere e áudio pro Spotify.

**Não é clone nem versão self-hosted do OpusClip.** Citar OpusClip só como referência de mercado
(decisão de UI do editor), nunca como definição do que o SB Clips é.

**Trabalha-se direto na VPS de produção em `/opt/opussb`** (não há ambiente local do dev):
qualquer `systemctl restart` sobe junto o que estiver não commitado no working tree.

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
10. **Subagents são padrão, não sob pedido.** Antes de codar mudança relevante (rota nova, módulo
    novo, mexer no pipeline/schema/S3): `architecture` (duplicação e reuso). Antes de commitar:
    `code-review` no diff. Se tocar input externo, credencial, auth ou rota: `seguranca`. São
    rodadas de leitura; o Klenio decide o que aplicar. Pula só em fix trivial (typo, string,
    constante) e avisa que pulou.

## Comandos

```bash
# Testes de backend — DOIS conjuntos, rode os dois:
./venv/bin/python test_backend.py          # suíte legada (pura, sem rede/DB real); usa DB temporário
./venv/bin/python -m pytest                 # suíte pytest (tests/); conftest.py isola DB/JOB_DIR em tmp
./venv/bin/python -m pytest tests/test_caption_review.py::nome_do_teste   # um teste só

# Lint (ruff não está no venv; roda ad-hoc):
uvx ruff check .            # ou pipx run ruff check .

# Rodar/operar o backend (produção). O gate e o restart no MESMO encadeamento, nunca em
# linhas separadas (ver "Verificação que não bloqueia é teatro", abaixo):
maintenance on                              # 1o: nginx recusa /api pra nao-admin, nenhum upload novo entra
curl -s localhost:5001/api/internal/deploy_gate | jq   # 2o: leia, inclusive os "suspeitos"
curl -sf localhost:5001/api/internal/deploy_gate | jq -e '.pode_reiniciar == true' >/dev/null \
  && systemctl restart opussb                # 3o: o && é o que impede o restart quando o gate fecha
cat /opt/opussb-deploy/current/.next/BUILD_ID   # 4o: SE tocou o front, tem que ter MUDADO
journalctl -u opussb -f                      # logs (print() do worker fica buffered; use log.*/stderr)
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5001/health

# Frontend (Next.js) — Node 20 via nvm sob o root:
source ~/.nvm/nvm.sh && nvm use 20
cd frontend && npm run dev        # dev
npm run lint                      # eslint

# Deploy do FRONT: SEMPRE por este script, NUNCA npm run build em /opt/opussb/frontend
./deploy-frontend.sh              # build isolado + smoke test + symlink atômico + restart
./deploy-frontend.sh --dry-run    # valida o pipeline sem reiniciar produção

# Manutenção programada (tela 503 do nginx):
maintenance on|off|status         # break-glass SSH; mesmo flag que o botão do painel admin
```

**Deploy do BACKEND** (sem CI): backup (`cp opussb.db` + `tar czf` dos `.py` em
`/opt/opussb_backups/`), `systemctl restart opussb` **só com `"pode_reiniciar": true` em
`/api/internal/deploy_gate`**.

**Deploy do FRONT: `./deploy-frontend.sh`, e SÓ ele.** Nunca `npm run build` dentro de
`/opt/opussb/frontend`, nunca `systemctl restart opussb-web` na mão. Duas razões, e as duas já
custaram caro:

1. **Buildar na árvore viva derruba produção.** O `next start` faz `require()` dos arquivos de
   `.next/server` sob demanda a cada requisição, não só no boot. Um `next build` por cima troca
   os nomes com hash e APAGA os antigos enquanto o processo no ar e o cache do navegador ainda
   apontam pra eles: 500 em chunk JS. Aconteceu **três vezes** em 2026-07-13. O script builda
   numa worktree isolada por SHA, com `node_modules` e `.next` próprios.
2. **Buildar ali não publica nada.** O `opussb-web` serve de `/opt/opussb-deploy/current`, um
   symlink pra releases versionadas. Buildar em `/opt/opussb/frontend` e reiniciar o serviço faz
   o serviço subir com o MESMO release de antes: o build compila, o restart funciona, e nada
   muda no ar. Em 2026-07-26 isso passou por um deploy inteiro sem ser notado, e a feature nova
   ficou fora do ar por horas com todo mundo achando que estava publicada.

O script cuida do resto: preflight (recusa se `frontend/` tem mudança não commitada, senão
deployaria sem ela), smoke test numa porta descartável ANTES de trocar o symlink, troca atômica,
verificação pós-restart pela via real e rollback automático pra release anterior se falhar.

**Se a mudança toca o front, a verificação obrigatória é o BUILD_ID SERVIDO ter mudado:**

```bash
cat /opt/opussb-deploy/current/.next/BUILD_ID       # antes e depois: tem que ser diferente
curl -s https://sbclips.space/<rota> | grep -oE '/_next/static/chunks/[^"]+\.js'  # e baixar um
```

Compilar não é publicar, e "o build passou" não é evidência de nada. Verifique o RESULTADO
servido pela URL pública, não o processo de build.

## Verificação que não bloqueia é teatro

**Toda verificação tem que BLOQUEAR a ação que ela protege, não só reportar.** Verificação que
imprime e deixa o comando seguinte rodar é teatro: ela produz a sensação de rigor e o registro
de que "foi checado", sem nunca impedir nada.

Na prática: o comando de verificação e a ação ficam no MESMO encadeamento (`&&`), ou a ação é
condicionada ao código de saída da verificação (`if ! verifica; then exit 1; fi`, com
`set -euo pipefail` em script). **Nunca em linhas independentes.** Um `assert` que imprime
`ABORTAR` numa linha não impede o `systemctl restart` da linha de baixo.

E a verificação tem que olhar o EFEITO, não o processo: o resultado servido, não o build que
compilou; o estado no banco, não a resposta HTTP; o teste ficando vermelho quando você quebra o
código de propósito, não o teste passando.

Três casos do MESMO padrão, todos em 2026-07-26/27, que é a razão de isto estar escrito aqui
como regra e não como anedota:

1. **O build que compilou sem publicar.** `npm run build` rodou, `systemctl restart opussb-web`
   rodou, e o release no ar continuou sendo o de três commits antes, porque o serviço serve de
   outro diretório. Verificou-se o processo (compilou), não o efeito (o BUILD_ID servido).
   A feature ficou fora do ar por horas com todo mundo achando que estava publicada.
2. **O gate que reportou sem bloquear.** O `deploy_gate` respondeu `pode_reiniciar: false` com
   um upload em voo há 140s, o assert disparou corretamente, e o `systemctl restart` da linha
   SEGUINTE rodou assim mesmo e matou o upload de um usuário (`POST /api/upload -> 502`). O
   gate funcionou; quem não obedeceu foi o operador. Duas linhas, nenhum `&&`.
3. **O teste de mutação que não chegou a ser aplicado.** Ao validar três gates novos de
   autorização removendo cada um pra ver o teste ficar vermelho, uma das três substituições
   não casou a string e não alterou nada. O teste passou verde e teria sido reportado como
   "provado por mutação" sem nunca ter sido mutado. Mutação só vale se você confirma que a
   mutação foi APLICADA.

**O gate de deploy é o `pode_reiniciar`, NÃO o `active_jobs`.** O `active_jobs` só enxerga a
tabela `jobs`, e já mentiu 3x: upload em voo não cria linha até TERMINAR (um restart matou um
de 10GB com 75min de trabalho em 2026-07-26), e queima, thumb, re-render, extração de MP3 e
revisor de legendas também não aparecem lá. O `pode_reiniciar` junta as duas metades: o banco
(durável) e o `em_voo.py` (o que ESTE processo faz agora, com idade).

**Ligue a manutenção ANTES** (`maintenance on`): o nginx passa a recusar `/api` pra não-admin,
então nenhum upload novo começa e a fila converge, em vez de você correr atrás dela.

### A manutenção é CONTRAINTUITIVA no upload direto, e isso precisa estar dito inteiro

**O primeiro passo do ritual, que existe pra proteger trabalho em voo, é o que mais destrói
trabalho em voo no upload direto.**

No upload direto os bytes vão do navegador pro S3 **sem passar pela VPS**, e o cliente volta
ao servidor só pra pedir o próximo LOTE de URLs presignadas (`/parts`). Então o restart, que é
o que o ritual teme, esse caminho **sobrevive** por construção (ver o cabeçalho do
`podcast_ingest.py`); e a tela de 503 da manutenção, que é o que o ritual usa pra proteger,
**mata**: um envio de 10GB morre em minutos, quando esgota o lote atual de presignadas.

Duas coisas foram feitas a respeito, e a segunda ainda está aberta:

1. **`/api/podcast/upload/parts` é exceção ao gate de manutenção** (nginx, `location =`, junto
   de `/api/login` e irmãs). É seguro e não afrouxa nada: a rota exige sessão válida E token
   assinado de um multipart JÁ ABERTO, não inicia trabalho na VPS, não cria job, não ocupa a
   vaga; só assina URL. E o **`/init` continua bloqueado**, então nenhum upload novo começa:
   só terminam os que já estavam voando, que é exatamente o que a manutenção quer.
2. **O `/complete` continua sendo o buraco.** Ele cria o job, então segue bloqueado. Upload que
   TERMINA dentro da janela sobe todos os bytes e morre no último passo; o retry do cliente
   cobre só ~42s (3 tentativas com backoff). Janela curta ele atravessa, janela longa não.
   **A saída definitiva já está decidida, pra não virar discussão nova quando chegar a hora:**
   com a manutenção ligada, o `/complete` **ESTACIONA** o job (`uploaded`) em vez de disparar
   a thread1. Aí nada começa, nada se perde, e a isenção passa a ser completa. Depende do
   estacionamento condicional que entra com a frente de upload direto pra clipes.

**Por isso, o ritual ganhou um passo que o gate NÃO consegue dar por você.** Antes de ligar a
manutenção, olhe se há upload direto em voo, porque o `deploy_gate` **não enxerga isso por
construção**: os bytes não passam pela VPS, então não há request longo pro `_marca_upload_em_voo`
marcar, e o `pode_reiniciar` responde `true` com um envio de 10GB acontecendo. Está correto
(o restart não o mataria); o que mata é o passo seguinte do ritual.

```bash
# Upload direto em voo? (o gate não sabe responder isso)
grep -E "upload/(parts|complete)" /var/log/nginx/access.log | tail -3
```

Se houver `/parts` recente sem um `/complete` depois, tem envio acontecendo. Com a isenção do
item 1 ele sobrevive à manutenção, mas se estiver perto do fim pode bater no `/complete`
bloqueado: espere o `/complete` aparecer no log antes de seguir.

O gate fica numa rota INTERNA (o nginx devolve 404 pra fora) e não no `/health`: aquele é
público, e publicar em tempo real o que a VPS está fazendo entregaria a qualquer um o horário
dos uploads e o momento exato em que a fila esvazia, que é quando o site cai pro deploy.

**E OLHE a lista `suspeitos` antes de reiniciar**, não só o `pode_reiniciar: true`. Suspeito é
operação em voo há mais tempo que o teto da categoria dela: ela NÃO bloqueia (senão um
vazamento travaria o deploy pra sempre), mas ignorar suspeito em silêncio é exatamente como
chegamos ao incidente. Se aparecer `"upload": {"n":1,"mais_antigo_s":21600}`, ou vazou de
verdade, ou é um upload legítimo lentíssimo que o restart vai matar. Nos dois casos, decida
consciente antes de reiniciar.

**O gate reduz a janela, não a elimina.** `POST /api/callback` do N8N chega pelo bind
`172.17.0.1:5001` sem passar pelo nginx, então a manutenção não o bloqueia: um callback pode
aterrissar entre a sua leitura do gate e o `systemctl restart` e disparar a thread2. É a mesma
corrida que o `active_jobs` sempre teve; o gate só a encolheu de 75 minutos para segundos.
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
  `uploading_clips` → `done`. `MAX_CONCURRENT_JOBS=1`; `reap_stale_jobs` (watchdog >30min)
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
geração de clipes). Tudo env-tunável em `config.py`.

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

**Cliente Claude:** `anthropic_client.py` é o cliente único da
Messages API (`call`/`text_block`/`image_block`/`price`), com structured output + parse tolerante. Antes
disto o Claude só era chamado via N8N. O **Revisor de legendas** (`caption_reviewer.py`, daemon thread)
usa Sonnet 5 em duas chamadas: visão LÊ o texto queimado dos frames (extraídos + deduplicados) e depois
REVISA contra o glossário (tabela `glossary`) + ortografia. Mídia é efêmera (apagada no fim).

## Docs de contexto (REFERÊNCIA sob demanda, NÃO ler por padrão)

**Economia de tokens:** NÃO abra `BACKEND_CONTEXT.md`, `FRONTEND_CONTEXT.md`, planos nem specs por
padrão (o BACKEND sozinho passa de 30k tokens). Só se o Klenio pedir, ou se faltar uma regra que não
está aqui: `grep -n '§7' BACKEND_CONTEXT.md` pra achar a seção e leia SÓ aquele trecho com
`Read offset/limit`. Nunca o arquivo inteiro. O mapa abaixo diz onde procurar:
- **`BACKEND_CONTEXT.md`** — fonte de verdade do backend (§1 regras, §3 pipeline, §5 modelo de dados,
  §7 auth, §8 operação, histórico §11+ até §43). Seções mais recentes SUPERSEDEM as antigas.
- **`FRONTEND_CONTEXT.md`** — fonte de verdade do front Next.js (migração da SPA `static/`, stack, telas).
- **`STAGING.md`** — worktree `/opt/opussb-staging` (branch `staging`), isolamento via `S3_KEY_PREFIX`.
- **`N8N_WORKFLOW.md`** / **`SECURITY_AUDIT.md`** — orquestração N8N e auditoria de segurança.
- **`docs/superpowers/`** — specs e planos de implementação (grandes; só sob pedido explícito).
- **`frontend/AGENTS.md`** — aviso: o Next.js aqui é versão nova; docs em `node_modules/next/dist/docs/`.
- Memória do projeto (persiste entre sessões): `/root/.claude/projects/-opt-opussb/memory/`.
