#!/usr/bin/env bash
# maintenance — break-glass da manutenção programada do SB Clips (via SSH/root).
# Opera o MESMO flag file que o botão "Modo manutenção" do painel admin (Flask,
# POST /api/admin/maintenance) e que o nginx lê a cada request (if -f): existe =
# todo mundo fora da allowlist de IP (geo em /etc/nginx/conf.d/00-sb-maintenance.conf)
# vê a tela 503; /api e /health nunca são bloqueados. Sem reload, sem restart:
# criar/remover o arquivo tem efeito imediato. Ver BACKEND_CONTEXT §43.
# Instalado em /usr/local/bin/maintenance (cópia deste arquivo do repo).
set -euo pipefail

FLAG=/var/lib/opussb/maintenance.on

case "${1:-}" in
  on)
    # Auto-suficiente pra disaster-recovery/restore: cria /var/lib/opussb se faltar
    # (o backup do deploy é só db+código). Idempotente, roda como root.
    install -d -m 755 -o www-data -g www-data /var/lib/opussb
    # -o www-data: o Flask (gunicorn roda como www-data) precisa conseguir apagar
    # o flag pelo botão do painel mesmo quando foi o root que ligou por aqui.
    # (O unlink exige escrita no DIRETÓRIO, que já é do www-data — o chown do
    # arquivo é higiene pra auditoria não sugerir dono errado.)
    install -m 644 -o www-data -g www-data /dev/null "$FLAG"
    printf 'ligada via break-glass (root) em %s\n' "$(date -Is)" > "$FLAG"
    echo "manutenção LIGADA (flag: $FLAG)"
    ;;
  off)
    rm -f "$FLAG"
    echo "manutenção desligada"
    ;;
  status)
    if [ -f "$FLAG" ]; then
      echo "manutenção: LIGADA — $(cat "$FLAG" 2>/dev/null || echo '(flag vazio)')"
    else
      echo "manutenção: desligada"
    fi
    ;;
  *)
    echo "uso: maintenance on|off|status" >&2
    exit 2
    ;;
esac
