# OpusSB · STAGING (servidor de testes)

Isolamento LEVE/sob demanda do staging (worktree `/opt/opussb-staging`, branch `staging`), montado
na Fase 0 do editor de crop por keyframe (BACKEND_CONTEXT §33). Decisao do dono: staging primeiro,
nada direto em producao.

## Como o staging fica isolado de producao

Producao roda de `/opt/opussb` (units systemd `opussb` + `opussb-web`, porta 5001/3000, branch master).
O staging roda do worktree `/opt/opussb-staging` com um `.env` PROPRIO que sobrescreve so o isolamento
(mesmos segredos/creds; o resto fica igual). Diferencas (no `/opt/opussb-staging/.env`):

| Var | Producao | Staging |
|---|---|---|
| `FLASK_PORT` | 5001 | **5002** |
| `JOB_DIR` | /mnt/opussb-jobs | **/mnt/opussb-jobs-staging** |
| `TMPDIR` | /mnt/opussb-jobs/tmp | **/mnt/opussb-jobs-staging/tmp** |
| `DB_PATH` | (default `/opt/opussb/opussb.db`) | **/opt/opussb-staging/opussb.db** |
| `S3_KEY_PREFIX` | "" (keys em `jobs/...`, `assets/...`) | **`staging/`** (tudo sob `staging/...` no MESMO bucket) |

O `S3_KEY_PREFIX` (knob novo em `config.py`, aplicado SO na borda do `s3_client.py` via `_k()`) garante
que o staging NUNCA toca objetos de producao: toda key vira `staging/jobs/...`. `app.py`/`processor.py`
seguem construindo keys SEM prefixo (o prefixo entra so no I/O). Em producao o knob fica "" => zero mudanca.

## Rodar o staging (sob demanda; sem unit permanente)

Backend Flask (porta 5002), a partir do worktree pra carregar o `.env` certo:
```
cd /opt/opussb-staging && /opt/opussb/venv/bin/python app.py        # ou gunicorn na 5002
```
Front (dev), apontando o rewrite de `/api` pro 5002 (ver next.config do front) e bind em 3001:
```
cd /opt/opussb-staging/frontend && source ~/.nvm/nvm.sh && nvm use 20 && npm run dev -- -H 127.0.0.1 -p 3001
```
Acesso por tunel SSH (`ssh -L 3001:127.0.0.1:3001 <vps>` + `-L 5002:127.0.0.1:5002`). Login: senha
(break-glass); o Zoho redireciona pra producao (ver caveat).

## Caveats (importante)

- **NAO subir job real pelo upload no staging.** O `N8N_WEBHOOK_URL` aponta pro N8N de producao, cujo
  workflow chama de volta um callback FIXO (producao). Um job de staging dispararia transcricao real e o
  callback iria pro lugar errado. Para de-riscar o crop, **semear dados de teste copiando objetos de um
  clipe de producao pro prefixo `staging/`** (`copy_object` dentro do bucket) + as linhas equivalentes no
  DB de staging, em vez de rodar o pipeline inteiro.
- **Zoho:** `ZOHO_REDIRECT_URI` = producao; login Zoho no staging nao fecha o ciclo. Usar senha.
- **DB de staging** nasce vazio (`/opt/opussb-staging/opussb.db`); criar um usuario admin com
  `manage_users.py` apontando o `.env` do staging se precisar logar.
- **RunPod:** staging usa os MESMOS endpoints (serverless, pay-per-use). Reframe GPU de teste custa centavos.

## Pendente da Fase 0

- **Lifecycle do Hetzner (TTL 30d do 16:9):** o teste de `PutBucketLifecycle` (escopado em
  `staging/lifecycle-probe/`) foi BLOQUEADO pelo classificador do harness (mutacao no bucket de producao).
  Decidir: liberar a permissao pro teste, ou ir de plano B (cron no app por LastModified, sem mexer no
  bucket). Sem lifecycle no bucket hoje (`NoSuchLifecycleConfiguration`).
