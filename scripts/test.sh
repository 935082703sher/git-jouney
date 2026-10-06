#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d postgres --wait
# Create only the dedicated test database, preserving the application's database.
if ! docker compose exec -T postgres psql -U aho -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'aho_test'" | grep -q 1; then
  docker compose exec -T postgres createdb -U aho aho_test
fi
(
  cd apps/backend
  ../../.venv/bin/pytest -q
)
npm run build --prefix apps/admin-web
