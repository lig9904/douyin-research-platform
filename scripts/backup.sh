#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.example to .env first." >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a
source .env
set +a

: "${POSTGRES_USER:=postgres}"
: "${POSTGRES_DB:=windmill}"
: "${RESEARCH_DB_NAME:=douyin_research}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${BACKUP_DIR:-$ROOT_DIR/backups/$STAMP}"
mkdir -p "$DEST"

echo "[backup] globals -> $DEST/globals.sql"
docker compose exec -T postgres \
  pg_dumpall -U "$POSTGRES_USER" --globals-only > "$DEST/globals.sql"

echo "[backup] Windmill -> $DEST/windmill.dump"
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$DEST/windmill.dump"

echo "[backup] research -> $DEST/douyin_research.dump"
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -Fc "$RESEARCH_DB_NAME" > "$DEST/douyin_research.dump"

printf '%s\n' \
  "created_at_utc=$STAMP" \
  "postgres_db=$POSTGRES_DB" \
  "research_db=$RESEARCH_DB_NAME" \
  "windmill_version=${WINDMILL_VERSION:-unknown}" > "$DEST/manifest.txt"

echo "[backup] complete: $DEST"
