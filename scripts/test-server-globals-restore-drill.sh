#!/usr/bin/env bash
# Restore only PostgreSQL global objects into one disposable, isolated cluster.
# This never contacts the selected test-server Compose project.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/test-server-globals-restore-drill.sh --backup-root <absolute-path> \
  --postgres-image <postgres@sha256:...> <backup-directory>

Requires TEST_SERVER_GLOBALS_RESTORE_DRILL=YES. The input must be a completed
test-server-backup-v1 directory below --backup-root. It is restored only into
one temporary PostgreSQL container with no network or published ports.
EOF
}

die() { printf 'ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BACKUP_VERIFY="$ROOT_DIR/scripts/test-server-backup-verify.py"
file_mode() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }
file_owner() { stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1"; }
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

[[ "${TEST_SERVER_GLOBALS_RESTORE_DRILL:-}" == YES ]] || die 'set TEST_SERVER_GLOBALS_RESTORE_DRILL=YES for this globals restore drill.' 2
backup_root=""
postgres_image=""
backup_argument=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --backup-root) backup_root="${2:-}"; shift 2 ;;
    --postgres-image) postgres_image="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) [[ -z "$backup_argument" ]] || { usage >&2; die "unknown argument: $1" 2; }; backup_argument="$1"; shift ;;
  esac
done

[[ "$backup_root" = /* && -d "$backup_root" ]] || die '--backup-root must be an existing absolute directory.' 2
backup_root="$(cd "$backup_root" && pwd -P)"
[[ "$backup_root" != / ]] || die '--backup-root must not be /.' 2
[[ "$(file_owner "$backup_root")" == "$(id -u)" && "$(file_mode "$backup_root")" == 700 && -w "$backup_root" && -x "$backup_root" ]] || die '--backup-root must be current-user-owned, writable, and exact mode 0700.' 2
[[ "$postgres_image" =~ ^postgres(@sha256:|:[a-zA-Z0-9._-]+@sha256:)[a-f0-9]{64}$ ]] || die '--postgres-image must be a fixed postgres image digest.' 2
[[ -n "$backup_argument" ]] || die 'one backup directory is required.' 2
backup_dir="$(cd "$backup_argument" 2>/dev/null && pwd -P)" || die 'backup directory does not exist.' 2
case "$backup_dir" in "$backup_root"/*) ;; *) die 'restore drill accepts backups only beneath --backup-root.' 2 ;; esac
verification="$("$BACKUP_VERIFY" --backup-root "$backup_root" --backup-dir "$backup_dir")" || die 'backup archive did not pass the authoritative test-server-backup-v1 verifier.'
[[ "$verification" =~ ^BACKUP_VALID\ format=test-server-backup-v1\ manifest_sha256=([a-f0-9]{64})\ inventory_sha256=([a-f0-9]{64})\ created_at_utc=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)\ verified_at_utc=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)$ ]] || die 'backup verifier returned an invalid summary.'
archive_manifest_sha="${BASH_REMATCH[1]}"
globals_inventory_sha="${BASH_REMATCH[2]}"
archive_created_at="${BASH_REMATCH[3]}"
archive_verified_at="${BASH_REMATCH[4]}"
start_epoch="$(date +%s)"
[[ -d "$backup_dir" && ! -L "$backup_dir" ]] || die 'backup directory must be a real directory.' 2
[[ "$(file_owner "$backup_dir")" == "$(id -u)" && "$(file_mode "$backup_dir")" == 700 ]] || die 'backup directory must be current-user-owned and exact mode 0700.' 2

for artifact in globals.sql globals.inventory windmill.dump research.dump manifest.txt SHA256SUMS; do
  artifact_path="$backup_dir/$artifact"
  [[ -f "$artifact_path" && ! -L "$artifact_path" ]] || die 'backup is incomplete or contains a symbolic link.'
  [[ "$(file_owner "$artifact_path")" == "$(id -u)" && "$(file_mode "$artifact_path")" == 600 ]] || die 'backup artifacts must be current-user-owned and exact mode 0600.'
done

# SHA256SUMS is parsed rather than delegated to an external manifest runner, so an untrusted
# manifest cannot make the verifier read an arbitrary path or print file names.
declare -A supplied_hashes=()
while IFS=' ' read -r digest filename extra; do
  [[ -n "$digest" && -n "$filename" && -z "${extra:-}" && "$digest" =~ ^[a-f0-9]{64}$ && "$filename" =~ ^(globals\.sql|globals\.inventory|windmill\.dump|research\.dump)$ ]] || die 'SHA256SUMS must contain only complete named backup artifact checksums.'
  [[ -z "${supplied_hashes[$filename]:-}" ]] || die 'SHA256SUMS contains a duplicate artifact.'
  supplied_hashes["$filename"]="$digest"
done < "$backup_dir/SHA256SUMS"
[[ "${#supplied_hashes[@]}" == 4 ]] || die 'checksum manifest must cover every test-server backup artifact.'
for artifact in globals.sql globals.inventory windmill.dump research.dump; do
  [[ -n "${supplied_hashes[$artifact]:-}" ]] || die 'checksum manifest does not cover every test-server backup artifact.'
  [[ "$(sha256_file "$backup_dir/$artifact")" == "${supplied_hashes[$artifact]}" ]] || die 'backup checksum verification failed.'
done

declare -A manifest=()
while IFS='=' read -r key value extra; do
  [[ -n "$key" && -z "${extra:-}" && "$key" =~ ^(format|created_at_utc|compose_project|research_database|windmill_database|postgres_role|globals_inventory_sha256)$ && -n "$value" ]] || die 'manifest.txt is malformed.'
  [[ -z "${manifest[$key]:-}" ]] || die 'manifest.txt contains a duplicate key.'
  manifest["$key"]="$value"
done < "$backup_dir/manifest.txt"
[[ "${#manifest[@]}" == 7 && "${manifest[format]:-}" == test-server-backup-v1 ]] || die 'backup manifest is not a complete test-server-backup-v1 manifest.'
[[ "${manifest[created_at_utc]:-}" =~ ^[0-9]{8}T[0-9]{6}Z$ && "${manifest[compose_project]:-}" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ && "${manifest[research_database]:-}" =~ ^[a-z_][a-z0-9_]{0,62}$ && "${manifest[windmill_database]:-}" =~ ^[a-z_][a-z0-9_]{0,62}$ && "${manifest[postgres_role]:-}" =~ ^[a-z_][a-z0-9_]{0,62}$ && "${manifest[globals_inventory_sha256]:-}" =~ ^[a-f0-9]{64}$ ]] || die 'backup manifest contains invalid values.'
[[ "${manifest[globals_inventory_sha256]}" == "${supplied_hashes[globals.inventory]}" ]] || die 'globals inventory digest does not match SHA256SUMS.'
source_postgres_role="${manifest[postgres_role]}"
[[ "$(grep -Fxc "CREATE ROLE ${source_postgres_role};" "$backup_dir/globals.sql")" == 1 ]] || die 'globals SQL must contain exactly one simple CREATE ROLE for the archived PostgreSQL administrator.'

# A tablespace needs a host path and therefore has no safe meaning in this
# sealed disposable cluster. Check without printing SQL, which can contain
# password hashes in CREATE/ALTER ROLE statements.
awk 'BEGIN { RS=";" } { statement=tolower($0); if (statement ~ /create[[:space:]]+tablespace/ || statement ~ /alter[[:space:]].*tablespace/) exit 1 }' "$backup_dir/globals.sql" || die 'globals restore drill rejects non-default tablespaces.'
command -v docker >/dev/null 2>&1 || die 'docker is required.' 2
docker info >/dev/null 2>&1 || die 'Docker daemon is unavailable.' 2
command -v openssl >/dev/null 2>&1 || die 'openssl is required to initialize the disposable PostgreSQL cluster.' 2

suffix="$(date -u +%Y%m%d%H%M%S)-$$-$RANDOM"
container="test-server-globals-restore-${suffix}"
volume="test-server-globals-restore-${suffix}"
runtime_dir="$(mktemp -d "$backup_root/.globals-restore.XXXXXX")"
chmod 700 "$runtime_dir"
restored_inventory="$runtime_dir/restored.inventory"
postgres_password="$(openssl rand -hex 24)"
volume_created=0
cleanup() {
  local status=$? cleanup_failed=0
  trap - EXIT HUP INT TERM
  # A failed `docker run -d` may still leave its exact named container behind.
  # The name contains our timestamp/PID/random suffix and is never user input.
  if docker container inspect "$container" >/dev/null 2>&1; then
    docker rm -f "$container" >/dev/null 2>&1 || cleanup_failed=1
  fi
  if docker container inspect "$container" >/dev/null 2>&1; then cleanup_failed=1; fi
  if [[ "$volume_created" == 1 ]]; then
    docker volume rm "$volume" >/dev/null 2>&1 || cleanup_failed=1
    if docker volume inspect "$volume" >/dev/null 2>&1; then cleanup_failed=1; fi
  fi
  rm -rf -- "$runtime_dir" || cleanup_failed=1
  if [[ "$cleanup_failed" == 1 ]]; then
    printf 'ERROR: disposable globals restore cleanup was incomplete: container=%s volume=%s runtime_dir=%s\n' \
      "$container" "$volume" "$runtime_dir" >&2
    [[ "$status" != 0 ]] || status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

docker volume create "$volume" >/dev/null
volume_created=1
# The PostgreSQL entrypoint needs only the three identity-management caps while
# initializing its empty volume. It gets no network, no host port, and no
# Docker socket or host filesystem mount.
docker run -d --name "$container" --network none --read-only \
  --cap-drop ALL --cap-add CHOWN --cap-add SETGID --cap-add SETUID \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m --tmpfs /var/run/postgresql:rw,noexec,nosuid,size=8m \
  --mount "type=volume,source=$volume,target=/var/lib/postgresql" \
  -e "POSTGRES_USER=$source_postgres_role" -e POSTGRES_DB=postgres -e "POSTGRES_PASSWORD=$postgres_password" "$postgres_image" >/dev/null
for _attempt in $(seq 1 45); do
  if docker exec "$container" pg_isready -h 127.0.0.1 -U "$source_postgres_role" -d postgres >/dev/null 2>&1; then break; fi
  [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" == true ]] || break
  sleep 1
done
docker exec "$container" pg_isready -h 127.0.0.1 -U "$source_postgres_role" -d postgres >/dev/null 2>&1 || die 'disposable PostgreSQL did not become ready.'

# Do not pass a globals SQL file as a Docker bind mount. It is streamed once,
# and both stdout/stderr are suppressed because globals.sql can hold hashes.
awk -v duplicate="CREATE ROLE ${source_postgres_role};" '$0 != duplicate' "$backup_dir/globals.sql" \
  | docker exec -i "$container" psql -X -v ON_ERROR_STOP=1 -U "$source_postgres_role" -d postgres >/dev/null 2>/dev/null \
  || die 'globals SQL restore command failed.'
tablespace_count="$(docker exec "$container" psql -X -At -v ON_ERROR_STOP=1 -U "$source_postgres_role" -d postgres -c "select count(*) from pg_tablespace where spcname not in ('pg_default', 'pg_global')" 2>/dev/null)" || die 'could not inspect restored tablespaces.'
[[ "$tablespace_count" == 0 ]] || die 'restored globals contain a non-default tablespace.'
docker exec "$container" psql -X -At -v ON_ERROR_STOP=1 -U "$source_postgres_role" -d postgres -c "
  select 'role|' || encode(convert_to(rolname, 'UTF8'), 'hex') || '|' || rolsuper || '|' || rolinherit || '|' || rolcreaterole || '|' || rolcreatedb || '|' || rolcanlogin || '|' || rolreplication || '|' || rolbypassrls || '|' || rolconnlimit || '|' || coalesce(to_char(rolvaliduntil at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'), '')
    from pg_roles where rolname !~ '^pg_' order by rolname;
  select 'member|' || encode(convert_to(parent.rolname, 'UTF8'), 'hex') || '|' || encode(convert_to(child.rolname, 'UTF8'), 'hex') || '|' || encode(convert_to(grantor.rolname, 'UTF8'), 'hex') || '|' || membership.admin_option || '|' || membership.inherit_option || '|' || membership.set_option
    from pg_auth_members membership
    join pg_roles parent on parent.oid = membership.roleid
    join pg_roles child on child.oid = membership.member
    join pg_roles grantor on grantor.oid = membership.grantor
    where parent.rolname !~ '^pg_' and child.rolname !~ '^pg_'
    order by parent.rolname, child.rolname;" 2>/dev/null | LC_ALL=C sort > "$restored_inventory" || die 'could not inventory restored global roles.'
chmod 600 "$restored_inventory"
cmp -s "$backup_dir/globals.inventory" "$restored_inventory" || die 'restored secret-free role and membership inventory does not match the backup.'

docker rm -f "$container" >/dev/null
docker container inspect "$container" >/dev/null 2>&1 && die 'disposable PostgreSQL container still exists after cleanup.'
docker volume rm "$volume" >/dev/null
volume_created=0
docker volume inspect "$volume" >/dev/null 2>&1 && die 'disposable PostgreSQL volume still exists after cleanup.'
rm -rf -- "$runtime_dir"
trap - EXIT HUP INT TERM
completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
duration_seconds="$(( $(date +%s) - start_epoch ))"
printf 'GLOBALS_RESTORE_DRILL_VALID format=test-server-backup-v1 manifest_sha256=%s inventory_sha256=%s archive_created_at_utc=%s archive_verified_at_utc=%s completed_at_utc=%s duration_seconds=%s\n' \
  "$archive_manifest_sha" "$globals_inventory_sha" "$archive_created_at" "$archive_verified_at" "$completed_at" "$duration_seconds"
