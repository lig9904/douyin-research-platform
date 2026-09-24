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
  local database="${1:-$research_database}" mode="${2:-current}" failed
  local collab_checks='' owner_count=10 owner_tables owner_only_tables
  owner_tables="'research_organization','research_project','research_project_member','research_subject','project_account_relation','account_group','account_group_member','account_identity_link','account_authorization','project_video_inclusion'"
  owner_only_tables="$owner_tables,'effective_account_authorization'"
  if [[ "$mode" == current || "$mode" == restored || "$mode" == pre_accepted_share ]]; then
    owner_count=12
    owner_tables="$owner_tables,'project_video_share_grant','project_access_event'"
    owner_only_tables="$owner_only_tables,'project_video_share_grant','project_access_event'"
    collab_checks=",
      ('025.share_grant', to_regclass('public.project_video_share_grant') is not null),
      ('025.access_event', to_regclass('public.project_access_event') is not null),
      ('025.share_cursor', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_video_share_target_active') and indisvalid and indisready)),
      ('025.share_acl', exists(select 1 from pg_proc where oid=to_regprocedure('public.project_shared_video_can_read(uuid,uuid,text,uuid)') and not prosecdef and proconfig is null and provolatile='s' and prolang=(select oid from pg_language where lanname='sql') and prorettype='boolean'::regtype and pg_get_userbyid(proowner)=current_user)),
      ('026.share_body', exists(select 1 from pg_proc where oid=to_regprocedure('public.project_shared_video_can_read(uuid,uuid,text,uuid)') and encode(sha256(convert_to(prosrc,'UTF8')),'hex')='8325f74b3627ea04a3a2fd5506ceeb8e5f00fd660923f7f877682151a7e0e1d1')),
      ('025.share_public_execute', ('$mode' in ('restored','pre_accepted_share') or not exists(select 1 from pg_proc function_row cross join lateral aclexplode(coalesce(function_row.proacl,acldefault('f',function_row.proowner))) grant_row where function_row.oid=to_regprocedure('public.project_shared_video_can_read(uuid,uuid,text,uuid)') and grant_row.grantee=0 and grant_row.privilege_type='EXECUTE'))),
      ('025.share_denies_unknown', public.project_shared_video_can_read('00000000-0000-0000-0000-000000000000'::uuid,'00000000-0000-0000-0000-000000000000'::uuid,'nobody@example.invalid','00000000-0000-0000-0000-000000000000'::uuid)=false)"
    if [[ "$mode" == pre_accepted_share ]]; then
      collab_checks="${collab_checks/8325f74b3627ea04a3a2fd5506ceeb8e5f00fd660923f7f877682151a7e0e1d1/3bf5615f5cbc79e1bd6d9f2865a4b28c84205bd8fc012742bfb9ac366c2cae62}"
    fi
  elif [[ "$mode" != pre_collaboration ]]; then
    echo 'ERROR: unknown project contract mode.' >&2
    exit 2
  fi
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
      ('024.cursor_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_account_relation_verified_cursor') and indisvalid and indisready))$collab_checks,
      ('project.table_owners', (select count(*)=$owner_count from pg_tables where schemaname='public' and tableowner=current_user and tablename in ($owner_tables))),
      ('project.read_grants', coalesce(has_table_privilege(current_user, to_regclass('public.research_project_member'), 'SELECT'),false) and coalesce(has_table_privilege(current_user, to_regclass('public.project_account_relation'), 'SELECT'),false) and coalesce(has_table_privilege(current_user, to_regclass('public.project_video_inclusion'), 'SELECT'),false)),
      ('project.owner_only_grants', not exists (
        select 1 from pg_class relation_row
        join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace
        cross join lateral aclexplode(coalesce(relation_row.relacl, acldefault('r',relation_row.relowner))) grant_row
        where namespace_row.nspname='public'
          and relation_row.relname in ($owner_only_tables)
          and grant_row.grantee<>relation_row.relowner
      ))
    )
    select coalesce(string_agg(name, ',' order by name), '') from checks where ok is distinct from true
  ")"
  [[ -z "$failed" ]] || { printf 'ERROR: project migration contract verification failed: %s\n' "$failed" >&2; exit 1; }
}

verify_subject_relevance_contract() {
  # 027 adds a project interpretation layer.  Verify object shape, ownership,
  # intended owner access, and absence of grants to any other role separately
  # from the migration ledger: a recorded filename alone is not deployable.
  local database="${1:-$research_database}" failed
  failed="$(research_query "$database" "
    with checks(name, ok) as (values
      ('027.subject_term', to_regclass('public.research_subject_term') is not null),
      ('027.relevance', to_regclass('public.project_video_subject_relevance') is not null),
      ('027.audit', to_regclass('public.project_video_subject_relevance_audit') is not null),
      ('027.subject_term_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.research_subject_term') and contype='f' and convalidated and pg_get_constraintdef(oid) like '%REFERENCES research_subject(id, project_id)%')),
      ('027.relevance_inclusion_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_relevance') and contype='f' and convalidated and pg_get_constraintdef(oid) like '%REFERENCES project_video_inclusion(project_id, video_id)%')),
      ('027.relevance_subject_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_relevance') and contype='f' and convalidated and pg_get_constraintdef(oid) like '%REFERENCES research_subject(id, project_id)%')),
      ('027.relevance_run_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_relevance') and conname='fk_subject_relevance_run_project' and contype='f' and convalidated)),
      ('027.audit_run_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_relevance_audit') and conname='fk_subject_relevance_audit_run_project' and contype='f' and convalidated)),
      ('027.audit_no_current_fk', not exists(select 1 from pg_constraint constraint_row join pg_class referenced_table on referenced_table.oid=constraint_row.confrelid where constraint_row.conrelid=to_regclass('public.project_video_subject_relevance_audit') and constraint_row.contype='f' and referenced_table.relname='project_video_subject_relevance')),
      ('027.term_check', exists(select 1 from pg_constraint where conrelid=to_regclass('public.research_subject_term') and contype='c' and convalidated and pg_get_constraintdef(oid) like '%term_type = ANY%' and pg_get_constraintdef(oid) like '%''alias''%' and pg_get_constraintdef(oid) like '%''geographic_context''%' and pg_get_constraintdef(oid) like '%''exclusion''%')),
      ('027.relevance_check', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_relevance') and contype='c' and convalidated and pg_get_constraintdef(oid) like '%decision = ANY%' and pg_get_constraintdef(oid) like '%''pending''%' and pg_get_constraintdef(oid) like '%''relevant''%' and pg_get_constraintdef(oid) like '%''irrelevant''%')),
      ('027.audit_check', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_relevance_audit') and contype='c' and convalidated and pg_get_constraintdef(oid) like '%event_type = ANY%' and pg_get_constraintdef(oid) like '%''rule_evaluated''%' and pg_get_constraintdef(oid) like '%''manual_override''%')),
      ('027.term_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.uq_research_subject_term_active') and indisvalid and indisready)),
      ('027.relevance_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_video_subject_relevance_gate') and indisvalid and indisready)),
      ('027.audit_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_video_subject_relevance_audit_video') and indisvalid and indisready)),
      ('027.owners', (select count(*)=3 from pg_tables where schemaname='public' and tableowner=current_user and tablename in ('research_subject_term','project_video_subject_relevance','project_video_subject_relevance_audit'))),
      ('027.owner_grants', coalesce(has_table_privilege(current_user,to_regclass('public.research_subject_term'),'SELECT,INSERT,UPDATE'),false) and coalesce(has_table_privilege(current_user,to_regclass('public.project_video_subject_relevance'),'SELECT,INSERT,UPDATE'),false) and coalesce(has_table_privilege(current_user,to_regclass('public.project_video_subject_relevance_audit'),'SELECT,INSERT'),false)),
      ('027.no_nonowner_grants', not exists(select 1 from pg_class relation_row join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace cross join lateral aclexplode(coalesce(relation_row.relacl,acldefault('r',relation_row.relowner))) grant_row where namespace_row.nspname='public' and relation_row.relname in ('research_subject_term','project_video_subject_relevance','project_video_subject_relevance_audit') and grant_row.grantee<>relation_row.relowner))
    ) select coalesce(string_agg(name, ',' order by name), '') from checks where ok is distinct from true
  ")"
  [[ -z "$failed" ]] || { printf 'ERROR: subject relevance migration contract verification failed: %s\n' "$failed" >&2; exit 1; }
}

verify_decision_loop_contract() {
  # 028 stores a project-owned decision and an append-only, versioned outcome.
  # A ledger row alone cannot prove that reviewed evidence is still immutable.
  local database="${1:-$research_database}" failed
  failed="$(research_query "$database" "
    with checks(name, ok) as (values
      ('028.card', to_regclass('public.project_decision_card') is not null),
      ('028.card_event', to_regclass('public.project_decision_card_event') is not null),
      ('028.publication', to_regclass('public.project_publication_record') is not null),
      ('028.observation', to_regclass('public.project_publication_metric_observation') is not null),
      ('028.review_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_decision_card') and conname='fk_project_decision_card_review_observation' and contype='f' and convalidated)),
      ('028.publication_card_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_publication_record') and contype='f' and convalidated and confrelid=to_regclass('public.project_decision_card'))),
      ('028.observation_publication_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_publication_metric_observation') and contype='f' and convalidated and confrelid=to_regclass('public.project_publication_record'))),
      ('028.observation_version_unique', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_publication_metric_observation') and contype='u' and convalidated and pg_get_constraintdef(oid) like '%publication_id, metric_date, version%')),
      ('028.unknown_not_zero', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_publication_metric_observation') and contype='c' and convalidated and pg_get_constraintdef(oid) like '%num_nonnulls%')),
      ('028.published_only', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_publication_record') and contype='c' and convalidated and pg_get_constraintdef(oid) like '%published%')),
      ('028.accepted_source_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_decision_card') and tgname='trg_project_decision_card_accepted_source' and not tgisinternal and tgenabled <> 'D')),
      ('028.owner_member_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_decision_card') and tgname='trg_project_decision_card_owner_member' and not tgisinternal and tgenabled <> 'D')),
      ('028.reviewed_immutable_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_decision_card') and tgname='trg_project_decision_card_reviewed_immutable' and not tgisinternal and tgenabled <> 'D')),
      ('028.review_snapshot_update_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_decision_card') and tgname='trg_project_decision_card_review_snapshot' and not tgisinternal and tgenabled <> 'D')),
      ('028.review_snapshot_insert_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_decision_card') and tgname='trg_project_decision_card_review_snapshot_insert' and not tgisinternal and tgenabled <> 'D')),
      ('028.observation_append_only_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_publication_metric_observation') and tgname='trg_project_publication_metric_observation_append_only' and not tgisinternal and tgenabled <> 'D')),
      ('028.card_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_decision_card_project_status') and indisvalid and indisready)),
      ('028.observation_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_publication_metric_observation_project_date') and indisvalid and indisready)),
      ('028.review_evidence_integrity', not exists(
        select 1 from public.project_decision_card card
        left join public.project_publication_metric_observation metric
          on metric.id=card.review_observation_id and metric.project_id=card.project_id
        left join public.project_publication_record publication
          on publication.id=metric.publication_id and publication.project_id=metric.project_id
        where card.status='reviewed' and (
          metric.id is null or publication.decision_card_id is distinct from card.id
          or metric.version is distinct from card.review_observation_version
          or card.review_metric_snapshot is distinct from jsonb_build_object(
            'id', metric.id, 'version', metric.version, 'metric_date', metric.metric_date,
            'impressions', metric.impressions, 'engagements', metric.engagements,
            'likes', metric.likes, 'comments', metric.comments, 'shares', metric.shares,
            'follows', metric.follows, 'conversions', metric.conversions,
            'source', metric.source, 'source_reference', metric.source_reference,
            'source_reported_at', metric.source_reported_at,
            'source_version_or_digest', metric.source_version_or_digest,
            'measurement_scope', metric.measurement_scope, 'recorded_at', metric.recorded_at
          )
        )
      )),
      ('028.owners', (select count(*)=4 from pg_tables where schemaname='public' and tableowner=current_user and tablename in ('project_decision_card','project_decision_card_event','project_publication_record','project_publication_metric_observation'))),
      ('028.owner_grants', coalesce(has_table_privilege(current_user,to_regclass('public.project_decision_card'),'SELECT,INSERT,UPDATE'),false) and coalesce(has_table_privilege(current_user,to_regclass('public.project_decision_card_event'),'SELECT,INSERT'),false) and coalesce(has_table_privilege(current_user,to_regclass('public.project_publication_record'),'SELECT,INSERT'),false) and coalesce(has_table_privilege(current_user,to_regclass('public.project_publication_metric_observation'),'SELECT,INSERT'),false)),
      ('028.no_nonowner_grants', not exists(select 1 from pg_class relation_row join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace cross join lateral aclexplode(coalesce(relation_row.relacl,acldefault('r',relation_row.relowner))) grant_row where namespace_row.nspname='public' and relation_row.relname in ('project_decision_card','project_decision_card_event','project_publication_record','project_publication_metric_observation') and grant_row.grantee<>relation_row.relowner))
    ) select coalesce(string_agg(name, ',' order by name), '') from checks where ok is distinct from true
  ")"
  [[ -z "$failed" ]] || { printf 'ERROR: decision loop migration contract verification failed: %s\n' "$failed" >&2; exit 1; }
}

verify_project_subject_score_contract() {
  # 029 keeps a subject-specific score local to a project and a source run.
  # It is deployable only if the composite evidence foreign keys, append-only
  # triggers, indexes, ownership, and grants all survived the release.
  local database="${1:-$research_database}" failed
  failed="$(research_query "$database" "
    with checks(name, ok) as (values
      ('029.score', to_regclass('public.project_video_subject_score') is not null),
      ('029.inclusion_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_score') and contype='f' and convalidated and confrelid=to_regclass('public.project_video_inclusion') and pg_get_constraintdef(oid) like '%(project_id, video_id)%')),
      ('029.subject_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_score') and contype='f' and convalidated and confrelid=to_regclass('public.research_subject') and pg_get_constraintdef(oid) like '%(subject_id, project_id)%')),
      ('029.run_fk', exists(select 1 from pg_constraint where conrelid=to_regclass('public.project_video_subject_score') and contype='f' and convalidated and confrelid=to_regclass('public.pipeline_run') and pg_get_constraintdef(oid) like '%(source_run_id, project_id)%')),
      ('029.eligible_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_video_subject_score') and tgname='trg_project_video_subject_score_eligible' and not tgisinternal and tgenabled <> 'D')),
      ('029.immutable_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.project_video_subject_score') and tgname='trg_project_video_subject_score_immutable' and not tgisinternal and tgenabled <> 'D')),
      ('029.subject_score_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_video_subject_score_project_subject_score') and indisvalid and indisready)),
      ('029.video_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_project_video_subject_score_project_video') and indisvalid and indisready)),
      ('029.owner', exists(select 1 from pg_tables where schemaname='public' and tablename='project_video_subject_score' and tableowner=current_user)),
      ('029.owner_grants', coalesce(has_table_privilege(current_user,to_regclass('public.project_video_subject_score'),'SELECT,INSERT'),false)),
      ('029.no_nonowner_grants', not exists(select 1 from pg_class relation_row join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace cross join lateral aclexplode(coalesce(relation_row.relacl,acldefault('r',relation_row.relowner))) grant_row where namespace_row.nspname='public' and relation_row.relname='project_video_subject_score' and grant_row.grantee<>relation_row.relowner))
    ) select coalesce(string_agg(name, ',' order by name), '') from checks where ok is distinct from true
  ")"
  [[ -z "$failed" ]] || { printf 'ERROR: project subject score migration contract verification failed: %s\n' "$failed" >&2; exit 1; }
}

verify_subject_profile_contract() {
  # 030 is a project-local, versioned profile.  A ledger row is insufficient:
  # preserve the composite RESTRICT boundary and lifecycle triggers before a
  # Raw App can expose an approved profile.
  local database="${1:-$research_database}" failed
  failed="$(research_query "$database" "
    with checks(name, ok) as (values
      ('030.profile', to_regclass('public.research_subject_profile_version') is not null),
      ('030.one_approved_index', exists(select 1 from pg_index index_row where index_row.indexrelid=to_regclass('public.uq_research_subject_profile_one_approved') and index_row.indisunique and index_row.indisvalid and index_row.indisready and pg_get_expr(index_row.indpred,index_row.indrelid) like '%status = ''approved''%')),
      ('030.version_index', exists(select 1 from pg_index where indexrelid=to_regclass('public.idx_research_subject_profile_subject_kind_version') and indisvalid and indisready)),
      ('030.project_fk_restrict', exists(select 1 from pg_constraint where conrelid=to_regclass('public.research_subject_profile_version') and contype='f' and convalidated and confrelid=to_regclass('public.research_project') and pg_get_constraintdef(oid) like '%FOREIGN KEY (project_id) REFERENCES research_project(id) ON DELETE RESTRICT%')),
      ('030.subject_fk_restrict', exists(select 1 from pg_constraint where conrelid=to_regclass('public.research_subject_profile_version') and contype='f' and convalidated and confrelid=to_regclass('public.research_subject') and pg_get_constraintdef(oid) like '%FOREIGN KEY (subject_id, project_id) REFERENCES research_subject(id, project_id) ON DELETE RESTRICT%')),
      ('030.lifecycle_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.research_subject_profile_version') and tgname='trg_research_subject_profile_version_lifecycle' and not tgisinternal and tgenabled <> 'D')),
      ('030.no_delete_trigger', exists(select 1 from pg_trigger where tgrelid=to_regclass('public.research_subject_profile_version') and tgname='trg_research_subject_profile_version_no_delete' and not tgisinternal and tgenabled <> 'D')),
      ('030.owner', exists(select 1 from pg_tables where schemaname='public' and tablename='research_subject_profile_version' and tableowner=current_user)),
      ('030.owner_grants', coalesce(has_table_privilege(current_user,to_regclass('public.research_subject_profile_version'),'SELECT,INSERT,UPDATE'),false)),
      ('030.no_nonowner_grants', not exists(select 1 from pg_class relation_row join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace cross join lateral aclexplode(coalesce(relation_row.relacl,acldefault('r',relation_row.relowner))) grant_row where namespace_row.nspname='public' and relation_row.relname='research_subject_profile_version' and grant_row.grantee<>relation_row.relowner))
    ) select coalesce(string_agg(name, ',' order by name), '') from checks where ok is distinct from true
  ")"
  [[ -z "$failed" ]] || { printf 'ERROR: subject profile migration contract verification failed: %s\n' "$failed" >&2; exit 1; }
}

archive_profile_count() {
  # Count only the profile COPY body while discarding every row.  The restore
  # drill compares counts without logging source content or references.
  compose exec -T postgres pg_restore --data-only -f - | python3 -c '
import re, sys
target="research_subject_profile_version"; found=False; current=None; count=0
for line in sys.stdin:
    if current is not None:
        if line.rstrip("\r\n")=="\\.": current=None
        elif current==target: count += 1
        continue
    match=re.fullmatch(r"COPY public\.([a-z_]+) \(.*\) FROM stdin;\r?\n?", line)
    if re.fullmatch(r"COPY .* FROM stdin;\r?\n?", line):
        current=match.group(1) if match else "__unselected_copy__"
        if current==target:
            if found: raise SystemExit("ERROR: duplicate profile COPY section")
            found=True
if current is not None or not found: raise SystemExit("ERROR: incomplete profile COPY inventory")
print(count)
'
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
  verify_subject_relevance_contract
  verify_decision_loop_contract
  verify_project_subject_score_contract
  verify_subject_profile_contract
  printf 'MIGRATED backup=%s\n' "$backup_dir"
}

cmd_verify() { wait_for_postgres; verify_migration_ledger required; verify_contract; verify_project_contract; verify_subject_relevance_contract; verify_decision_loop_contract; verify_project_subject_score_contract; verify_subject_profile_contract; echo 'VERIFIED research, project, subject relevance, decision loop, subject score, and subject profile migration contracts and ledger.'; }

cmd_restore_drill() (
  [[ "${TEST_SERVER_RESTORE_DRILL:-}" == YES ]] || { echo 'ERROR: set TEST_SERVER_RESTORE_DRILL=YES for this restore drill.' >&2; exit 2; }
  [[ -n "$backup_argument" ]] || { echo 'ERROR: restore-drill requires one backup directory.' >&2; exit 2; }
  local backup_dir source_counts restored_counts windmill_source_counts windmill_restored_counts remaining research_verified windmill_verified existing
  local brief_state brief_contract brief_source_counts brief_restored_counts brief_verified
  local project_state project_contract project_source_counts project_restored_counts subject_contract subject_source_counts subject_restored_counts
  local decision_contract=legacy_absent decision_source_counts decision_restored_counts
  local subject_score_contract=legacy_absent subject_score_source_counts subject_score_restored_counts
  local subject_profile_contract=legacy_absent subject_profile_source_counts subject_profile_restored_counts
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
  project_state="$(research_query "$RESTORE_DATABASE" "select (select count(*) from schema_migrations where filename in ('021_project_account_foundation.sql','022_project_task_ownership.sql','023_project_video_read_acl.sql','024_project_account_relation_cursor.sql','025_project_collaboration.sql','026_share_only_accepted_video.sql','027_subject_relevance_gate.sql','028_project_decision_loop.sql','029_project_subject_score.sql','030_subject_profile_version.sql')) || '|' || (select count(*) from pg_class relation_row join pg_namespace namespace_row on namespace_row.oid=relation_row.relnamespace where namespace_row.nspname='public' and relation_row.relname in ('research_organization','research_project','research_project_member','research_subject','project_account_relation','account_group','account_group_member','account_identity_link','account_authorization','project_video_inclusion','effective_account_authorization','idx_project_account_relation_verified_cursor','project_video_share_grant','project_access_event','research_subject_term','project_video_subject_relevance','project_video_subject_relevance_audit','uq_research_subject_term_active','idx_research_subject_term_project_subject','idx_project_video_subject_relevance_gate','idx_project_video_subject_relevance_audit_video','project_decision_card','project_decision_card_event','project_publication_record','project_publication_metric_observation','idx_project_decision_card_project_status','idx_project_decision_card_project_source_video','idx_project_decision_card_event_card_time','idx_project_publication_record_project_date','idx_project_publication_metric_observation_project_date','project_video_subject_score','idx_project_video_subject_score_project_subject_score','idx_project_video_subject_score_project_video','research_subject_profile_version','uq_research_subject_profile_one_approved','idx_research_subject_profile_subject_kind_version')) + (select count(*) from pg_proc function_row join pg_namespace namespace_row on namespace_row.oid=function_row.pronamespace where namespace_row.nspname='public' and function_row.proname in ('project_actor_can_read','project_video_can_read','project_shared_video_can_read','enforce_project_decision_card_accepted_source','enforce_project_decision_card_owner_member','reject_reviewed_project_decision_card_change','reject_project_publication_metric_observation_change','enforce_project_decision_card_review_snapshot','enforce_project_video_subject_score_eligible','reject_project_video_subject_score_change','enforce_research_subject_profile_version','reject_research_subject_profile_version_delete'))")"
  case "$project_state" in
    '0|0') project_contract=legacy_absent; subject_contract=legacy_absent ;;
    '4|14')
      verify_project_contract "$RESTORE_DATABASE" pre_collaboration
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project_024)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: pre-collaboration project restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=pre_collaboration; subject_contract=legacy_absent
      ;;
    '5|17')
      verify_project_contract "$RESTORE_DATABASE" pre_accepted_share
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion) || '|' || (select count(*) from project_video_share_grant) || '|' || (select count(*) from project_access_event)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: pre-026 project restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=pre_accepted_share; subject_contract=legacy_absent
      ;;
    '6|17')
      # A 026 archive is a valid reviewed prefix even after a newer 027 is
      # checked out. Restore compatibility must not demand future migrations.
      verify_migration_ledger prefix "$RESTORE_DATABASE"
      verify_project_contract "$RESTORE_DATABASE" restored
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion) || '|' || (select count(*) from project_video_share_grant) || '|' || (select count(*) from project_access_event)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: project restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=present; subject_contract=legacy_absent
      ;;
    '7|24')
      # This branch knows through 027 only; a later 028 checkout must still
      # accept this archive as a verified prefix rather than misclassifying it.
      verify_migration_ledger prefix "$RESTORE_DATABASE"
      verify_project_contract "$RESTORE_DATABASE" restored
      verify_subject_relevance_contract "$RESTORE_DATABASE"
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion) || '|' || (select count(*) from project_video_share_grant) || '|' || (select count(*) from project_access_event)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: subject relevance project restore counts do not match the backup archive.' >&2; exit 1; }
      subject_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" subject_relevance_027)"
      subject_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_subject_term) || '|' || (select count(*) from project_video_subject_relevance) || '|' || (select count(*) from project_video_subject_relevance_audit)")"
      [[ "$subject_source_counts" == "$subject_restored_counts" ]] || { echo 'ERROR: subject relevance restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=present; subject_contract=present
      ;;
    '8|38')
      # A reviewed 028 archive remains restorable after 029 is checked out.
      # The ledger is a valid prefix; do not demand the future score schema.
      verify_migration_ledger prefix "$RESTORE_DATABASE"
      verify_project_contract "$RESTORE_DATABASE" restored
      verify_subject_relevance_contract "$RESTORE_DATABASE"
      verify_decision_loop_contract "$RESTORE_DATABASE"
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion) || '|' || (select count(*) from project_video_share_grant) || '|' || (select count(*) from project_access_event)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: decision loop project restore counts do not match the backup archive.' >&2; exit 1; }
      subject_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" subject_relevance_027)"
      subject_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_subject_term) || '|' || (select count(*) from project_video_subject_relevance) || '|' || (select count(*) from project_video_subject_relevance_audit)")"
      [[ "$subject_source_counts" == "$subject_restored_counts" ]] || { echo 'ERROR: decision loop subject restore counts do not match the backup archive.' >&2; exit 1; }
      decision_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" decision_loop_028)"
      decision_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from project_decision_card) || '|' || (select count(*) from project_decision_card_event) || '|' || (select count(*) from project_publication_record) || '|' || (select count(*) from project_publication_metric_observation)")"
      [[ "$decision_source_counts" == "$decision_restored_counts" ]] || { echo 'ERROR: decision loop restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=present; subject_contract=present; decision_contract=present
      ;;
    '9|43')
      # A reviewed 029 archive remains a valid prefix after 030 is checked
      # out. Do not demand a profile table that the archive never contained.
      verify_migration_ledger prefix "$RESTORE_DATABASE"
      verify_project_contract "$RESTORE_DATABASE" restored
      verify_subject_relevance_contract "$RESTORE_DATABASE"
      verify_decision_loop_contract "$RESTORE_DATABASE"
      verify_project_subject_score_contract "$RESTORE_DATABASE"
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion) || '|' || (select count(*) from project_video_share_grant) || '|' || (select count(*) from project_access_event)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: subject score project restore counts do not match the backup archive.' >&2; exit 1; }
      subject_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" subject_relevance_027)"
      subject_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_subject_term) || '|' || (select count(*) from project_video_subject_relevance) || '|' || (select count(*) from project_video_subject_relevance_audit)")"
      [[ "$subject_source_counts" == "$subject_restored_counts" ]] || { echo 'ERROR: subject relevance restore counts do not match the backup archive.' >&2; exit 1; }
      decision_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" decision_loop_028)"
      decision_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from project_decision_card) || '|' || (select count(*) from project_decision_card_event) || '|' || (select count(*) from project_publication_record) || '|' || (select count(*) from project_publication_metric_observation)")"
      [[ "$decision_source_counts" == "$decision_restored_counts" ]] || { echo 'ERROR: decision loop restore counts do not match the backup archive.' >&2; exit 1; }
      subject_score_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" subject_score_029)"
      subject_score_restored_counts="$(research_query "$RESTORE_DATABASE" "select count(*) from project_video_subject_score")"
      [[ "$subject_score_source_counts" == "$subject_score_restored_counts" ]] || { echo 'ERROR: subject score restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=present; subject_contract=present; decision_contract=present; subject_score_contract=present
      ;;
    '10|48')
      verify_migration_ledger required "$RESTORE_DATABASE"
      verify_project_contract "$RESTORE_DATABASE" restored
      verify_subject_relevance_contract "$RESTORE_DATABASE"
      verify_decision_loop_contract "$RESTORE_DATABASE"
      verify_project_subject_score_contract "$RESTORE_DATABASE"
      verify_subject_profile_contract "$RESTORE_DATABASE"
      project_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" project)"
      project_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_organization) || '|' || (select count(*) from research_project) || '|' || (select count(*) from research_project_member) || '|' || (select count(*) from research_subject) || '|' || (select count(*) from project_account_relation) || '|' || (select count(*) from account_group) || '|' || (select count(*) from account_group_member) || '|' || (select count(*) from account_identity_link) || '|' || (select count(*) from account_authorization) || '|' || (select count(*) from project_video_inclusion) || '|' || (select count(*) from project_video_share_grant) || '|' || (select count(*) from project_access_event)")"
      [[ "$project_source_counts" == "$project_restored_counts" ]] || { echo 'ERROR: subject profile project restore counts do not match the backup archive.' >&2; exit 1; }
      subject_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" subject_relevance_027)"
      subject_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from research_subject_term) || '|' || (select count(*) from project_video_subject_relevance) || '|' || (select count(*) from project_video_subject_relevance_audit)")"
      [[ "$subject_source_counts" == "$subject_restored_counts" ]] || { echo 'ERROR: subject profile relevance restore counts do not match the backup archive.' >&2; exit 1; }
      decision_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" decision_loop_028)"
      decision_restored_counts="$(research_query "$RESTORE_DATABASE" "select (select count(*) from project_decision_card) || '|' || (select count(*) from project_decision_card_event) || '|' || (select count(*) from project_publication_record) || '|' || (select count(*) from project_publication_metric_observation)")"
      [[ "$decision_source_counts" == "$decision_restored_counts" ]] || { echo 'ERROR: subject profile decision loop restore counts do not match the backup archive.' >&2; exit 1; }
      subject_score_source_counts="$(compose exec -T postgres pg_restore --data-only -f - < "$backup_dir/research.dump" | python3 "$ROOT_DIR/scripts/test-server-archive-counts.py" subject_score_029)"
      subject_score_restored_counts="$(research_query "$RESTORE_DATABASE" "select count(*) from project_video_subject_score")"
      [[ "$subject_score_source_counts" == "$subject_score_restored_counts" ]] || { echo 'ERROR: subject profile score restore counts do not match the backup archive.' >&2; exit 1; }
      subject_profile_source_counts="$(archive_profile_count < "$backup_dir/research.dump")"
      subject_profile_restored_counts="$(research_query "$RESTORE_DATABASE" "select count(*) from research_subject_profile_version")"
      [[ "$subject_profile_source_counts" == "$subject_profile_restored_counts" ]] || { echo 'ERROR: subject profile restore counts do not match the backup archive.' >&2; exit 1; }
      project_contract=present; subject_contract=present; decision_contract=present; subject_score_contract=present; subject_profile_contract=present
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
  printf 'RESTORE_DRILL_VALID format=test-server-backup-v1 manifest_sha256=%s inventory_sha256=%s archive_created_at_utc=%s archive_verified_at_utc=%s completed_at_utc=%s duration_seconds=%s research_brief_contract=%s project_contract=%s subject_relevance_contract=%s decision_loop_contract=%s subject_score_contract=%s subject_profile_contract=%s\n' \
    "$archive_manifest_sha" "$globals_inventory_sha" "$archive_created_at" "$archive_verified_at" "$completed_at" "$duration_seconds" "$brief_contract" "$project_contract" "$subject_contract" "$decision_contract" "$subject_score_contract" "$subject_profile_contract"
)

case "$command_name" in
  backup) cmd_backup ;;
  migrate) cmd_migrate ;;
  verify) cmd_verify ;;
  restore-drill) cmd_restore_drill ;;
  *) echo "ERROR: unknown command: $command_name" >&2; usage >&2; exit 2 ;;
esac
