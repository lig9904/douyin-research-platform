#!/usr/bin/env bash
# Local-only lifecycle wrapper. It never changes the active Docker context and
# never removes named volumes.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="l3-review-local"
CONTEXT="colima-l3-review-local"
PROJECT="l3-review-local"
ENV_FILE="$ROOT_DIR/.env.l3-local"
BASE_COMPOSE="$ROOT_DIR/docker-compose.yml"
LOCAL_COMPOSE="$ROOT_DIR/docker-compose.l3-local.yml"
RESOLV_CONF="$ROOT_DIR/config/l3-local-resolv.conf"
DOCKER_DAEMON_CONF="$ROOT_DIR/config/l3-local-docker-daemon.json"

usage() {
  cat <<'EOF'
Usage: scripts/local-l3-env.sh <init|start|status|provision|verify|migrate|restore-drill|test|review|stop>

Review:
  scripts/local-l3-env.sh review <video-id> <privacy-review-version> <fingerprint>

All commands target only the Colima profile and Compose project l3-review-local.
The test command recreates only the dedicated douyin_research_test database.
The migrate command targets only this profile's douyin_research database,
creates a checked backup first, and requires LOCAL_RESEARCH_MIGRATE=YES.
The restore-drill command restores one checked migration backup only into the
fixed temporary local_research_restore database and requires
LOCAL_RESEARCH_RESTORE_DRILL=YES.
EOF
}

env_value() {
  local key="$1"
  awk -F= -v wanted="$key" '$1 == wanted { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE"
}

require_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: missing .env.l3-local; run '$0 init' first." >&2
    exit 1
  fi
  if [[ "$(stat -f '%Lp' "$ENV_FILE")" != "600" ]]; then
    echo "ERROR: .env.l3-local must be mode 0600." >&2
    exit 1
  fi
}

compose() {
  docker --context "$CONTEXT" compose \
    -p "$PROJECT" \
    --env-file "$ENV_FILE" \
    -f "$BASE_COMPOSE" \
    -f "$LOCAL_COMPOSE" \
    "$@"
}

ensure_runtime() {
  if ! colima status --profile "$PROFILE" >/dev/null 2>&1; then
    echo "[local-l3] starting isolated Colima profile: $PROFILE"
    colima start --profile "$PROFILE" --runtime docker --cpus 2 --memory 4 --disk 20 \
      --dns 114.114.114.114 --dns 223.5.5.5 --activate=false
  fi
  # Some Colima Ubuntu images leave /etc/resolv.conf pointing at an absent
  # systemd-resolved stub. Repair only this dedicated profile from a committed,
  # non-secret resolver file before Docker performs registry lookups.
  colima ssh --profile "$PROFILE" -- sudo rm -f /etc/resolv.conf
  colima ssh --profile "$PROFILE" -- sudo install -m 0644 "$RESOLV_CONF" /etc/resolv.conf
  if ! colima ssh --profile "$PROFILE" -- cmp -s "$DOCKER_DAEMON_CONF" /etc/docker/daemon.json; then
    colima ssh --profile "$PROFILE" -- sudo install -m 0644 "$DOCKER_DAEMON_CONF" /etc/docker/daemon.json
    colima ssh --profile "$PROFILE" -- sudo systemctl restart docker
  fi
  docker --context "$CONTEXT" info >/dev/null
}

wait_for_postgres() {
  local postgres_user
  postgres_user="$(env_value POSTGRES_USER)"
  for _ in $(seq 1 45); do
    if compose exec -T postgres pg_isready -h 127.0.0.1 -U "$postgres_user" -d windmill >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  compose logs postgres >&2 || true
  echo "ERROR: isolated PostgreSQL did not become ready." >&2
  exit 1
}

cmd_init() {
  if [[ -e "$ENV_FILE" ]]; then
    echo "ERROR: refusing to overwrite existing .env.l3-local." >&2
    exit 1
  fi
  local postgres_password research_password reviewer_password
  postgres_password="$(openssl rand -hex 24)"
  research_password="$(openssl rand -hex 24)"
  reviewer_password="$(openssl rand -hex 24)"
  umask 077
  {
    printf '%s\n' 'WINDMILL_VERSION=1.815.0'
    printf '%s\n' 'WINDMILL_IMAGE=ghcr.io/windmill-labs/windmill@sha256:9f175a520477de0cc24724e32f49104ab7aa2537fc684eb1353bf541c1f1142a'
    printf '%s\n' 'POSTGRES_IMAGE=postgres@sha256:86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae'
    printf '%s\n' 'WINDMILL_BIND_HOST=127.0.0.1'
    printf '%s\n' 'WINDMILL_PORT=18000'
    printf '%s\n' 'POSTGRES_BIND_HOST=127.0.0.1'
    printf '%s\n' 'POSTGRES_PORT=15432'
    printf '%s\n' 'WINDMILL_BASE_URL=http://127.0.0.1:18000'
    printf '%s\n' 'WINDMILL_INTERNAL_URL=http://windmill_server:8000'
    printf '%s\n' 'POSTGRES_USER=postgres'
    printf 'POSTGRES_PASSWORD=%s\n' "$postgres_password"
    printf '%s\n' 'POSTGRES_DB=windmill'
    printf 'WINDMILL_DATABASE_URL=postgres://postgres:%s@postgres:5432/windmill?sslmode=disable\n' "$postgres_password"
    printf '%s\n' 'RESEARCH_DB_NAME=douyin_research'
    printf '%s\n' 'RESEARCH_DB_USER=douyin_research'
    printf 'RESEARCH_DB_PASSWORD=%s\n' "$research_password"
    printf '%s\n' 'L3_LOCAL_REVIEWER_USER=l3_local_reviewer'
    printf 'L3_LOCAL_REVIEWER_PASSWORD=%s\n' "$reviewer_password"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "[local-l3] created .env.l3-local with mode 0600. Secrets were not printed."
}

cmd_provision() {
  require_env
  ensure_runtime
  compose up -d postgres
  wait_for_postgres
  compose exec -T postgres bash /docker-entrypoint-initdb.d/02-bootstrap-l3-local-reviewer.sh >/dev/null
  echo "[local-l3] reviewer role provisioned."
}

cmd_status() {
  require_env
  ensure_runtime
  compose ps
}

cmd_verify() {
  require_env
  ensure_runtime
  local windmill_port postgres_port permission_row role_row
  windmill_port="$(env_value WINDMILL_PORT)"
  postgres_port="$(env_value POSTGRES_PORT)"

  [[ "$(compose port windmill_server 8000)" == "127.0.0.1:${windmill_port}" ]] || {
    echo "ERROR: Windmill is not bound to the expected loopback port." >&2
    exit 1
  }
  [[ "$(compose port postgres 5432)" == "127.0.0.1:${postgres_port}" ]] || {
    echo "ERROR: PostgreSQL is not bound to the expected loopback port." >&2
    exit 1
  }
  curl --fail --silent --show-error "http://127.0.0.1:${windmill_port}/api/version" >/dev/null

  role_row="$(printf '%s\n' \
    "SELECT r.rolinherit, r.rolbypassrls, count(m.roleid) FROM pg_roles r LEFT JOIN pg_auth_members m ON m.member=r.oid WHERE r.rolname=:'reviewer_user' GROUP BY r.rolinherit, r.rolbypassrls;" \
    | compose exec -T postgres psql \
      -U "$(env_value POSTGRES_USER)" -d "$(env_value POSTGRES_DB)" -At \
      -v reviewer_user="$(env_value L3_LOCAL_REVIEWER_USER)")"
  [[ "$role_row" == "f|f|0" ]] || {
    echo "ERROR: reviewer role inheritance policy does not match the local read-only contract." >&2
    exit 1
  }

  permission_row="$(compose exec -T postgres bash -c '
    PGPASSWORD="$L3_LOCAL_REVIEWER_PASSWORD" exec psql \
      -h postgres -U "$L3_LOCAL_REVIEWER_USER" -d "$RESEARCH_DB_NAME" \
      -At -v ON_ERROR_STOP=1 \
      -c "SELECT current_setting('"'"'transaction_read_only'"'"'), has_table_privilege(current_user, '"'"'public.source_video'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.research_promotion_decision'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.video_comment_feature_snapshot'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.transcript'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.metric_snapshot'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.merged_video_metric'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.analysis_run'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.research_task_cost'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.daily_budget'"'"', '"'"'SELECT'"'"'), has_table_privilege(current_user, '"'"'public.human_annotation'"'"', '"'"'SELECT,INSERT'"'"'), has_table_privilege(current_user, '"'"'public.daily_budget'"'"', '"'"'UPDATE'"'"'), has_schema_privilege(current_user, '"'"'public'"'"', '"'"'CREATE'"'"'), has_database_privilege(current_user, current_database(), '"'"'CREATE'"'"'), has_database_privilege(current_user, current_database(), '"'"'TEMPORARY'"'"'), has_database_privilege(current_user, '"'"'windmill'"'"', '"'"'CONNECT'"'"');"
  ')"
  [[ "$permission_row" == "on|t|t|t|t|t|t|t|t|t|f|f|f|f|f|f" ]] || {
    echo "ERROR: reviewer role permissions do not match the local read-only contract." >&2
    exit 1
  }
  echo "[local-l3] verified loopback ports, Windmill health, and reviewer read-only permissions."
}

cmd_test() {
  require_env
  ensure_runtime
  compose up -d postgres
  wait_for_postgres

  local research_user research_password postgres_port test_database test_url migration_path
  research_user="$(env_value RESEARCH_DB_USER)"
  research_password="$(env_value RESEARCH_DB_PASSWORD)"
  postgres_port="$(env_value POSTGRES_PORT)"
  test_database="douyin_research_test"

  # This command deliberately recreates only the fixed, local test database.
  # It never touches the research or Windmill databases.
  compose exec -T postgres psql \
    -U "$(env_value POSTGRES_USER)" \
    -d postgres \
    -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE IF EXISTS ${test_database} WITH (FORCE)" \
    -c "CREATE DATABASE ${test_database} OWNER ${research_user}" >/dev/null
  compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$RESEARCH_DB_USER" -d douyin_research_test -f /bootstrap/schema.sql' \
    >/dev/null
  for migration_path in "$ROOT_DIR"/db/migrations/*.sql; do
    compose exec -T postgres bash -c \
      'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -v ON_ERROR_STOP=1 -U "$RESEARCH_DB_USER" -d douyin_research_test' \
      < "$migration_path" >/dev/null
  done

  test_url="postgresql://${research_user}:${research_password}@127.0.0.1:${postgres_port}/${test_database}?sslmode=disable"
  echo "[local-l3] compiled sources and applied schema plus every migration."
  uv run python -m compileall -q src windmill
  echo "[local-l3] running the full suite against the dedicated PostgreSQL test database."
  TEST_DATABASE_URL="$test_url" uv run --extra dev pytest "$@"
}

cmd_migrate() {
  require_env
  [[ "${LOCAL_RESEARCH_MIGRATE:-}" == "YES" ]] || {
    echo "ERROR: set LOCAL_RESEARCH_MIGRATE=YES for the reviewed local migration." >&2
    exit 2
  }
  ensure_runtime
  compose up -d postgres
  wait_for_postgres

  local research_db duplicates has_account invalid_items stamp backup_dir migration_path verified
  research_db="$(env_value RESEARCH_DB_NAME)"
  [[ "$research_db" == "douyin_research" ]] || {
    echo "ERROR: local migration only permits the fixed douyin_research database." >&2
    exit 2
  }

  research_query() {
    compose exec -T postgres bash -c \
      'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -U "$RESEARCH_DB_USER" -d "$RESEARCH_DB_NAME" -At -v ON_ERROR_STOP=1 -c "$1"' \
      bash "$1"
  }
  duplicates="$(research_query \
    "select count(*) from (select created_by, lower(name) from collection group by created_by, lower(name) having count(*) > 1) duplicate_names")"
  [[ "$duplicates" == "0" ]] || {
    echo "ERROR: duplicate owner/name collections must be resolved before migration." >&2
    exit 1
  }
  has_account="$(research_query \
    "select exists(select 1 from information_schema.columns where table_schema='public' and table_name='collection_item' and column_name='account_id')")"
  if [[ "$has_account" == "t" ]]; then
    invalid_items="$(research_query \
      "select count(*) from collection_item where (video_id is not null)::int + (account_id is not null)::int + (signal_id is not null)::int <> 1")"
  else
    invalid_items="$(research_query \
      "select count(*) from collection_item where (video_id is not null)::int + (signal_id is not null)::int <> 1")"
  fi
  [[ "$invalid_items" == "0" ]] || {
    echo "ERROR: invalid collection targets must be resolved before migration." >&2
    exit 1
  }

  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_dir="$ROOT_DIR/work/local-db-migrations/$stamp"
  [[ ! -e "$backup_dir" ]] || { echo "ERROR: refusing to overwrite migration backup." >&2; exit 1; }
  mkdir -p "$backup_dir"
  chmod 700 "$ROOT_DIR/work/local-db-migrations" "$backup_dir"
  compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec pg_dump -U "$RESEARCH_DB_USER" -Fc "$RESEARCH_DB_NAME"' \
    > "$backup_dir/douyin_research.dump"
  chmod 600 "$backup_dir/douyin_research.dump"
  (cd "$backup_dir" && shasum -a 256 douyin_research.dump > SHA256SUMS)
  printf 'created_at_utc=%s\ncontext=%s\nproject=%s\ndatabase=%s\n' \
    "$stamp" "$CONTEXT" "$PROJECT" "$research_db" > "$backup_dir/manifest.txt"

  {
    printf '%s\n' '\set ON_ERROR_STOP on' 'begin;'
    for migration_path in "$ROOT_DIR"/db/migrations/*.sql; do
      printf '%s %s\n' '\echo applying' "$(basename "$migration_path")"
      sed -n '1,$p' "$migration_path"
    done
    printf '%s\n' 'commit;'
  } | compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -U "$RESEARCH_DB_USER" -d "$RESEARCH_DB_NAME" -v ON_ERROR_STOP=1' \
    >/dev/null

  verified="$(research_query \
    "select (to_regclass('public.saved_research_filter') is not null)::int || '|' || (to_regclass('public.research_user_action') is not null)::int || '|' || exists(select 1 from information_schema.columns where table_schema='public' and table_name='collection_item' and column_name='account_id')::int || '|' || exists(select 1 from pg_constraint where conname='collection_item_one_target')::int || '|' || (to_regclass('public.uq_collection_owner_name') is not null)::int || '|' || (select bool_and(tableowner=current_user) from pg_tables where schemaname='public' and tablename in ('saved_research_filter','research_user_action'))::int || '|' || (has_table_privilege(current_user,'public.saved_research_filter','SELECT') and has_table_privilege(current_user,'public.saved_research_filter','INSERT') and has_table_privilege(current_user,'public.saved_research_filter','UPDATE') and has_table_privilege(current_user,'public.research_user_action','SELECT') and has_table_privilege(current_user,'public.research_user_action','INSERT') and has_table_privilege(current_user,'public.research_user_action','UPDATE'))::int")"
  [[ "$verified" == "1|1|1|1|1|1|1" ]] || {
    echo "ERROR: migration verification did not match the research action contract." >&2
    exit 1
  }
  echo "[local-l3] migrations applied atomically after preflight; verified backup: $backup_dir"
}

cmd_restore_drill() (
  require_env
  [[ "${LOCAL_RESEARCH_RESTORE_DRILL:-}" == "YES" ]] || {
    echo "ERROR: set LOCAL_RESEARCH_RESTORE_DRILL=YES for the local restore drill." >&2
    exit 2
  }
  [[ "$#" == "1" ]] || {
    echo "ERROR: restore-drill requires one migration backup directory." >&2
    exit 2
  }

  local backup_dir research_db restore_db=local_research_restore count_sql source_counts restored_counts verified remaining
  [[ -d "$1" ]] || { echo "ERROR: migration backup directory does not exist." >&2; exit 2; }
  backup_dir="$(cd "$1" && pwd -P)"
  case "$backup_dir" in
    "$ROOT_DIR"/work/local-db-migrations/*) ;;
    *) echo "ERROR: restore drill only accepts backups under work/local-db-migrations/." >&2; exit 2 ;;
  esac
  [[ -f "$backup_dir/douyin_research.dump" && -f "$backup_dir/SHA256SUMS" && -f "$backup_dir/manifest.txt" ]] || {
    echo "ERROR: migration backup is incomplete." >&2
    exit 1
  }
  grep -Fq "douyin_research.dump" "$backup_dir/SHA256SUMS" || {
    echo "ERROR: migration backup checksum does not cover the research dump." >&2
    exit 1
  }
  (cd "$backup_dir" && shasum -a 256 -c SHA256SUMS >/dev/null)

  ensure_runtime
  compose up -d postgres
  wait_for_postgres
  research_db="$(env_value RESEARCH_DB_NAME)"
  [[ "$research_db" == "douyin_research" && "$restore_db" != "$research_db" ]] || {
    echo "ERROR: restore drill database guard failed." >&2
    exit 2
  }

  cleanup_restore() {
    compose exec -T postgres psql -U "$(env_value POSTGRES_USER)" -d postgres \
      -v ON_ERROR_STOP=1 -v restore_db="$restore_db" >/dev/null <<'SQL' || true
SELECT format('DROP DATABASE IF EXISTS %I WITH (FORCE)', :'restore_db')\gexec
SQL
  }
  cleanup_restore_strict() {
    compose exec -T postgres psql -U "$(env_value POSTGRES_USER)" -d postgres \
      -v ON_ERROR_STOP=1 -v restore_db="$restore_db" >/dev/null <<'SQL'
SELECT format('DROP DATABASE IF EXISTS %I WITH (FORCE)', :'restore_db')\gexec
SQL
  }
  database_query() {
    local database="$1" query="$2"
    compose exec -T postgres bash -c \
      'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec psql -U "$RESEARCH_DB_USER" -d "$1" -At -v ON_ERROR_STOP=1 -c "$2"' \
      bash "$database" "$query"
  }
  trap cleanup_restore EXIT
  cleanup_restore
  compose exec -T postgres psql -U "$(env_value POSTGRES_USER)" -d postgres \
    -v ON_ERROR_STOP=1 -v restore_db="$restore_db" -v restore_owner="$(env_value RESEARCH_DB_USER)" >/dev/null <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'restore_db', :'restore_owner')\gexec
SQL
  compose exec -T postgres bash -c \
    'PGPASSWORD="$RESEARCH_DB_PASSWORD" exec pg_restore -U "$RESEARCH_DB_USER" --no-owner --no-privileges -d local_research_restore' \
    < "$backup_dir/douyin_research.dump"

  count_sql="select (select count(*) from source_video) || '|' || (select count(*) from collection) || '|' || (select count(*) from collection_item)"
  source_counts="$(database_query "$research_db" "$count_sql")"
  restored_counts="$(database_query "$restore_db" "$count_sql")"
  [[ "$source_counts" == "$restored_counts" ]] || {
    echo "ERROR: restored key-table row counts do not match the source database." >&2
    exit 1
  }
  verified="$(database_query "$restore_db" "select (to_regclass('public.source_video') is not null)::int || '|' || (to_regclass('public.collection') is not null)::int || '|' || (to_regclass('public.collection_item') is not null)::int || '|' || (to_regclass('public.saved_research_filter') is not null)::int || '|' || (to_regclass('public.research_user_action') is not null)::int || '|' || (select bool_and(tableowner=current_user) from pg_tables where schemaname='public' and tablename in ('source_video','collection','collection_item','saved_research_filter','research_user_action'))::int")"
  [[ "$verified" == "1|1|1|1|1|1" ]] || {
    echo "ERROR: restored database schema or owner verification failed." >&2
    exit 1
  }

  cleanup_restore_strict
  remaining="$(compose exec -T postgres psql -U "$(env_value POSTGRES_USER)" -d postgres -At -v ON_ERROR_STOP=1 -c "select exists(select 1 from pg_database where datname='$restore_db')")"
  [[ "$remaining" == "f" ]] || {
    echo "ERROR: temporary restore database still exists after cleanup." >&2
    exit 1
  }
  trap - EXIT
  echo "[local-l3] restore drill passed: checksum, key-table counts, owners, and cleanup verified."
)

cmd_review() {
  require_env
  ensure_runtime
  if [[ "$#" != "3" ]]; then
    usage
    exit 2
  fi
  local video_id="$1" review_version="$2" fingerprint="$3"
  local reviewer_user reviewer_password research_db postgres_port
  reviewer_user="$(env_value L3_LOCAL_REVIEWER_USER)"
  reviewer_password="$(env_value L3_LOCAL_REVIEWER_PASSWORD)"
  research_db="$(env_value RESEARCH_DB_NAME)"
  postgres_port="$(env_value POSTGRES_PORT)"
  export L3_REVIEW_DATABASE_URL="postgresql://${reviewer_user}:${reviewer_password}@127.0.0.1:${postgres_port}/${research_db}?sslmode=disable"
  exec uv run python -m douyin_research.l3.local_review \
    --video-id "$video_id" \
    --privacy-review-version "$review_version" \
    --expected-fingerprint "$fingerprint" \
    --port 18443
}

cmd_start() {
  require_env
  ensure_runtime
  compose pull
  compose up -d
  cmd_provision
  cmd_verify
}

cmd_stop() {
  require_env
  ensure_runtime
  compose down --remove-orphans
  echo "[local-l3] stopped containers; named volumes were retained."
}

case "${1:-}" in
  init) cmd_init ;;
  start) cmd_start ;;
  status) cmd_status ;;
  provision) cmd_provision ;;
  verify) cmd_verify ;;
  migrate) cmd_migrate ;;
  restore-drill) shift; cmd_restore_drill "$@" ;;
  test) shift; cmd_test "$@" ;;
  review) shift; cmd_review "$@" ;;
  stop) cmd_stop ;;
  *) usage; exit 2 ;;
esac
