#!/usr/bin/env bash
# Ship the working tree to the VPS and (re)start Convox there. The server keeps its own .env
# (/opt/convox/.env: CONVOX_ENV=prod, DOMAIN, PG_PASSWORD, real engines); this never copies one.
set -euo pipefail
HOST=${CONVOX_HOST:-root@187.127.185.75}
cd "$(dirname "$0")/.."
rsync -az --delete \
  --exclude .git --exclude .venv --exclude out --exclude logs --exclude .env \
  --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache --exclude .claude \
  ./ "$HOST:/opt/convox/"
ssh "$HOST" 'cd /opt/convox && docker compose -f deploy/compose.yml --env-file .env up -d --build app'
