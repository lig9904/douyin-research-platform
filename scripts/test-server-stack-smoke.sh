#!/usr/bin/env bash
# Runs the test-server Compose overlay as an isolated, disposable local stack.
# It deliberately has no Provider configuration and only talks to loopback.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BASE_COMPOSE="$ROOT_DIR/docker-compose.yml"
SERVER_COMPOSE="$ROOT_DIR/docker-compose.test-server.yml"
EXAMPLE_ENV="$ROOT_DIR/.env.test-server.example"

usage() {
  cat <<'EOF'
Usage: TEST_SERVER_STACK_SMOKE=YES scripts/test-server-stack-smoke.sh

Creates a temporary 0600 Compose env and self-signed localhost SAN certificate,
then starts an isolated local test-server stack. It does not read deployment
env files, does not contact Providers, and always removes its exact project
and volumes before exit.
EOF
}

[[ "${1:-}" != "--help" && "${1:-}" != "-h" ]] || { usage; exit 0; }
[[ $# == 0 ]] || { echo 'ERROR: this smoke test accepts no arguments.' >&2; usage >&2; exit 2; }
[[ "${TEST_SERVER_STACK_SMOKE:-}" == YES ]] || {
  echo 'ERROR: set TEST_SERVER_STACK_SMOKE=YES to run the disposable full-stack smoke test.' >&2
  exit 2
}

for required in docker curl openssl; do
  command -v "$required" >/dev/null 2>&1 || { echo "ERROR: $required is required." >&2; exit 2; }
done
docker compose version >/dev/null 2>&1 || { echo 'ERROR: Docker Compose v2 is required.' >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo 'ERROR: Docker daemon is not reachable.' >&2; exit 2; }

example_image() {
  local key="$1" count value
  count="$(awk -F= -v key="$key" '$1 == key {count++} END {print count + 0}' "$EXAMPLE_ENV")"
  [[ "$count" == 1 ]] || { echo "ERROR: $key must occur exactly once in the committed test-server env example." >&2; exit 1; }
  value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print}' "$EXAMPLE_ENV")"
  [[ "$value" =~ ^[a-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: $key in the committed test-server env example is not digest-pinned." >&2
    exit 1
  }
  printf '%s' "$value"
}

file_mode() {
  if stat -c '%a' "$1" >/dev/null 2>&1; then stat -c '%a' "$1"; else stat -f '%Lp' "$1"; fi
}

# Colima only bind-mounts configured host paths into its Linux VM. Keep this
# disposable directory under the repository's ignored work/ directory rather
# than macOS's per-user TMPDIR, which is not necessarily VM-visible.
RUNTIME_BASE="$ROOT_DIR/work"
mkdir -p "$RUNTIME_BASE"
RUNTIME_DIR="$(mktemp -d "$RUNTIME_BASE/test-server-stack-smoke.XXXXXX")"
case "$RUNTIME_DIR" in
  "$RUNTIME_BASE"/test-server-stack-smoke.*) ;;
  *) echo 'ERROR: mktemp returned an unexpected runtime directory.' >&2; exit 1 ;;
esac
PROJECT="test-server-smoke-$(date -u +%s)-$$"
ENV_FILE="$RUNTIME_DIR/stack.env"
TLS_DIR="$RUNTIME_DIR/tls"
AUTH_SENTINEL="smoke-auth-${PROJECT}"
QUERY_SENTINEL="smoke-query-${PROJECT}"

compose() {
  docker compose --project-name "$PROJECT" --env-file "$ENV_FILE" \
    -f "$BASE_COMPOSE" -f "$SERVER_COMPOSE" "$@"
}

published_port() {
  local service_port="$1" endpoint
  endpoint="$(compose port proxy "$service_port" 2>/dev/null | tail -n 1 || true)"
  # Docker may render a loopback bind as 0.0.0.0 here on some macOS VM
  # backends. HostConfig.PortBindings below is the authoritative bind check.
  endpoint="${endpoint##*:}"
  [[ "$endpoint" =~ ^[0-9]+$ && "$endpoint" != 0 ]] || return 1
  printf '%s' "$endpoint"
}

cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [[ -f "$ENV_FILE" ]]; then
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  rm -rf -- "$RUNTIME_DIR"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

umask 077
mkdir "$TLS_DIR"
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 1 \
  -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -keyout "$TLS_DIR/privkey.pem" -out "$TLS_DIR/fullchain.pem" >/dev/null 2>&1
# The proxy intentionally lacks CAP_DAC_OVERRIDE. On a native Linux bind mount
# it therefore needs ordinary read/traverse bits for this proxy-only mount.
# The containing RUNTIME_DIR remains mode 0700 on the host.
chmod 755 "$TLS_DIR"
chmod 644 "$TLS_DIR/privkey.pem" "$TLS_DIR/fullchain.pem"

postgres_password="$(openssl rand -hex 24)"
research_password="$(openssl rand -hex 24)"
windmill_image="$(example_image WINDMILL_IMAGE)"
postgres_image="$(example_image POSTGRES_IMAGE)"
nginx_image="$(example_image NGINX_IMAGE)"
{
  printf '%s\n' \
    "WINDMILL_IMAGE=$windmill_image" \
    "POSTGRES_IMAGE=$postgres_image" \
    "NGINX_IMAGE=$nginx_image" \
    'POSTGRES_USER=postgres' \
    "POSTGRES_PASSWORD=$postgres_password" \
    'POSTGRES_DB=windmill' \
    'RESEARCH_DB_NAME=douyin_research' \
    'RESEARCH_DB_USER=douyin_research' \
    "RESEARCH_DB_PASSWORD=$research_password" \
    "WINDMILL_DATABASE_URL=postgres://postgres:${postgres_password}@postgres:5432/windmill?sslmode=disable" \
    'WINDMILL_BASE_URL=https://localhost' \
    'WINDMILL_INTERNAL_URL=http://windmill_server:8000' \
    'TEST_SERVER_BIND_HOST=127.0.0.1' \
    'TEST_SERVER_HTTP_PORT=0' \
    'TEST_SERVER_HTTPS_PORT=0' \
    "TEST_SERVER_TLS_DIR=$TLS_DIR"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
[[ "$(file_mode "$ENV_FILE")" == 600 ]] || { echo 'ERROR: temporary env is not mode 0600.' >&2; exit 1; }

# Render before starting so accidental direct host ports fail before containers
# or volumes are created. The overlay must leave proxy as the sole publisher.
rendered="$(compose config)"
proxy_section="$(printf '%s\n' "$rendered" | sed -n '/^  proxy:/,/^  [a-zA-Z_].*:/p')"
[[ "$proxy_section" == *"published: \"0\""* || "$proxy_section" == *"published: 0"* ]] || {
  echo 'ERROR: disposable proxy ports were not rendered as ephemeral ports.' >&2; exit 1;
}

compose up --detach postgres windmill_server windmill_worker windmill_worker_native proxy >/dev/null

proxy_https_port=""
for _ in $(seq 1 90); do
  proxy_https_port="$(published_port 8443 || true)"
  if [[ -n "$proxy_https_port" ]] && \
    curl --fail --silent --show-error --cacert "$TLS_DIR/fullchain.pem" \
      -H "Authorization: Bearer $AUTH_SENTINEL" \
      "https://127.0.0.1:${proxy_https_port}/api/version?probe=${QUERY_SENTINEL}" >/dev/null; then
    break
  fi
  proxy_container="$(compose ps --all -q proxy)"
  if [[ -n "$proxy_container" && "$(docker inspect --format '{{.State.Running}}' "$proxy_container")" != true ]]; then
    echo 'ERROR: proxy exited before the HTTPS readiness probe succeeded.' >&2
    compose logs --no-color proxy >&2
    exit 1
  fi
  sleep 2
done
[[ -n "$proxy_https_port" ]] || { echo 'ERROR: proxy did not publish an HTTPS port.' >&2; exit 1; }
curl --fail --silent --show-error --cacert "$TLS_DIR/fullchain.pem" \
  "https://127.0.0.1:${proxy_https_port}/api/version" >/dev/null || { echo 'ERROR: HTTPS proxy never became healthy.' >&2; exit 1; }

proxy_http_port="$(published_port 8080)" || { echo 'ERROR: proxy did not publish an HTTP port.' >&2; exit 1; }
if curl --silent --show-error --max-time 10 "http://127.0.0.1:${proxy_http_port}/api/version?probe=${QUERY_SENTINEL}" >/dev/null 2>&1; then
  echo 'ERROR: plaintext HTTP unexpectedly returned a response.' >&2
  exit 1
fi

for service in postgres windmill_server windmill_worker windmill_worker_native proxy; do
  container="$(compose ps -q "$service")"
  [[ -n "$container" ]] || { echo "ERROR: $service has no container." >&2; exit 1; }
  [[ "$(docker inspect --format '{{.State.Running}}' "$container")" == true ]] || {
    echo "ERROR: $service is not running." >&2; exit 1;
  }
done
[[ "$(docker inspect --format '{{.State.Health.Status}}' "$(compose ps -q windmill_server)")" == healthy ]] || {
  echo 'ERROR: Windmill server is not healthy.' >&2; exit 1;
}
for service in postgres windmill_server windmill_worker windmill_worker_native; do
  bindings="$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$(compose ps -q "$service")")"
  [[ "$bindings" == null || "$bindings" == '{}' ]] || { echo "ERROR: $service has host port bindings." >&2; exit 1; }
done
proxy_inspect="$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}|{{json .HostConfig.CapDrop}}|{{json .HostConfig.CapAdd}}|{{json .HostConfig.SecurityOpt}}' "$(compose ps -q proxy)")"
[[ "$proxy_inspect" == true* && "$proxy_inspect" == *'"ALL"'* && "$proxy_inspect" == *'"CAP_CHOWN"'* && "$proxy_inspect" == *'"CAP_SETGID"'* && "$proxy_inspect" == *'"CAP_SETUID"'* && "$proxy_inspect" == *'no-new-privileges'* ]] || {
  echo 'ERROR: proxy read-only or privilege-drop contract is not active.' >&2; exit 1;
}
proxy_binding_rows="$(docker inspect --format '{{range $port, $bindings := .NetworkSettings.Ports}}{{range $bindings}}{{println $port .HostIp .HostPort}}{{end}}{{end}}' "$(compose ps -q proxy)")"
binding_count=0
while read -r container_port host_ip host_port; do
  [[ -n "$container_port" ]] || continue
  binding_count=$((binding_count + 1))
  [[ "$host_ip" == 127.0.0.1 && "$host_port" =~ ^[0-9]+$ && "$host_port" != 0 ]] || {
    echo 'ERROR: a proxy port is not bound to a concrete loopback-only host port.' >&2
    exit 1
  }
done <<< "$proxy_binding_rows"
[[ "$binding_count" == 2 ]] || { echo 'ERROR: proxy must publish exactly two loopback ports.' >&2; exit 1; }
proxy_logs="$(compose logs --no-color proxy)"
[[ "$proxy_logs" != *"$AUTH_SENTINEL"* && "$proxy_logs" != *"$QUERY_SENTINEL"* ]] || {
  echo 'ERROR: proxy logs exposed an Authorization or query sentinel.' >&2; exit 1;
}

echo "PASS: isolated project $PROJECT ran all five services through loopback TLS; plaintext was rejected; direct PostgreSQL/Windmill ports were absent; proxy hardening and log redaction held; no Provider calls were configured."
