#!/usr/bin/env bash
# Disposable localhost security acceptance stack. Never uses the active context.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE=l3-security-local
CONTEXT=colima-l3-security-local
PROJECT=l3-security-local
ENV_FILE="$ROOT_DIR/.env.local-security"
RUNTIME_DIR="$ROOT_DIR/work/local-security"
BASE="$ROOT_DIR/docker-compose.yml"
OVERLAY="$ROOT_DIR/docker-compose.local-security.yml"
ACL_SQL="$ROOT_DIR/ops/local-security/verify-folder-acl.sql"

usage() { echo "Usage: $0 <init|start|status|verify-acl|verify-proxy|backup|restore-drill|verify|stop>"; }
env_value() { awk -F= -v wanted="$1" '$1==wanted {sub(/^[^=]*=/,""); print; exit}' "$ENV_FILE"; }
require_env() {
  [[ -f "$ENV_FILE" ]] || { echo "ERROR: run '$0 init' first." >&2; exit 1; }
  [[ "$(stat -f '%Lp' "$ENV_FILE")" == 600 ]] || { echo 'ERROR: local env must be 0600.' >&2; exit 1; }
}
compose() { docker --context "$CONTEXT" compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$BASE" -f "$OVERLAY" "$@"; }
ensure_runtime() {
  # `colima status` can itself fail during first-boot context registration;
  # `colima list` is the stable profile-level readiness indicator here.
  if ! colima list | awk -v profile="$PROFILE" '$1 == profile && $2 == "Running" { found=1 } END { exit !found }'; then
    colima start --profile "$PROFILE" --runtime docker --cpus 2 --memory 4 --disk 20 --dns 114.114.114.114 --dns 223.5.5.5 --activate=false
  fi
  if ! docker context inspect "$CONTEXT" >/dev/null 2>&1; then
    docker context create "$CONTEXT" --docker "host=unix://$HOME/.colima/$PROFILE/docker.sock" >/dev/null
  fi
  colima ssh --profile "$PROFILE" -- sudo rm -f /etc/resolv.conf
  colima ssh --profile "$PROFILE" -- sudo install -m 0644 "$ROOT_DIR/config/l3-security-local-resolv.conf" /etc/resolv.conf
  if ! colima ssh --profile "$PROFILE" -- cmp -s "$ROOT_DIR/config/l3-security-local-docker-daemon.json" /etc/docker/daemon.json; then
    colima ssh --profile "$PROFILE" -- sudo install -m 0644 "$ROOT_DIR/config/l3-security-local-docker-daemon.json" /etc/docker/daemon.json
    colima ssh --profile "$PROFILE" -- sudo systemctl restart docker
  fi
  docker --context "$CONTEXT" info >/dev/null
}
wait_for() {
  local kind="$1" port
  port="$(env_value LOCAL_SECURITY_HTTPS_PORT)"
  for _ in $(seq 1 45); do
    if [[ "$kind" == pg ]] && compose exec -T postgres pg_isready -U "$(env_value POSTGRES_USER)" -d windmill >/dev/null 2>&1; then return; fi
    if [[ "$kind" == proxy ]] && curl -fsS --cacert "$RUNTIME_DIR/certs/localhost.crt" "https://localhost:${port}/api/version" >/dev/null 2>&1; then return; fi
    sleep 2
  done
  compose logs "$([[ "$kind" == pg ]] && echo postgres || echo proxy)" >&2 || true
  echo "ERROR: local-security $kind did not become ready." >&2; exit 1
}
certificate() {
  mkdir -p "$RUNTIME_DIR/certs"; chmod 700 "$RUNTIME_DIR" "$RUNTIME_DIR/certs"
  if [[ ! -s "$RUNTIME_DIR/certs/localhost.key" || ! -s "$RUNTIME_DIR/certs/localhost.crt" ]]; then
    umask 077
    openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 7 -keyout "$RUNTIME_DIR/certs/localhost.key" -out "$RUNTIME_DIR/certs/localhost.crt" -subj '/CN=localhost' -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' >/dev/null 2>&1
    chmod 600 "$RUNTIME_DIR/certs/localhost.key"; chmod 644 "$RUNTIME_DIR/certs/localhost.crt"
  fi
}
cmd_init() {
  [[ ! -e "$ENV_FILE" ]] || { echo 'ERROR: refusing to overwrite local env.' >&2; exit 1; }
  local pg rp; pg="$(openssl rand -hex 24)"; rp="$(openssl rand -hex 24)"; umask 077
  {
    echo 'LOCAL_SECURITY_BIND_HOST=127.0.0.1'; echo 'LOCAL_SECURITY_HTTPS_PORT=28443'; echo 'LOCAL_SECURITY_POSTGRES_BIND_HOST=127.0.0.1'; echo 'LOCAL_SECURITY_POSTGRES_PORT=25432'; echo 'LOCAL_SECURITY_BASE_URL=https://localhost:28443'; echo "LOCAL_SECURITY_RUNTIME_DIR=$RUNTIME_DIR"
    echo 'POSTGRES_USER=postgres'; echo "POSTGRES_PASSWORD=$pg"; echo 'POSTGRES_DB=windmill'; echo "WINDMILL_DATABASE_URL=postgres://postgres:$pg@postgres:5432/windmill?sslmode=disable"; echo 'WINDMILL_INTERNAL_URL=http://windmill_server:8000'; echo 'RESEARCH_DB_NAME=douyin_research'; echo 'RESEARCH_DB_USER=douyin_research'; echo "RESEARCH_DB_PASSWORD=$rp"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"; certificate
  echo '[local-security] generated ignored local credentials and localhost TLS material; no secret was printed.'
}
cmd_verify_acl() {
  require_env; ensure_runtime; compose up -d postgres; wait_for pg
  compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$(env_value POSTGRES_USER)" -d "$(env_value POSTGRES_DB)" < "$ACL_SQL" >/dev/null
  echo '[local-security] verified CE Folder RLS for synthetic admin/reviewer/viewer identities.'
}
cmd_verify_proxy() {
  require_env; ensure_runtime; local port sentinel statuses limited
  port="$(env_value LOCAL_SECURITY_HTTPS_PORT)"; wait_for proxy
  openssl s_client -connect "127.0.0.1:$port" -servername localhost -CAfile "$RUNTIME_DIR/certs/localhost.crt" </dev/null 2>/dev/null | grep -q 'Verify return code: 0 (ok)'
  sentinel="Bearer local-security-no-log-$(openssl rand -hex 8)"
  curl -fsS --cacert "$RUNTIME_DIR/certs/localhost.crt" -H "Authorization: $sentinel" "https://localhost:$port/api/version" >/dev/null
  ! compose logs --no-color proxy | grep -Fq "$sentinel" || { echo 'ERROR: proxy log exposed authorization sentinel.' >&2; exit 1; }
  statuses="$RUNTIME_DIR/rate-statuses.txt"; : > "$statuses"
  export LOCAL_SECURITY_RATE_URL="https://localhost:$port/api/version" LOCAL_SECURITY_CA_FILE="$RUNTIME_DIR/certs/localhost.crt" LOCAL_SECURITY_STATUS_FILE="$statuses"
  seq 1 48 | xargs -P 24 -n 1 sh -c 'curl -s -o /dev/null -w "%{http_code}\n" --cacert "$LOCAL_SECURITY_CA_FILE" "$LOCAL_SECURITY_RATE_URL" >> "$LOCAL_SECURITY_STATUS_FILE"' >/dev/null
  ! grep -Ev '^(200|429)$' "$statuses" >/dev/null || { echo 'ERROR: unexpected proxy status in rate test.' >&2; exit 1; }
  limited="$(grep -c '^429$' "$statuses" || true)"; [[ "$limited" -gt 0 ]] || { echo 'ERROR: no 429 observed during rate probe.' >&2; exit 1; }
  echo "[local-security] TLS, Authorization redaction, and rate-limit baseline verified ($limited HTTP 429)."
}
backup_dir() { [[ -n "${1:-}" ]] && printf '%s\n' "$1" || printf '%s/backups/%s\n' "$RUNTIME_DIR" "$(date -u +%Y%m%dT%H%M%SZ)"; }
cmd_backup() {
  require_env; ensure_runtime; compose up -d postgres; wait_for pg
  local dst user wd rd; dst="$(backup_dir "${1:-}")"; case "$dst" in "$RUNTIME_DIR"/backups/*) ;; *) echo 'ERROR: backup must be under local-security runtime.' >&2; exit 2;; esac; [[ ! -e "$dst" ]] || { echo 'ERROR: refusing to overwrite backup.' >&2; exit 1; }
  mkdir -p "$dst"; chmod 700 "$dst"; user="$(env_value POSTGRES_USER)"; wd="$(env_value POSTGRES_DB)"; rd="$(env_value RESEARCH_DB_NAME)"
  compose exec -T postgres pg_dumpall -U "$user" --globals-only > "$dst/globals.sql"; compose exec -T postgres pg_dump -U "$user" -Fc "$wd" > "$dst/windmill.dump"; compose exec -T postgres pg_dump -U "$user" -Fc "$rd" > "$dst/research.dump"
  (cd "$dst" && shasum -a 256 globals.sql windmill.dump research.dump) > "$dst/SHA256SUMS"; printf 'created_at_utc=%s\ncompose_project=%s\nwindmill_db=%s\nresearch_db=%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" "$PROJECT" "$wd" "$rd" > "$dst/manifest.txt"
  echo "[local-security] backup complete: $dst"
}
cmd_restore_drill() {
  require_env; ensure_runtime; local src user wd rd rw=local_security_windmill_restore rr=local_security_research_restore sw sr aw ar
  src="${1:-}"; if [[ -z "$src" ]]; then src="$(backup_dir)"; cmd_backup "$src"; fi; case "$src" in "$RUNTIME_DIR"/backups/*) ;; *) echo 'ERROR: restore must use local-security backup.' >&2; exit 2;; esac
  [[ -f "$src/SHA256SUMS" && -f "$src/windmill.dump" && -f "$src/research.dump" ]] || { echo 'ERROR: incomplete backup.' >&2; exit 1; }; (cd "$src" && shasum -a 256 -c SHA256SUMS >/dev/null)
  compose up -d postgres; wait_for pg; user="$(env_value POSTGRES_USER)"; wd="$(env_value POSTGRES_DB)"; rd="$(env_value RESEARCH_DB_NAME)"
  cleanup_restore(){ compose exec -T postgres psql -U "$user" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $rw WITH (FORCE)" -c "DROP DATABASE IF EXISTS $rr WITH (FORCE)" >/dev/null || true; }; trap cleanup_restore RETURN; cleanup_restore
  compose exec -T postgres psql -U "$user" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $rw" -c "CREATE DATABASE $rr" >/dev/null
  compose exec -T postgres pg_restore -U "$user" --no-owner --no-privileges -d "$rw" < "$src/windmill.dump"; compose exec -T postgres pg_restore -U "$user" --no-owner --no-privileges -d "$rr" < "$src/research.dump"
  sw="$(compose exec -T postgres psql -U "$user" -d "$wd" -At -c 'select count(*) from workspace')"; sr="$(compose exec -T postgres psql -U "$user" -d "$rd" -At -c 'select count(*) from source_video')"; aw="$(compose exec -T postgres psql -U "$user" -d "$rw" -At -c 'select count(*) from workspace')"; ar="$(compose exec -T postgres psql -U "$user" -d "$rr" -At -c 'select count(*) from source_video')"
  [[ "$sw" == "$aw" && "$sr" == "$ar" ]] || { echo 'ERROR: restored row-count sentinel mismatch.' >&2; exit 1; }; echo '[local-security] restore drill passed; temporary restore databases will be removed.'
}
cmd_start(){ require_env; ensure_runtime; certificate; compose pull; compose up -d; wait_for pg; wait_for proxy; cmd_verify_acl; cmd_verify_proxy; }
cmd_status(){ require_env; ensure_runtime; compose ps; }
cmd_verify(){ cmd_verify_acl; cmd_verify_proxy; cmd_restore_drill; }
cmd_stop(){ require_env; ensure_runtime; compose down --remove-orphans; echo '[local-security] stopped only dedicated containers; volumes/backups retained.'; }
case "${1:-}" in init)cmd_init;; start)cmd_start;; status)cmd_status;; verify-acl)cmd_verify_acl;; verify-proxy)cmd_verify_proxy;; backup)shift;cmd_backup "${1:-}";; restore-drill)shift;cmd_restore_drill "${1:-}";; verify)cmd_verify;; stop)cmd_stop;; *)usage;exit 2;; esac
