#!/usr/bin/env bash
# Controlled release operations for a reviewed test server.  This script never
# reads an env file as shell code and never picks a Docker project implicitly.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BASE_COMPOSE="$ROOT_DIR/docker-compose.yml"
SERVER_COMPOSE="$ROOT_DIR/docker-compose.test-server.yml"
RESTORE_DATABASE="test_server_research_restore"
RESTORE_WINDMILL_DATABASE="test_server_windmill_restore"
BACKUP_VERIFY="$ROOT_DIR/scripts/test-server-backup-verify.py"

usage() {
  cat <<'EOF'
Usage: scripts/test-server-release.sh <backup|migrate|verify|restore-drill> \
  --env-file <path> --project <compose-project> --backup-root <absolute-path> \
  [--compose-overlay <docker-compose.test-server.yml|docker-compose.test-server-external-proxy.yml>] [backup-directory]

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
compose_overlay="docker-compose.test-server.yml"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --env-file) env_file="${2:-}"; shift 2 ;;
    --project) project="${2:-}"; shift 2 ;;
    --backup-root) backup_root="${2:-}"; shift 2 ;;
    --compose-overlay) compose_overlay="${2:-}"; shift 2 ;;
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

case "$compose_overlay" in
  docker-compose.test-server.yml)
    SERVER_COMPOSE="$ROOT_DIR/docker-compose.test-server.yml"
    ;;
  docker-compose.test-server-external-proxy.yml)
    SERVER_COMPOSE="$ROOT_DIR/docker-compose.test-server-external-proxy.yml"
    ;;
  *)
    echo 'ERROR: --compose-overlay must be a reviewed test-server overlay filename.' >&2
    exit 2
    ;;
esac

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

if [[ "$compose_overlay" == docker-compose.test-server-external-proxy.yml ]]; then
  "$ROOT_DIR/scripts/test-server-external-proxy-validate.sh" \
    --env-file "$env_file" --project "$project" >/dev/null
fi

compose() {
  docker compose --project-name "$project" --env-file "$env_file" \
    -f "$BASE_COMPOSE" -f "$SERVER_COMPOSE" "$@"
}

wait_for_postgres() {
  local attempt
  for attempt in $(seq 1 30); do
    if compose exec -T postgres pg_isready -h 127.0.0.1 -U "$postgres_user" -d "$windmill_database" >/dev/null 2>&1; then return 0; fi
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

# This deliberately excludes rolpassword and rolconfig.  The inventory is a
# restore-verification target, not another copy of credential material.
write_globals_inventory() {
  compose exec -T postgres psql -At -v ON_ERROR_STOP=1 -U "$postgres_user" -d postgres -c "
    select 'role|' || encode(convert_to(rolname, 'UTF8'), 'hex') || '|' || rolsuper || '|' || rolinherit || '|' || rolcreaterole || '|' || rolcreatedb || '|' || rolcanlogin || '|' || rolreplication || '|' || rolbypassrls || '|' || rolconnlimit || '|' || coalesce(to_char(rolvaliduntil at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), '')
      from pg_roles where rolname !~ '^pg_' order by rolname;
    select 'member|' || encode(convert_to(parent.rolname, 'UTF8'), 'hex') || '|' || encode(convert_to(child.rolname, 'UTF8'), 'hex') || '|' || encode(convert_to(grantor.rolname, 'UTF8'), 'hex') || '|' || membership.admin_option || '|' || membership.inherit_option || '|' || membership.set_option
      from pg_auth_members membership
      join pg_roles parent on parent.oid = membership.roleid
      join pg_roles child on child.oid = membership.member
      join pg_roles grantor on grantor.oid = membership.grantor
      where parent.rolname !~ '^pg_' and child.rolname !~ '^pg_'
      order by parent.rolname, child.rolname;" \
    | LC_ALL=C sort
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
  write_globals_inventory > "$temporary/globals.inventory"
  chmod 600 "$temporary/globals.sql" "$temporary/globals.inventory" "$temporary/windmill.dump" "$temporary/research.dump"
  (cd "$temporary" && write_checksums globals.sql globals.inventory windmill.dump research.dump)
  printf 'format=test-server-backup-v1\ncreated_at_utc=%s\ncompose_project=%s\nresearch_database=%s\nwindmill_database=%s\npostgres_role=%s\nglobals_inventory_sha256=%s\n' \
    "$stamp" "$project" "$research_database" "$windmill_database" "$postgres_user" "$(sha256_file "$temporary/globals.inventory")" > "$temporary/manifest.txt"
  chmod 600 "$temporary/manifest.txt" "$temporary/SHA256SUMS"
  "$BACKUP_VERIFY" --backup-root "$backup_root" --backup-dir "$temporary" >/dev/null
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
  local mode="${1:-prefix}" database="${2:-$research_database}" migration expected actual prefix ledger_exists ledger_row actual_count
  ledger_exists="$(research_query "$database" "select to_regclass('public.schema_migrations') is not null")"
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
  research_query "$database" "select filename || '|' || sha256 from schema_migrations order by filename" > "$actual"
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
  verified="$(research_query "$research_database" "select (to_regclass('public.saved_research_filter') is not null)::int || '|' || (to_regclass('public.research_user_action') is not null)::int || '|' || exists(select 1 from information_schema.columns where table_schema='public' and table_name='collection_item' and column_name='account_id')::int || '|' || exists(select 1 from pg_constraint where conname='collection_item_one_target')::int || '|' || (to_regclass('public.uq_collection_owner_name') is not null)::int || '|' || (select bool_and(tableowner=current_user) from pg_tables where schemaname='public' and tablename in ('saved_research_filter','research_user_action','research_brief','research_brief_run'))::int || '|' || (has_table_privilege(current_user,'public.saved_research_filter','SELECT,INSERT,UPDATE') and has_table_privilege(current_user,'public.research_user_action','SELECT,INSERT,UPDATE') and has_table_privilege(current_user,'public.research_brief','SELECT,INSERT,UPDATE') and has_table_privilege(current_user,'public.research_brief_run','SELECT,INSERT,UPDATE'))::int || '|' || (to_regclass('public.research_brief') is not null)::int || '|' || (to_regclass('public.research_brief_run') is not null)::int || '|' || (to_regclass('public.uq_research_brief_owner_name') is not null)::int || '|' || (to_regclass('public.idx_research_brief_due') is not null)::int || '|' || (to_regclass('public.idx_research_brief_run_time') is not null)::int || '|' || exists(select 1 from pg_constraint where conname='research_brief_run_brief_id_fkey' and conrelid='public.research_brief_run'::regclass)::int")"
  [[ "$verified" == '1|1|1|1|1|1|1|1|1|1|1|1|1' ]] || { echo 'ERROR: research migration owner, privilege, or object verification failed.' >&2; exit 1; }
}

verify_project_contract() {
  local database="${1:-$research_database}" failed
  # Migration SHA equality is checked separately.  These object checks make a
  # ledger-only success insufficient to release a project-scoped Raw App. The
  # function hashes are SHA-256 of pg_proc.prosrc after applying reviewed 023;
  # pg_dump/restore preserves that source text. Update them only with a
  # reviewed ACL migration, never to accommodate unexplained server drift.
  failed="$(research_query "$database" "
    with checks(name, ok) as (values
      ('021.organization', to_regclass('public.research_organization') is not null),
      ('021.project', to_regclass('public.research_project') is not null),
      ('021.member', to_regclass('public.research_project_member') is not null),
      ('021.subject', to_regclass('public.research_subject') is not null),
      ('021.relation', to_regclass('public.project_account_relation') is not null),
      ('021.group', to_regclass('public.account_group') is not null),
      ('021.group_member', to_regclass('public.account_group_member') is not null),
      ('021.identity_link', to_regclass('public.account_identity_link') is not null),
      ('021.authorization', to_regclass('public.account_authorization') is not null),
      ('021.effective_authorization', to_regclass('public.effective_account_authorization') is not null),
      ('021.revoke_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_account_relation') and tgname='trg_revoke_authorizations_for_inactive_relation' and not tgisinternal and tgenabled <> 'D')),
      ('021.authorization_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.account_authorization') and tgname='trg_enforce_authorization_relation_not_inactive' and not tgisinternal and tgenabled <> 'D')),
      ('022.inclusion', to_regclass('public.project_video_inclusion') is not null),
      ('022.brief_project_id', exists(select 1 from pg_attribute where attrelid=to_regclass('public.research_brief') and attname='project_id' and atttypid='uuid'::regtype and not attisdropped)),
      ('022.run_project_id', exists(select 1 from pg_attribute where attrelid=to_regclass('public.pipeline_run') and attname='project_id' and atttypid='uuid'::regtype and not attisdropped)),
      ('022.brief_run_project_id', exists(select 1 from pg_attribute where attrelid=to_regclass('public.research_brief_run') and attname='project_id' and atttypid='uuid'::regtype and not attisdropped)),
      ('022.brief_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.research_brief_run') and conname='fk_research_brief_run_project_brief' and contype='f' and convalidated)),
      ('022.source_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.research_brief_run') and conname='fk_research_brief_run_project_source_run' and contype='f' and convalidated)),
      ('022.brief_scope_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.research_brief_run') and tgname='trg_research_brief_run_project_scope' and not tgisinternal and tgenabled <> 'D')),
      ('022.brief_immutable', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.research_brief') and tgname='trg_research_brief_project_id_immutable' and not tgisinternal and tgenabled <> 'D')),
      ('022.run_immutable', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.pipeline_run') and tgname='trg_pipeline_run_project_id_immutable' and not tgisinternal and tgenabled <> 'D')),
      ('022.brief_run_immutable', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.research_brief_run') and tgname='trg_research_brief_run_project_id_immutable' and not tgisinternal and tgenabled <> 'D')),
      ('022.inclusion_immutable', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_video_inclusion') and tgname='trg_project_video_inclusion_project_id_immutable' and not tgisinternal and tgenabled <> 'D')),
      ('023.actor_acl', exists(select 1 from pg_proc where oid=to_regprocedure('public.project_actor_can_read(uuid,text)') and not prosecdef and proconfig is null and provolatile='s' and prolang=(select oid from pg_language where lanname='sql') and prorettype='boolean'::regtype and pg_get_userbyid(proowner)=current_user)),
      ('023.video_acl', exists(select 1 from pg_proc where oid=to_regprocedure('public.project_video_can_read(uuid,text,uuid)') and not prosecdef and proconfig is null and provolatile='s' and prolang=(select oid from pg_language where lanname='sql') and prorettype='boolean'::regtype and pg_get_userbyid(proowner)=current_user)),
      ('023.actor_body', exists(select 1 from pg_proc where oid=to_regprocedure('public.project_actor_can_read(uuid,text)') and encode(sha256(convert_to(prosrc,'UTF8')),'hex')='33d00282c5102844ca75c904758014501974e5b0c294cf988dddf2e2d93cf4f6')),
      ('023.video_body', exists(select 1 from pg_proc where oid=to_regprocedure('public.project_video_can_read(uuid,text,uuid)') and encode(sha256(convert_to(prosrc,'UTF8')),'hex')='b552a65a73f582918ca91fae708192e14689db49e1977dddf72aaee70c789ca9')),
      ('023.actor_denies_unknown', public.project_actor_can_read('00000000-0000-0000-0000-000000000000'::uuid, 'nobody@example.invalid') = false),
      ('023.video_denies_unknown', public.project_video_can_read('00000000-0000-0000-0000-000000000000'::uuid, 'nobody@example.invalid', '00000000-0000-0000-0000-000000000000'::uuid) = false),
      ('024.cursor_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_account_relation_verified_cursor') and indisvalid and indisready)),
      ('project.table_owners', (select count(*)=10 from pg_tables where schemaname='public' and tableowner=current_user and tablename in ('research_organization','research_project','research_project_member','research_subject','project_account_relation','account_group','account_group_member','account_identity_link','account_authorization','project_video_inclusion'))),
      ('project.read_grants', coalesce(has_table_privilege(current_user, to_regclass('public.research_project_member'), 'SELECT'),false) and coalesce(has_table_privilege(current_user, to_regclass('public.project_account_relation'), 'SELECT'),false) and coalesce(has_table_privilege(current_user, to_regclass('public.project_video_inclusion'), 'SELECT'),false)),
      ('project.owner_only_grants', not exists (
        select 1 from pg_class relation_row
        join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace
        cross join lateral aclexplode(coalesce(relation_row.relacl, acldefault('r',relation_row.relowner))) grant_row
        where namespace_row.nspname='public'
          and relation_row.relname in ('research_organization','research_project','research_project_member','research_subject','project_account_relation','account_group','account_group_member','account_identity_link','account_authorization','project_video_inclusion','effective_account_authorization')
          and grant_row.grantee<>relation_row.relowner
      ))
    )
    select coalesce(string_agg(name, ',' order by name), '') from checks where ok is distinct from true
  ")"
  [[ -z "$failed" ]] || { printf 'ERROR: project migration contract verification failed: %s\n' "$failed" >&2; exit 1; }
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
  verify_project_contract
  printf 'MIGRATED backup=%s\n' "$backup_dir"
}

cmd_verify() { wait_for_postgres; verify_migration_ledger required; verify_contract; verify_project_contract; echo 'VERIFIED research and project migration contracts and ledger.'; }

cmd_restore_drill() (
  [[ "${TEST_SERVER_RESTORE_DRILL:-}" == YES ]] || { echo 'ERROR: set TEST_SERVER_RESTORE_DRILL=YES for this restore drill.' >&2; exit 2; }
  [[ -n "$backup_argument" ]] || { echo 'ERROR: restore-drill requires one backup directory.' >&2; exit 2; }
  local backup_dir source_counts restored_counts windmill_source_counts windmill_restored_counts remaining research_verified windmill_verified existing
  local brief_state brief_contract brief_source_counts brief_restored_counts brief_verified
  local project_state project_contract project_source_counts project_restored_counts
  local verification archive_manifest_sha globals_inventory_sha archive_created_at archive_verified_at start_epoch completed_at duration_seconds
  local research_created=0 windmill_created=0
  backup_dir="$(cd "$backup_argument" 2>/dev/null && pwd -P)" || { echo 'ERROR: backup directory does not exist.' >&2; exit 2; }
  case "$backup_dir" in "$backup_root"/*) ;; *) echo 'ERROR: restore drill accepts backups only beneath --backup-root.' >&2; exit 2;; esac
  verification="$("$BACKUP_VERIFY" --backup-root "$backup_root" --backup-dir "$backup_dir")"
  [[ "$verification" =~ ^BACKUP_VALID\ format=test-server-backup-v1\ manifest_sha256=([a-f0-9]{64})\ inventory_sha256=([a-f0-9]{64})\ created_at_utc=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)\ verified_at_utc=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)$ ]] || { echo 'ERROR: backup verifier returned an invalid summary.' >&2; exit 1; }
  archive_manifest_sha="${BASH_REMATCH[1]}"
  globals_inventory_sha="${BASH_REMATCH[2]}"
  archive_created_at="${BASH_REMATCH[3]}"
  archive_verified_at="${BASH_REMATCH[4]}"
  start_epoch="$(date +%s)"
  [[ -f "$backup_dir/research.dump" && -f "$backup_dir/windmill.dump" && -f "$backup_dir/globals.sql" && -f "$backup_dir/SHA256SUMS" && -f "$backup_dir/manifest.txt" ]] || { echo 'ERROR: backup is incomplete.' >&2; exit 1; }
  grep -Fqx 'research.dump' <(awk '{print $2}' "$backup_dir/SHA256SUMS") && grep -Fqx 'windmill.dump' <(awk '{print $2}' "$backup_dir/SHA256SUMS") && grep -Fqx 'globals.sql' <(awk '{print $2}' "$backup_dir/SHA256SUMS") || { echo 'ERROR: checksum manifest does not cover every backup artifact.' >&2; exit 1; }
  [[ "$(awk -F= '$1 == "compose_project" {print $2}' "$backup_dir/manifest.txt")" == "$project" ]] || { echo 'ERROR: backup manifest Compose project does not match --project.' >&2; exit 1; }
  [[ "$(awk -F= '$1 == "research_database" {print $2}' "$backup_dir/manifest.txt")" == "$research_database" && "$(awk -F= '$1 == "windmill_database" {print $2}' "$backup_dir/manifest.txt")" == "$windmill_database" ]] || { echo 'ERROR: backup manifest database names do not match the selected env file.' >&2; exit 1; }
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
  source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" research)"
  restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from source_video) || '|' || (select count(*) from collection) || '|' || (select count(*) from collection_item)")"
  [[ "$source_counts" == "$restored_counts" ]] || { echo 'ERROR: restore-drill key-table counts do not match the backup archive.' >&2; exit 1; }
  brief_state="$(research_query "$RESTORE_DATABASE" "select (to_regclass('public.research_brief') is not null)::int || '|' || (to_regclass('public.research_brief_run') is not null)::int || '|' || exists(select 1 from schema_migrations where filename='020_research_brief.sql')::int")"
  case "$brief_state" in
    '1|1|1')
      brief_contract=present
      brief_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" research_brief)"
      brief_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_brief) || '|' || (select count(*) from research_brief_run)")"
      [[ "$brief_source_counts" == "$brief_restored_counts" ]] || { echo 'ERROR: research-brief restore counts do not match the backup archive.' >&2; exit 1; }
      brief_verified="$(research_query "$RESTORE_DATABASE" "select (select bool_and(tableowner=current_user) from pg_tables where schemaname='public' and tablename in ('research_brief','research_brief_run'))::int || '|' || (to_regclass('public.uq_research_brief_owner_name') is not null)::int || '|' || (to_regclass('public.idx_research_brief_due') is not null)::int || '|' || (to_regclass('public.idx_research_brief_run_time') is not null)::int || '|' || exists(select 1 from pg_constraint where conname='research_brief_run_brief_id_fkey' and conrelid='public.research_brief_run'::regclass)::int")"
      [[ "$brief_verified" == '1|1|1|1|1' ]] || { echo 'ERROR: restored research-brief owner or object verification failed.' >&2; exit 1; }
      ;;
    '0|0|0') brief_contract=legacy_absent ;;
    *) echo 'ERROR: restored research-brief schema and migration ledger are inconsistent.' >&2; exit 1 ;;
  esac
  windmill_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/windmill.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" windmill)"
  windmill_restored_counts="$(admin_query "$RESTORE_WINDMILL_DATABASE" "select (select count(*) from workspace) || '|' || (select count(*) from usr)")"
  [[ "$windmill_source_counts" == "$windmill_restored_counts" ]] || { echo 'ERROR: Windmill restore-drill key-table counts do not match the backup archive.' >&2; exit 1; }
  research_verified="$(research_query "$RESTORE_DATABASE" "select (to_regclass('public.schema_migrations') is not null)::int || '|' || (to_regclass('public.source_video') is not null)::int || '|' || (to_regclass('public.collection') is not null)::int || '|' || (to_regclass('public.collection_item') is not null)::int || '|' || (to_regclass('public.saved_research_filter') is not null)::int || '|' || (to_regclass('public.research_user_action') is not null)::int || '|' || ((select count(*) from pg_tables where schemaname='public' and tablename in ('source_video','collection','collection_item','saved_research_filter','research_user_action','schema_migrations') and tableowner=current_user)=6)::int")"
  [[ "$research_verified" == '1|1|1|1|1|1|1' ]] || { echo 'ERROR: restored research database owner or key-object verification failed.' >&2; exit 1; }
  verify_migration_ledger prefix "$RESTORE_DATABASE"
  project_state="$(research_query "$RESTORE_DATABASE" "select (select count(*) from schema_migrations where filename in ('021_project_account_foundation.sql','022_project_task_ownership.sql','023_project_video_read_acl.sql','024_project_account_relation_cursor.sql')) || '|' || (select count(*) from pg_class relation_row join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace where namespace_row.nspname='public' and relation_row.relname in ('research_organization','research_project','research_project_member','research_subject','project_account_relation','account_group','account_group_member','account_identity_link','account_authorization','project_video_inclusion','effective_account_authorization','idx_project_account_relation_verified_cursor')) + (select count(*) from pg_proc function_row join pg_namespace namespace_row on namespace_row.oid=function_row.pronamespace where namespace_row.nspname='public' and function_row.proname in ('project_actor_can_read','project_video_can_read'))")"
  case "$project_state" in
    '0|0') project_contract=legacy_absent ;;
    '4|14')
      verify_migration_ledger required "$RESTORE_DATABASE"
      verify_project_contract "$RESTORE_DATABASE"
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: project restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=present
      ;;
    *) echo 'ERROR: restored project schema and migration ledger are inconsistent.' >&2; exit 1 ;;
  esac
  windmill_verified="$(admin_query "$RESTORE_WINDMILL_DATABASE" "select (to_regclass('public.workspace') is not null)::int || '|' || (to_regclass('public.usr') is not null)::int || '|' || (select bool_and(tableowner=current_user) from pg_tables where schemaname='public' and tablename in ('workspace','usr'))::int")"
  [[ "$windmill_verified" == '1|1|1' ]] || { echo 'ERROR: restored Windmill database owner or key-object verification failed.' >&2; exit 1; }
  local business_sql business_result business_summary
  business_sql="$(python3 "$ROOT_DIR/scripts/test-server-restored-business-sql.py")"
  business_result="$(research_query "$RESTORE_DATABASE" "$business_sql")"
  business_summary="$(printf '%s\n' "$business_result" | python3 -c '
import json,sys
rows=[line.removeprefix("RESTORED_BUSINESS_SQL ").strip() for line in sys.stdin if line.startswith("RESTORED_BUSINESS_SQL ")]
if len(rows)!=1:
    raise SystemExit("ERROR: missing unique restored business audit summary")
result=json.loads(rows[0])
print("RESTORED_BUSINESS_AUDIT "+json.dumps(result,sort_keys=True,separators=(",",":")))
if result.get("status")!="business_chain_present" or result.get("v1_release_accepted") is not False:
    print("RESTORED_BUSINESS_AUDIT "+json.dumps(result,sort_keys=True,separators=(",",":")), file=sys.stderr)
    raise SystemExit("ERROR: restored business association audit did not pass")
')"
  printf '%s\n' "$business_summary"
  cleanup
  remaining="$(admin_query postgres "select exists(select 1 from pg_database where datname in ('${RESTORE_DATABASE}', '${RESTORE_WINDMILL_DATABASE}'))")"
  [[ "$remaining" == f ]] || { echo 'ERROR: fixed temporary restore database remains after cleanup.' >&2; exit 1; }
  trap - EXIT
  completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  duration_seconds="$(( $(date +%s) - start_epoch ))"
  printf 'RESTORE_DRILL_VALID format=test-server-backup-v1 manifest_sha256=%s inventory_sha256=%s archive_created_at_utc=%s archive_verified_at_utc=%s completed_at_utc=%s duration_seconds=%s research_brief_contract=%s project_contract=%s\n' \
    "$archive_manifest_sha" "$globals_inventory_sha" "$archive_created_at" "$archive_verified_at" "$completed_at" "$duration_seconds" "$brief_contract" "$project_contract"
)

case "$command_name" in
  backup) cmd_backup ;;
  migrate) cmd_migrate ;;
  verify) cmd_verify ;;
  restore-drill) cmd_restore_drill ;;
  *) echo "ERROR: unknown command: $command_name" >&2; usage >&2; exit 2 ;;
esac
