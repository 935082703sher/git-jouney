#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/bootstrap.sh
export BUILDX_CONFIG="${BUILDX_CONFIG:-/tmp/aho-buildx}"
docker compose -f docker-compose.yml -f infrastructure/compose.cloud.yml up -d --build --wait
curl --fail --silent http://127.0.0.1:8000/health/ready
