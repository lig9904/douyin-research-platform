#!/usr/bin/env bash
# Disposable Linux smoke for the externally managed reverse-proxy profile.
# It has no Provider configuration and removes only its own project/volumes.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BASE_COMPOSE="$ROOT_DIR/docker-compose.yml"
OVERLAY="$ROOT_DIR/docker-compose.test-server-external-proxy.yml"
EXAMPLE_ENV="$ROOT_DIR/.env.test-server-external-proxy.example"

[[ "${TEST_SERVER_EXTERNAL_PROXY_STACK_SMOKE:-}" == YES ]] || {
  echo 'ERROR: set TEST_SERVER_EXTERNAL_PROXY_STACK_SMOKE=YES to run this disposable smoke.' >&2; exit 2;
}
[[ "$(uname -s)" == Linux ]] || { echo 'BLOCKED: external-proxy smoke requires Linux.' >&2; exit 3; }
for required in docker ip jq openssl; do command -v "$required" >/dev/null 2>&1 || { echo "BLOCKED: $required is required." >&2; exit 3; }; done
docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1 || { echo 'BLOCKED: reachable Docker Compose v2 is required.' >&2; exit 3; }

image_value() {
  local key="$1" value
  value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$EXAMPLE_ENV")"
  [[ "$value" =~ ^[a-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}$ ]] || { echo "ERROR: $key is not digest pinned." >&2; exit 1; }
  printf '%s' "$value"
}

bind_host="$(ip -4 -o addr show scope global | awk '{split($4, a, "/"); ip=a[1]; if (ip ~ /^10\./ || ip ~ /^192\.168\./ || ip ~ /^172\.(1[6-9]|2[0-9]|3[01])\./) {print ip; exit}}')"
[[ -n "$bind_host" ]] || { echo 'BLOCKED: no RFC1918 IPv4 address is configured on this Linux host.' >&2; exit 3; }

runtime_base="$ROOT_DIR/work"
mkdir -p "$runtime_base"
runtime_dir="$(mktemp -d "$runtime_base/test-server-external-proxy-smoke.XXXXXX")"
case "$runtime_dir" in "$runtime_base"/test-server-external-proxy-smoke.*) ;; *) echo 'ERROR: unexpected runtime directory.' >&2; exit 1;; esac
project="test-server-external-proxy-smoke-$(date -u +%s)-$$"
env_file="$runtime_dir/stack.env"
compose() { docker compose --project-name "$project" --env-file "$env_file" -f "$BASE_COMPOSE" -f "$OVERLAY" "$@"; }
cleanup() { local status=$?; trap - EXIT HUP INT TERM; [[ -f "$env_file" ]] && compose down --volumes --remove-orphans >/dev/null 2>&1 || true; rm -rf -- "$runtime_dir"; exit "$status"; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

umask 077
postgres_password="$(openssl rand -hex 24)"
research_password="$(openssl rand -hex 24)"
windmill_image="$(image_value WINDMILL_IMAGE)"
postgres_image="$(image_value POSTGRES_IMAGE)"
printf '%s\n' \
  "WINDMILL_IMAGE=$windmill_image" "POSTGRES_IMAGE=$postgres_image" \
  'POSTGRES_USER=postgres' "POSTGRES_PASSWORD=$postgres_password" 'POSTGRES_DB=windmill' \
  'RESEARCH_DB_NAME=douyin_research' 'RESEARCH_DB_USER=douyin_research' "RESEARCH_DB_PASSWORD=$research_password" \
  "WINDMILL_DATABASE_URL=postgres://postgres:${postgres_password}@postgres:5432/windmill?sslmode=disable" \
  'WINDMILL_BASE_URL=https://research-ci.example.com' 'WINDMILL_INTERNAL_URL=http://windmill_server:8000' \
  "TEST_SERVER_WINDMILL_BIND_HOST=$bind_host" 'TEST_SERVER_WINDMILL_PORT=8000' >"$env_file"
chmod 600 "$env_file"

compose up --detach postgres windmill_server windmill_worker windmill_worker_native >/dev/null
server=""
for _ in $(seq 1 90); do
  server="$(compose ps -q windmill_server)"
  [[ -n "$server" && "$(docker inspect --format '{{.State.Health.Status}}' "$server" 2>/dev/null || true)" == healthy ]] && break
  sleep 2
done
[[ -n "$server" && "$(docker inspect --format '{{.State.Health.Status}}' "$server")" == healthy ]] || { compose logs --no-color windmill_server >&2; exit 1; }
for service in postgres windmill_server windmill_worker windmill_worker_native; do
  container="$(compose ps -q "$service")"; [[ -n "$container" && "$(docker inspect --format '{{.State.Running}}' "$container")" == true ]] || { echo "ERROR: $service is not running." >&2; exit 1; }
done
! compose ps --all --services | grep -Fxq proxy || { echo 'ERROR: external profile unexpectedly created proxy.' >&2; exit 1; }
postgres="$(compose ps -q postgres)"
[[ "$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$postgres")" == null || "$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$postgres")" == '{}' ]] || { echo 'ERROR: PostgreSQL has a host port binding.' >&2; exit 1; }
docker inspect --format '{{json .NetworkSettings.Ports}}' "$server" | jq -e --arg host "$bind_host" '
  .["8000/tcp"] | length == 1 and .[0].HostIp == $host and .[0].HostPort == "8000"
' >/dev/null || { echo 'ERROR: Windmill does not have exactly one expected private :8000 binding.' >&2; exit 1; }
echo "PASS: external-proxy smoke ran PostgreSQL and three Windmill services; no proxy/Provider; PostgreSQL unpublished; Windmill bound only to private :8000."
