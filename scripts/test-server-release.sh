#!/usr/bin/env bash
# Controlled release operations for a reviewed test server.  This script never
# reads an env file as shell code and never picks a Docker project implicitly.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BASE_COMPOSE="$ROOT_DIR/docker-compose.yml"
SERVER_COMPOSE="$ROOT_DIR/docker-compose.test-server.yml"
RESTORE_DATABASE="test_server_research_restore"
RESTORE_WINDMILL_DATABASE="test_server_windmill_restore"

usage() {
  cat <<'EOF'
Usage: scripts/test-server-release.sh <backup|migrate|verify|restore-drill> \
  --env-file <path> --project <compose-project> --backup-root <absolute-path> [backup-directory]

Every invocation requires an explicit ignored env file, Compose project, and
backup root.  migrate requires TEST_SERVER_RESEARCH_MIGRATE=YES; restore-drill
requires TEST_SERVER_RESTORE_DRILL=YES.  restore-drill accepts exactly one
backup directory created by this script and restores only into the fixed,
temporary research and Windmill databases, which are dropped afterwards.
EOF
}

command_name="${1:-}"
[[ -n "$command_name" ]] || { usage >&2; exit 2; }
shift || true
env_file=""
project=""
backup_root=""
backup_argument=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --env-file) env_file="${2:-}"; shift 2 ;;
    --project) project="${2:-}"; shift 2 ;;
    --backup-root) backup_root="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *)
      if [[ -z "$backup_argument" && "$command_name" == "restore-drill" ]]; then
        backup_argument="$1"; shift
      else
        printf 'ERROR: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2
      fi
      ;;
  esac
done

[[ -f "$env_file" ]] || { echo 'ERROR: --env-file must name an existing file.' >&2; exit 2; }
[[ -n "$project" && "$project" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || {
  echo 'ERROR: --project must be an explicit lowercase Compose project name.' >&2; exit 2;
}
[[ "$backup_root" = /* && -d "$backup_root" ]] || {
  echo 'ERROR: --backup-root must be an existing absolute directory.' >&2; exit 2;
}
backup_root="$(cd "$backup_root" && pwd -P)"
[[ "$backup_root" != / && "$backup_root" != "$ROOT_DIR" ]] || {
  echo 'ERROR: --backup-root must not be / or the repository root.' >&2; exit 2;
}
env_mode="$(stat -c '%a' "$env_file" 2>/dev/null || stat -f '%Lp' "$env_file")"
[[ "$env_mode" == 600 ]] || { echo 'ERROR: --env-file must have exact mode 0600.' >&2; exit 2; }
backup_mode="$(stat -c '%a' "$backup_root" 2>/dev/null || stat -f '%Lp' "$backup_root")"
backup_owner="$(stat -c '%u' "$backup_root" 2>/dev/null || stat -f '%u' "$backup_root")"
[[ "$backup_owner" == "$(id -u)" && "$backup_mode" == 700 && -w "$backup_root" && -x "$backup_root" ]] || {
  echo 'ERROR: --backup-root must be owned by the current user, writable, and exact mode 0700.' >&2; exit 2;
}

# Parse only the documented KEY=value format.  Do not source deployment files:
# that would execute arbitrary shell from a secret-bearing file.
env_value() {
  local key="$1" value
  value="$(awk -F= -v wanted="$key" '$1 == wanted { sub(/^[^=]*=/, ""); print; exit }' "$env_file")"
  [[ -n "$value" && "$value" != CHANGE_ME* ]] || {
    printf 'ERROR: %s is missing or still a placeholder in --env-file.\n' "$key" >&2; exit 2;
  }
  printf '%s' "$value"
}

for required in POSTGRES_USER POSTGRES_DB RESEARCH_DB_NAME RESEARCH_DB_USER; do
  [[ "$(awk -F= -v wanted="$required" '$1 == wanted {count++} END {print count + 0}' "$env_file")" == 1 ]] || {
    printf 'ERROR: %s must occur exactly once in --env-file.\n' "$required" >&2; exit 2;
  }
  env_value "$required" >/dev/null
done
research_database="$(env_value RESEARCH_DB_NAME)"
research_user="$(env_value RESEARCH_DB_USER)"
windmill_database="$(env_value POSTGRES_DB)"
postgres_user="$(env_value POSTGRES_USER)"
for identifier in "$research_database" "$research_user" "$windmill_database" "$postgres_user"; do
  [[ "$identifier" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || {
    echo 'ERROR: database and role names must be simple lowercase SQL identifiers.' >&2; exit 2;
  }
done
[[ "$research_database" != "$windmill_database" && "$RESTORE_DATABASE" != "$research_database" && "$RESTORE_DATABASE" != "$windmill_database" && "$RESTORE_WINDMILL_DATABASE" != "$research_database" && "$RESTORE_WINDMILL_DATABASE" != "$windmill_database" ]] || {
  echo 'ERROR: database names collide with the fixed restore-drill database.' >&2; exit 2;
}

compose() {
  docker compose --project-name "$project" --env-file "$env_file" \
    -f "$BASE_COMPOSE" -f "$SERVER_COMPOSE" "$@"
}

wait_for_postgres() {
  local attempt
  for attempt in $(seq 1 30); do
    if compose exec -T postgres pg_isready -U "$postgres_user" -d "$windmill_database" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo 'ERROR: PostgreSQL did not become ready in the explicitly selected Compose project.' >&2
  exit 1
}

research_query() {
  local database="$1" query="$2"
  compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -At -v ON_ERROR_STOP=1 -U "$RESEARCH_DB_USER" -d "$1" -c "$2"' \
    bash "$database" "$query"
}

admin_query() {
  local database="$1" query="$2"
  compose exec -T postgres psql -At -v ON_ERROR_STOP=1 -U "$postgres_user" -d "$database" -c "$query"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

write_checksums() {
  local file
  : > SHA256SUMS
  for file in "$@"; do printf '%s  %s\n' "$(sha256_file "$file")" "$file" >> SHA256SUMS; done
}

check_checksums() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum -c SHA256SUMS >/dev/null
  else shasum -a 256 -c SHA256SUMS >/dev/null; fi
}

create_backup() (
  local stamp destination temporary lock_dir
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  destination="$backup_root/$stamp"
  [[ ! -e "$destination" ]] || { echo 'ERROR: refusing to overwrite an existing backup directory.' >&2; exit 1; }
  lock_dir="$backup_root/.test-server-release-backup.lock"
  mkdir "$lock_dir" 2>/dev/null || { echo 'ERROR: another backup is already running for this backup root.' >&2; exit 1; }
  temporary="$backup_root/.${stamp}.incomplete.$$"
  cleanup_backup() { rm -rf "$temporary"; rmdir "$lock_dir" 2>/dev/null || true; }
  trap cleanup_backup EXIT
  mkdir "$temporary"
  chmod 700 "$temporary"
  # Dumps are streamed out of the container; neither database password nor URL
  # is written into the backup directory or process output.
  compose exec -T postgres pg_dumpall -U "$postgres_user" --globals-only > "$temporary/globals.sql"
  compose exec -T postgres pg_dump -U "$postgres_user" -Fc "$windmill_database" > "$temporary/windmill.dump"
  compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec pg_dump -U "$RESEARCH_DB_USER" -Fc "$RESEARCH_DB_NAME"' \
    > "$temporary/research.dump"
  chmod 600 "$temporary/globals.sql" "$temporary/windmill.dump" "$temporary/research.dump"
  (cd "$temporary" && write_checksums globals.sql windmill.dump research.dump)
  printf 'created_at_utc=%s\ncompose_project=%s\nresearch_database=%s\nwindmill_database=%s\n' \
    "$stamp" "$project" "$research_database" "$windmill_database" > "$temporary/manifest.txt"
  chmod 600 "$temporary/manifest.txt" "$temporary/SHA256SUMS"
  # The test-server host is Linux (enforced by the host preflight), where GNU
  # mv -T fails instead of nesting if a destination appears during the rename.
  # The guarded fallback keeps this command locally testable on macOS; the
  # 0700 current-user-owned root and lock exclude other deployment processes.
  if mv --help 2>&1 | grep -q -- '--no-target-directory'; then
    mv -T -- "$temporary" "$destination"
  else
    [[ ! -e "$destination" ]] || { echo 'ERROR: backup destination appeared before publish.' >&2; exit 1; }
    mv "$temporary" "$destination"
  fi
  trap - EXIT
  rmdir "$lock_dir"
  printf '%s\n' "$destination"
)

preflight_migration() {
  local duplicates invalid_items has_account
  duplicates="$(research_query "$research_database" "select count(*) from (select created_by, lower(name) from collection group by created_by, lower(name) having count(*) > 1) duplicate_names")"
  [[ "$duplicates" == 0 ]] || { echo 'ERROR: duplicate collection owner/name values must be resolved before migration.' >&2; exit 1; }
  has_account="$(research_query "$research_database" "select exists(select 1 from information_schema.columns where table_schema='public' and table_name='collection_item' and column_name='account_id')")"
  if [[ "$has_account" == t ]]; then
    invalid_items="$(research_query "$research_database" "select count(*) from collection_item where (video_id is not null)::int + (account_id is not null)::int + (signal_id is not null)::int <> 1")"
  else
    invalid_items="$(research_query "$research_database" "select count(*) from collection_item where (video_id is not null)::int + (signal_id is not null)::int <> 1")"
  fi
  [[ "$invalid_items" == 0 ]] || { echo 'ERROR: invalid collection targets must be resolved before migration.' >&2; exit 1; }
}

verify_migration_ledger() {
  # Before migration, the deployed ledger may be a contiguous prefix of the
  # reviewed repository. Every recorded filename/hash and its order must still
  # match. After migration, `required` demands the complete reviewed set.
  local mode="${1:-prefix}" migration expected actual prefix ledger_exists ledger_row actual_count
  ledger_exists="$(research_query "$research_database" "select to_regclass('public.schema_migrations') is not null")"
  if [[ "$ledger_exists" != t ]]; then
    [[ "$mode" != required ]] && return 0
    echo 'ERROR: schema_migrations ledger is missing; run the reviewed migration first.' >&2
    exit 1
  fi
  expected="$(mktemp)"
  actual="$(mktemp)"
  prefix="$(mktemp)"
  for migration in "$ROOT_DIR"/db/migrations/*.sql; do
    printf '%s|%s\n' "$(basename "$migration")" "$(sha256_file "$migration")" >> "$expected"
  done
  research_query "$research_database" "select filename || '|' || sha256 from schema_migrations order by filename" > "$actual"
  sort -o "$expected" "$expected"
  while IFS= read -r ledger_row; do
    if ! grep -Fqx -- "$ledger_row" "$expected"; then
      rm -f "$expected" "$actual" "$prefix"
      echo 'ERROR: schema_migrations ledger contains an unknown filename or changed SHA-256.' >&2
      exit 1
    fi
  done < "$actual"
  actual_count="$(wc -l < "$actual" | tr -d ' ')"
  head -n "$actual_count" "$expected" > "$prefix"
  if ! diff -u "$prefix" "$actual" >/dev/null; then
    rm -f "$expected" "$actual" "$prefix"
    echo 'ERROR: schema_migrations ledger is not a contiguous reviewed prefix.' >&2
    exit 1
  fi
  if [[ "$mode" == required ]] && ! diff -u "$expected" "$actual" >/dev/null; then
    rm -f "$expected" "$actual" "$prefix"
    echo 'ERROR: schema_migrations ledger differs from checked-in migration SHA-256 values.' >&2
    exit 1
  fi
  rm -f "$expected" "$actual" "$prefix"
}

verify_contract() {
  local verified
  verified="$(research_query "$research_database" "select (to_regclass('public.saved_research_filter') is not null)::int || '|' || (to_regclass('public.research_user_action') is not null)::int || '|' || exists(select 1 from information_schema.columns where table_schema='public' and table_name='collection_item' and column_name='account_id')::int || '|' || exists(select 1 from pg_constraint where conname='collection_item_one_target')::int || '|' || (to_regclass('public.uq_collection_owner_name') is not null)::int || '|' || (select bool_and(tableowner=current_user) from pg_tables where schemaname='public' and tablename in ('saved_research_filter','research_user_action'))::int || '|' || (has_table_privilege(current_user,'public.saved_research_filter','SELECT') and has_table_privilege(current_user,'public.saved_research_filter','INSERT') and has_table_privilege(current_user,'public.saved_research_filter','UPDATE') and has_table_privilege(current_user,'public.research_user_action','SELECT') and has_table_privilege(current_user,'public.research_user_action','INSERT') and has_table_privilege(current_user,'public.research_user_action','UPDATE'))::int")"
  [[ "$verified" == '1|1|1|1|1|1|1' ]] || { echo 'ERROR: research migration owner, privilege, or object verification failed.' >&2; exit 1; }
}

cmd_backup() { wait_for_postgres; printf 'BACKUP %s\n' "$(create_backup)"; }

cmd_migrate() {
  [[ "${TEST_SERVER_RESEARCH_MIGRATE:-}" == YES ]] || { echo 'ERROR: set TEST_SERVER_RESEARCH_MIGRATE=YES for this test-server migration.' >&2; exit 2; }
  wait_for_postgres
  preflight_migration
  verify_migration_ledger
  local backup_dir migration
  backup_dir="$(create_backup)"
  {
    printf '%s\n' '\set ON_ERROR_STOP on' 'begin;'
    printf '%s\n' 'create table if not exists schema_migrations (filename text primary key, sha256 text not null, applied_at timestamptz not null default now());'
    for migration in "$ROOT_DIR"/db/migrations/*.sql; do
      # Files already in a verified ledger are deliberately not replayed.
      if [[ "$(research_query "$research_database" "select exists(select 1 from schema_migrations where filename='$(basename "$migration")')" 2>/dev/null || true)" != t ]]; then
        printf '%s\n' "$(<"$migration")"
        printf "insert into schema_migrations(filename, sha256) values ('%s', '%s');\n" "$(basename "$migration")" "$(sha256_file "$migration")"
      fi
    done
    printf '%s\n' 'commit;'
  } | compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$RESEARCH_DB_USER" -d "$RESEARCH_DB_NAME"' >/dev/null
  verify_migration_ledger required
  verify_contract
  printf 'MIGRATED backup=%s\n' "$backup_dir"
}

cmd_verify() { wait_for_postgres; verify_migration_ledger required; verify_contract; echo 'VERIFIED research migration contract and ledger.'; }

cmd_restore_drill() (
  [[ "${TEST_SERVER_RESTORE_DRILL:-}" == YES ]] || { echo 'ERROR: set TEST_SERVER_RESTORE_DRILL=YES for this restore drill.' >&2; exit 2; }
  [[ -n "$backup_argument" ]] || { echo 'ERROR: restore-drill requires one backup directory.' >&2; exit 2; }
  local backup_dir source_counts restored_counts windmill_source_counts windmill_restored_counts remaining research_verified windmill_verified existing
  local research_created=0 windmill_created=0
  backup_dir="$(cd "$backup_argument" 2>/dev/null && pwd -P)" || { echo 'ERROR: backup directory does not exist.' >&2; exit 2; }
  case "$backup_dir" in "$backup_root"/*) ;; *) echo 'ERROR: restore drill accepts backups only beneath --backup-root.' >&2; exit 2;; esac
  [[ -f "$backup_dir/research.dump" && -f "$backup_dir/windmill.dump" && -f "$backup_dir/globals.sql" && -f "$backup_dir/SHA256SUMS" && -f "$backup_dir/manifest.txt" ]] || { echo 'ERROR: backup is incomplete.' >&2; exit 1; }
  grep -Fqx 'research.dump' <(awk '{print $2}' "$backup_dir/SHA256SUMS") && grep -Fqx 'windmill.dump' <(awk '{print $2}' "$backup_dir/SHA256SUMS") && grep -Fqx 'globals.sql' <(awk '{print $2}' "$backup_dir/SHA256SUMS") || { echo 'ERROR: checksum manifest does not cover every backup artifact.' >&2; exit 1; }
  [[ "$(awk -F= '$1 == "compose_project" {print $2}' "$backup_dir/manifest.txt")" == "$project" ]] || { echo 'ERROR: backup manifest Compose project does not match --project.' >&2; exit 1; }
  [[ "$(awk -F= '$1 == "research_database" {print $2}' "$backup_dir/manifest.txt")" == "$research_database" && "$(awk -F= '$1 == "windmill_database" {print $2}' "$backup_dir/manifest.txt")" == "$windmill_database" ]] || { echo 'ERROR: backup manifest database names do not match the selected env file.' >&2; exit 1; }
  (cd "$backup_dir" && check_checksums)
  wait_for_postgres
  cleanup() {
    if [[ "$research_created" == 1 ]]; then
      admin_query postgres "drop database ${RESTORE_DATABASE} with (force)" >/dev/null || true
      research_created=0
    fi
    if [[ "$windmill_created" == 1 ]]; then
      admin_query postgres "drop database ${RESTORE_WINDMILL_DATABASE} with (force)" >/dev/null || true
      windmill_created=0
    fi
  }
  trap cleanup EXIT
  existing="$(admin_query postgres "select exists(select 1 from pg_database where datname in ('${RESTORE_DATABASE}', '${RESTORE_WINDMILL_DATABASE}'))")"
  [[ "$existing" == f ]] || { echo 'ERROR: a fixed restore-drill database already exists; refusing to delete or reuse it.' >&2; exit 1; }
  admin_query postgres "create database ${RESTORE_DATABASE} owner ${research_user}" >/dev/null
  research_created=1
  admin_query postgres "create database ${RESTORE_WINDMILL_DATABASE} owner ${postgres_user}" >/dev/null
  windmill_created=1
  compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec pg_restore -U "$RESEARCH_DB_USER" --no-owner --no-privileges -d "$1"' \
    bash "$RESTORE_DATABASE" < "$backup_dir/research.dump"
  compose exec -T postgres pg_restore -U "$postgres_user" --no-owner --no-privileges -d "$RESTORE_WINDMILL_DATABASE" < "$backup_dir/windmill.dump"
  source_counts="$(research_query "$research_database" "select (select count(*) from source_video) || '|' || (select count(*) from collection) || '|' || (select count(*) from collection_item)")"
  restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from source_video) || '|' || (select count(*) from collection) || '|' || (select count(*) from collection_item)")"
  [[ "$source_counts" == "$restored_counts" ]] || { echo 'ERROR: restore-drill key-table counts do not match the source database.' >&2; exit 1; }
  windmill_source_counts="$(admin_query "$windmill_database" "select (select count(*) from workspace) || '|' || (select count(*) from usr)")"
  windmill_restored_counts="$(admin_query "$RESTORE_WINDMILL_DATABASE" "select (select count(*) from workspace) || '|' || (select count(*) from usr)")"
  [[ "$windmill_source_counts" == "$windmill_restored_counts" ]] || { echo 'ERROR: Windmill restore-drill key-table counts do not match the source database.' >&2; exit 1; }
  research_verified="$(research_query "$RESTORE_DATABASE" "select (to_regclass('public.schema_migrations') is not null)::int || '|' || (to_regclass('public.source_video') is not null)::int || '|' || (to_regclass('public.collection') is not null)::int || '|' || (to_regclass('public.collection_item') is not null)::int || '|' || (to_regclass('public.saved_research_filter') is not null)::int || '|' || (to_regclass('public.research_user_action') is not null)::int || '|' || ((select count(*) from pg_tables where schemaname='public' and tablename in ('source_video','collection','collection_item','saved_research_filter','research_user_action','schema_migrations') and tableowner=current_user)=6)::int")"
  [[ "$research_verified" == '1|1|1|1|1|1|1' ]] || { echo 'ERROR: restored research database owner or key-object verification failed.' >&2; exit 1; }
  windmill_verified="$(admin_query "$RESTORE_WINDMILL_DATABASE" "select (to_regclass('public.workspace') is not null)::int || '|' || (to_regclass('public.usr') is not null)::int || '|' || (select bool_and(tableowner=current_user) from pg_tables where schemaname='public' and tablename in ('workspace','usr'))::int")"
  [[ "$windmill_verified" == '1|1|1' ]] || { echo 'ERROR: restored Windmill database owner or key-object verification failed.' >&2; exit 1; }
  cleanup
  remaining="$(admin_query postgres "select exists(select 1 from pg_database where datname in ('${RESTORE_DATABASE}', '${RESTORE_WINDMILL_DATABASE}'))")"
  [[ "$remaining" == f ]] || { echo 'ERROR: fixed temporary restore database remains after cleanup.' >&2; exit 1; }
  trap - EXIT
  echo 'RESTORE_DRILL passed dump checksums, two isolated database restores, sentinel row counts, owners, key objects, and cleanup. globals.sql integrity was checked but role restore requires a separate disposable PostgreSQL cluster.'
)

case "$command_name" in
  backup) cmd_backup ;;
  migrate) cmd_migrate ;;
  verify) cmd_verify ;;
  restore-drill) cmd_restore_drill ;;
  *) echo "ERROR: unknown command: $command_name" >&2; usage >&2; exit 2 ;;
esac
