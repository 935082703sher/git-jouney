#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/bootstrap.sh
python3 -m venv .venv
.venv/bin/pip install --no-cache-dir -r apps/backend/requirements.lock
npm ci --prefix apps/admin-web --cache /tmp/aho-npm-cache
if [ ! -e apps/backend/.env ]; then ln -s ../../.env apps/backend/.env; fi
docker compose up -d postgres --wait
(
  cd apps/backend
  ../../.venv/bin/alembic upgrade head
  ../../.venv/bin/python -m aho.seed
)
npm run build --prefix apps/admin-web
