#!/usr/bin/env bash
# Runs only when the PostgreSQL data volume is initialized for the first time.
# The official postgres image sources non-executable *.sh files as well.
set -e

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${RESEARCH_DB_NAME:?RESEARCH_DB_NAME is required}"
: "${RESEARCH_DB_USER:?RESEARCH_DB_USER is required}"
: "${RESEARCH_DB_PASSWORD:?RESEARCH_DB_PASSWORD is required}"

echo "[bootstrap] creating research role/database if needed"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=research_db="$RESEARCH_DB_NAME" \
  --set=research_user="$RESEARCH_DB_USER" \
  --set=research_password="$RESEARCH_DB_PASSWORD" <<'EOSQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'research_user', :'research_password')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = :'research_user'
)
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'research_db', :'research_user')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = :'research_db'
)
\gexec
EOSQL

echo "[bootstrap] applying /bootstrap/schema.sql to $RESEARCH_DB_NAME"

PGPASSWORD="$RESEARCH_DB_PASSWORD" psql -v ON_ERROR_STOP=1 \
  --username "$RESEARCH_DB_USER" \
  --dbname "$RESEARCH_DB_NAME" \
  --file /bootstrap/schema.sql

echo "[bootstrap] research database ready"
