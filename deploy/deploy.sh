#!/usr/bin/env bash
# Ship the current checkout to the paidway-prod VM and restart. Usage: deploy/deploy.sh [ssh-host]
set -euo pipefail
HOST=${1:-paidway-prod}
cd "$(dirname "$0")/.."
(cd frontend && npm run build)
rsync -az --delete --exclude .git --exclude .venv --exclude node_modules --exclude '*.sqlite3' \
  --exclude backend/data/nessie_ids.json --exclude .env --exclude deploy/paidway.env --exclude __pycache__ \
  ./ "$HOST":/opt/paidway/
ssh "$HOST" 'cd /opt/paidway/backend && uv sync --frozen --no-dev -q && sudo systemctl restart paidway-api'
