#!/usr/bin/env bash
# Backup without importing deployment credentials into this shell. A completed
# directory is visible only after dumps, checksums, and explicit publish pass.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ENV_FILE="$ROOT_DIR/.env"
PROJECT="douyin-research-platform"
OUTPUT_ROOT="$ROOT_DIR/backups"
PUBLISH_EXECUTABLE=""
RUN_PUBLISH=0

usage() {
  cat <<'EOF'
Usage: scripts/backup.sh [options]
  --env-file <path>             Docker Compose env file (default: .env)
  --project <name>              Compose project (default: compose file name)
  --output-root <directory>     Parent for timestamped backups (default: ./backups)
  --publish-executable <path>   Optional executable; never runs without --publish
  --publish                     Explicitly run the configured publisher

Compatibility: `bash scripts/backup.sh` uses the repository Compose file, its
declared project name, .env and ./backups. The env file is never sourced or
printed and must be mode 0600.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --output-root) OUTPUT_ROOT="${2:-}"; shift 2 ;;
    --publish-executable) PUBLISH_EXECUTABLE="${2:-}"; shift 2 ;;
    --publish) RUN_PUBLISH=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

require_nonempty() { [[ -n "$2" ]] || { echo "ERROR: --$1 requires a value." >&2; exit 2; }; }
require_nonempty env-file "$ENV_FILE"
require_nonempty output-root "$OUTPUT_ROOT"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: env file does not exist." >&2; exit 2; }
[[ "$PROJECT" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo "ERROR: project contains unsupported characters." >&2; exit 2; }
[[ "$RUN_PUBLISH" != 1 || -n "$PUBLISH_EXECUTABLE" ]] || { echo "ERROR: --publish requires --publish-executable." >&2; exit 2; }
[[ -z "$PUBLISH_EXECUTABLE" || -x "$PUBLISH_EXECUTABLE" ]] || { echo "ERROR: publish executable is not executable." >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is required." >&2; exit 2; }

file_mode() {
  if stat -c '%a' "$1" >/dev/null 2>&1; then stat -c '%a' "$1"; else stat -f '%Lp' "$1"; fi
}
[[ "$(file_mode "$ENV_FILE")" == "600" ]] || { echo "ERROR: env file must have mode 0600." >&2; exit 2; }

# This is deliberately a narrow data parser, not `source`: only non-secret DB
# identifiers are read, and unsafe values are rejected before they reach docker.
env_name_or_default() {
  local key="$1" fallback="$2" value
  value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit}' "$ENV_FILE")"
  [[ -n "$value" ]] || value="$fallback"
  [[ "$value" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "ERROR: $key in env file is not a safe database identifier." >&2; exit 2; }
  printf '%s' "$value"
}
POSTGRES_USER="$(env_name_or_default POSTGRES_USER postgres)"
POSTGRES_DB="$(env_name_or_default POSTGRES_DB windmill)"
RESEARCH_DB_NAME="$(env_name_or_default RESEARCH_DB_NAME douyin_research)"

mkdir -p "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd -P)"
[[ "$OUTPUT_ROOT" != "/" ]] || { echo "ERROR: output root cannot be /." >&2; exit 2; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_DIR="$OUTPUT_ROOT/$STAMP"
STAGE_DIR="$OUTPUT_ROOT/.incomplete-$STAMP-$$"
LOCK_DIR="$OUTPUT_ROOT/.backup.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then echo "ERROR: another backup holds the output-root lock." >&2; exit 1; fi
cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  rm -rf "$STAGE_DIR"
  rmdir "$LOCK_DIR" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[[ ! -e "$FINAL_DIR" ]] || { echo "ERROR: timestamped backup directory already exists." >&2; exit 1; }
mkdir "$STAGE_DIR"
chmod 700 "$STAGE_DIR"
compose() {
  local args=(docker compose --project-directory "$ROOT_DIR" --env-file "$ENV_FILE" -f "$ROOT_DIR/docker-compose.yml")
  if [[ -n "$PROJECT" ]]; then args+=(--project-name "$PROJECT"); fi
  "${args[@]}" "$@"
}
dump_to() {
  local label="$1" file="$2"; shift 2
  echo "[backup] $label"
  compose exec -T postgres "$@" > "$file"
  chmod 600 "$file"
  [[ -s "$file" ]] || { echo "ERROR: $label produced an empty dump." >&2; exit 1; }
}
checksum() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
  else echo "ERROR: sha256sum or shasum is required." >&2; exit 2; fi
}

dump_to "globals" "$STAGE_DIR/globals.sql" pg_dumpall -U "$POSTGRES_USER" --globals-only
dump_to "Windmill database" "$STAGE_DIR/windmill.dump" pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"
dump_to "research database" "$STAGE_DIR/douyin_research.dump" pg_dump -U "$POSTGRES_USER" -Fc "$RESEARCH_DB_NAME"
{
  printf 'format=windmill-research-backup-v2\ncreated_at_utc=%s\ncompose_project=%s\npostgres_db=%s\nresearch_db=%s\n' "$STAMP" "$PROJECT" "$POSTGRES_DB" "$RESEARCH_DB_NAME"
  printf 'globals.sql.sha256=%s\n' "$(checksum "$STAGE_DIR/globals.sql")"
  printf 'windmill.dump.sha256=%s\n' "$(checksum "$STAGE_DIR/windmill.dump")"
  printf 'douyin_research.dump.sha256=%s\n' "$(checksum "$STAGE_DIR/douyin_research.dump")"
} > "$STAGE_DIR/manifest.txt"
chmod 600 "$STAGE_DIR/manifest.txt"

if [[ -n "$PUBLISH_EXECUTABLE" && "$RUN_PUBLISH" != 1 ]]; then echo "[backup] publish executable configured but not run (pass --publish explicitly)."; fi
if [[ "$RUN_PUBLISH" == 1 ]]; then echo "[backup] publishing verified candidate with explicit executable."; "$PUBLISH_EXECUTABLE" "$STAGE_DIR"; fi
if mv --help 2>&1 | grep -q -- '--no-target-directory'; then
  mv -T -- "$STAGE_DIR" "$FINAL_DIR"
else
  [[ ! -e "$FINAL_DIR" ]] || { echo "ERROR: completed backup destination appeared before publish." >&2; exit 1; }
  mv "$STAGE_DIR" "$FINAL_DIR"
fi
[[ -f "$FINAL_DIR/manifest.txt" && ! -e "$FINAL_DIR/$(basename "$STAGE_DIR")" ]] || {
  echo "ERROR: backup publish postcondition failed." >&2
  exit 1
}
echo "[backup] complete: $FINAL_DIR"
