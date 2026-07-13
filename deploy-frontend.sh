#!/usr/bin/env bash
# deploy-frontend — build e deploy do frontend Next.js isolados da produção.
#
# Builda numa release isolada (git worktree por SHA, com node_modules e .next próprios,
# nunca dentro de /opt/opussb/frontend) e só troca o symlink `current` + reinicia o
# opussb-web depois de um smoke test passar. O next start em produção faz require()
# de node_modules/.next/server sob demanda a cada requisição (não só no boot), então
# sobrescrever esses arquivos com o processo antigo no ar foi a causa das 3 quebras de
# 500 em chunk JS de 2026-07-13 (next build troca os nomes com hash de conteúdo e apaga
# os antigos enquanto o servidor e o cache do navegador ainda apontam pra eles).
#
# Uso: /opt/opussb/deploy-frontend.sh [--dry-run]
#   --dry-run: builda isolado, roda o smoke test e troca o symlink `current`,
#              mas PARA antes do systemctl restart. Não toca no processo que
#              está servindo produção agora. Útil pra validar o pipeline sem
#              reiniciar nada.
set -euo pipefail

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "rode como root (precisa de systemctl restart opussb-web)" >&2
  exit 1
fi

REPO=/opt/opussb
DEPLOY_ROOT=/opt/opussb-deploy
RELEASES="$DEPLOY_ROOT/releases"
CURRENT="$DEPLOY_ROOT/current"
LOG="$DEPLOY_ROOT/deploy.log"
LOCK="$DEPLOY_ROOT/.deploy.lock"
KEEP_RELEASES=2 # além da que acabou de ir ao ar
MIN_FREE_KB=$((5 * 1024 * 1024)) # piso de 5GB livres antes de buildar
SMOKE_PORT=3001
NODE_BIN=/root/.nvm/versions/node/v20.20.2/bin/node
MAINT_FLAG=/var/lib/opussb/maintenance.on
SMOKE_PID=""

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG" >&2; }

cleanup() {
  if [ -n "$SMOKE_PID" ]; then
    kill "$SMOKE_PID" 2>/dev/null || true
    wait "$SMOKE_PID" 2>/dev/null || true
  fi
}

# Verifica se um dado alvo (produção real ou 127.0.0.1 direto, se a manutenção
# programada estiver ligada) responde 200 em /login, com retry curto.
verify_up() {
  local target="$1" tries=0
  while [ "$tries" -lt 20 ]; do
    if curl -fsk "$target" >/dev/null 2>&1; then return 0; fi
    tries=$((tries + 1))
    sleep 0.5
  done
  return 1
}

verify_target() {
  if [ -f "$MAINT_FLAG" ]; then
    echo "http://127.0.0.1:3000/login"
  else
    echo "https://sbclips.space/login"
  fi
}

# Reaponta `current` pra uma release anterior e reinicia. Se isso também falhar,
# para de tentar (nunca looping infinito) e grita bem alto pra intervenção manual.
rollback_to() {
  local target="$1"
  if [ -z "$target" ]; then
    log "ROLLBACK IMPOSSÍVEL: não havia release anterior (primeiro deploy). Produção pode estar quebrada, intervenção manual necessária AGORA."
    return 1
  fi
  log "iniciando rollback para $target"
  ln -sfn "$target" "$DEPLOY_ROOT/current.tmp"
  mv -Tf "$DEPLOY_ROOT/current.tmp" "$CURRENT"
  systemctl reset-failed opussb-web 2>/dev/null || true
  if ! timeout 60 systemctl restart opussb-web; then
    log "ROLLBACK FALHOU AO REINICIAR. current aponta pra $target mas o serviço não subiu. Intervenção manual necessária AGORA: journalctl -u opussb-web -n 100"
    return 1
  fi
  if ! verify_up "$(verify_target)"; then
    log "ROLLBACK TAMBÉM FALHOU NA VERIFICAÇÃO. Produção pode estar fora do ar. Intervenção manual necessária AGORA. Comando manual: ln -sfn $target $CURRENT && systemctl reset-failed opussb-web && systemctl restart opussb-web"
    return 1
  fi
  log "rollback OK, current de volta para $target"
  return 0
}

# Mantém a release atual + as KEEP_RELEASES anteriores; poda o resto via
# `git worktree remove` (nunca rm -rf cru, que deixa metadado órfão em .git/worktrees
# e o git continua "achando" que a worktree existe).
prune_releases() {
  local all total drop
  all=$(ls -1 "$RELEASES" 2>/dev/null | sort)
  total=$(printf '%s\n' "$all" | grep -c . || true)
  drop=$((total - 1 - KEEP_RELEASES))
  if [ "$drop" -le 0 ]; then return 0; fi
  printf '%s\n' "$all" | head -n "$drop" | while IFS= read -r name; do
    [ -z "$name" ] && continue
    local path="$RELEASES/$name"
    if git -C "$REPO" worktree remove --force "$path" 2>>"$LOG"; then
      log "release podada: $path"
    else
      log "falha ao podar $path, deixei pra remoção manual depois"
    fi
  done
  git -C "$REPO" worktree prune 2>>"$LOG" || true
}

install -d -m 755 "$RELEASES"
trap cleanup EXIT

exec 200>"$LOCK"
if ! flock -n 200; then
  echo "deploy já em andamento (lock $LOCK), abortando" >&2
  exit 1
fi

# --- 1. Preflight na árvore viva ---------------------------------------------
# Escopado a frontend/: é o que o worktree isolado materializa e builda. Sujeira
# em outras partes do repo (docs, assets, scratch) não afeta o build e não deve
# bloquear o deploy à toa.
cd "$REPO"
if [ -n "$(git status --porcelain -- frontend)" ]; then
  echo "frontend/ em $REPO tem mudança não commitada. Commit primeiro: o deploy só" >&2
  echo "materializa o que está commitado no SHA atual, então deployar com o frontend" >&2
  echo "sujo subiria silenciosamente SEM a mudança pendente." >&2
  exit 1
fi

SHA=$(git rev-parse --short=7 HEAD)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
REL="$RELEASES/${STAMP}-${SHA}"

FREE_KB=$(df --output=avail -k "$DEPLOY_ROOT" | tail -1 | tr -d ' ')
if [ "$FREE_KB" -lt "$MIN_FREE_KB" ]; then
  echo "espaço livre abaixo do piso de 5GB em $DEPLOY_ROOT, abortando" >&2
  exit 1
fi

log "iniciando deploy do commit $SHA em $REL"

# --- 2. Worktree isolada, por SHA e detached (nunca por nome de branch: git recusa
# duas worktrees na mesma branch, e /opt/opussb pode estar em qualquer branch) --------
git worktree add --detach "$REL" "$SHA" >>"$LOG" 2>&1

# --- 3. Build isolado: não toca a árvore viva nem o processo em produção -----
(
  # shellcheck disable=SC1091
  source /root/.nvm/nvm.sh
  nvm use 20 >/dev/null
  cd "$REL/frontend"
  npm ci >>"$LOG" 2>&1
  npm run build >>"$LOG" 2>&1
)
log "build isolado ok"

# --- 4. Smoke test isolado, porta descartável, sempre em 127.0.0.1 -----------
# (contra o release NOVO diretamente, current ainda aponta pro release velho aqui)
# O `next start` resolve `.next` relativo ao cwd do processo, não ao caminho do
# binário, então precisa rodar com cwd dentro do release. `exec` dentro do
# subshell troca o processo do subshell pelo do node, então $! mata o processo
# certo direto (sem deixar node órfão).
(
  cd "$REL/frontend"
  exec env NODE_ENV=production "$NODE_BIN" node_modules/next/dist/bin/next \
    start -H 127.0.0.1 -p "$SMOKE_PORT"
) >>"$LOG" 2>&1 &
SMOKE_PID=$!

if ! verify_up "http://127.0.0.1:$SMOKE_PORT/login"; then
  log "smoke test: release novo não respondeu em :$SMOKE_PORT, abortando (produção não foi tocada)"
  exit 1
fi

HTML=$(curl -fs "http://127.0.0.1:$SMOKE_PORT/login")
CHUNKS=$(printf '%s' "$HTML" | grep -oE '/_next/static/(chunks|media)/[a-zA-Z0-9_.-]+\.(js|css|woff2?)' | sort -u | head -3)
if [ -z "$CHUNKS" ]; then
  log "smoke test: nenhum asset _next/static encontrado no HTML de /login, abortando (produção não foi tocada)"
  exit 1
fi
while IFS= read -r asset; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$SMOKE_PORT$asset")
  if [ "$code" != "200" ]; then
    log "smoke test: asset $asset voltou $code, abortando (produção não foi tocada)"
    exit 1
  fi
done <<<"$CHUNKS"

kill "$SMOKE_PID" 2>/dev/null || true
wait "$SMOKE_PID" 2>/dev/null || true
SMOKE_PID=""
log "smoke test ok, release $REL aprovada para ir ao ar"

# --- 5. Troca atômica do symlink ---------------------------------------------
PREV=""
[ -e "$CURRENT" ] && PREV=$(readlink -f "$CURRENT")

ln -sfn "$REL/frontend" "$DEPLOY_ROOT/current.tmp"
mv -Tf "$DEPLOY_ROOT/current.tmp" "$CURRENT"
log "current agora aponta para $REL/frontend (anterior: ${PREV:-nenhum, primeiro deploy})"

if [ "$DRY_RUN" -eq 1 ]; then
  log "--dry-run: parando antes do systemctl restart. Produção não foi tocada."
  echo
  echo "dry-run OK: commit $SHA buildado e validado em $REL"
  echo "current aponta pra ele, mas o opussb-web NÃO foi reiniciado (produção segue no ar como estava)."
  echo "log completo: $LOG"
  exit 0
fi

# --- 6. Restart (inevitável: next start carrega o manifest só no boot) -------
systemctl reset-failed opussb-web 2>/dev/null || true
if ! timeout 60 systemctl restart opussb-web; then
  log "systemctl restart falhou"
  rollback_to "$PREV" || true
  exit 1
fi

# --- 7. Verificação pós-restart pela via real, com desvio se a manutenção -----
# programada estiver ligada (senão o rollback automático dispara por falso positivo)
if ! verify_up "$(verify_target)"; then
  log "verificação pós-restart falhou"
  rollback_to "$PREV" || true
  exit 1
fi

log "deploy do commit $SHA CONFIRMADO no ar (release $REL)"

# --- 8. Poda de releases antigas, só depois de sucesso confirmado -----------
prune_releases

echo
echo "deploy OK: commit $SHA em $REL"
echo "rollback manual se precisar depois: ln -sfn ${PREV:-<release-anterior>} $CURRENT && systemctl reset-failed opussb-web && systemctl restart opussb-web"
echo "log completo: $LOG"
