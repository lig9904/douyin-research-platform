#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${POSTGRES_USER:=postgres}"
: "${RESEARCH_DB_NAME:=douyin_research}"
: "${WINDMILL_PORT:=8000}"
: "${WINDMILL_BIND_HOST:=127.0.0.1}"

echo "[verify] docker compose services"
docker compose ps

echo "[verify] Windmill version endpoint"
curl -fsS "http://${WINDMILL_BIND_HOST}:${WINDMILL_PORT}/api/version"
echo

echo "[verify] research database tables"
docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$RESEARCH_DB_NAME" \
  -Atc "select count(*) from pg_catalog.pg_tables where schemaname='public';"

echo "[verify] required table: source_video"
docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$RESEARCH_DB_NAME" \
  -Atc "select to_regclass('public.source_video');"

echo "[verify] OK"
