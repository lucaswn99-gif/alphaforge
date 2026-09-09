#!/usr/bin/env bash
# Deploy do Alphaforge no droplet. Idempotente e com rollback.
#
#     ./deploy.sh
#
# Roda no servidor, disparado pelo GitHub Actions a cada push na main — ou à
# mão, se preferir. O ponto é não deixar o serviço no ar quebrado: se a versão
# nova não responder ao health check, ele volta para o commit anterior sozinho.
set -euo pipefail

PASTA="${ALPHAFORGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
SERVICO="${ALPHAFORGE_SERVICE:-alphaforge}"
SAUDE="${ALPHAFORGE_HEALTH:-http://127.0.0.1:8000/health}"
ESPERA="${ALPHAFORGE_WAIT:-20}"

cd "$PASTA"

anterior="$(git rev-parse HEAD)"
echo "[deploy] commit atual: ${anterior:0:8}"

git fetch origin main
novo="$(git rev-parse origin/main)"
if [ "$anterior" = "$novo" ]; then
  echo "[deploy] nada novo na main; saindo sem reiniciar."
  exit 0
fi

echo "[deploy] atualizando para ${novo:0:8}"
git reset --hard origin/main

# Dependência nova só entra se o requirements mudou: pip install a cada deploy
# custa minuto e derruba o serviço por mais tempo do que precisa.
if ! git diff --quiet "$anterior" "$novo" -- requirements.txt; then
  echo "[deploy] requirements.txt mudou — instalando"
  if [ -x "venv/bin/pip" ]; then venv/bin/pip install -r requirements.txt
  else pip3 install -r requirements.txt; fi
fi

reiniciar() { sudo systemctl restart "$SERVICO"; }
saudavel() {
  for _ in $(seq 1 "$ESPERA"); do
    if curl -fsS --max-time 3 "$SAUDE" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

echo "[deploy] reiniciando $SERVICO"
reiniciar

if saudavel; then
  echo "[deploy] OK — no ar em ${novo:0:8}"
  curl -fsS "$SAUDE" || true
  echo
  exit 0
fi

# O health check não toca em fonte externa de propósito: se ele falhou, o
# problema é o próprio serviço, não o Yahoo oscilando.
echo "[deploy] FALHOU o health check — voltando para ${anterior:0:8}"
git reset --hard "$anterior"
reiniciar
if saudavel; then
  echo "[deploy] rollback concluído; o site continua no ar na versão anterior."
else
  echo "[deploy] ATENCAO: rollback feito mas o servico segue sem responder."
fi
exit 1
