#!/usr/bin/env bash
# Validate the ignored env file for the external-reverse-proxy test profile.
# It parses only the documented KEY=value form and never prints values.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BASE_COMPOSE="$ROOT_DIR/docker-compose.yml"
EXTERNAL_PROXY_COMPOSE="$ROOT_DIR/docker-compose.test-server-external-proxy.yml"

usage() {
  cat <<'EOF'
Usage: scripts/test-server-external-proxy-validate.sh --env-file <ignored-file> [--project <compose-project>] [--skip-local-bind-check]

Validates the profile for an externally managed HTTPS reverse proxy. It
requires an exact-mode-0600 env file, a concrete public HTTPS base URL, and a
RFC1918 IPv4 bind address with fixed TCP port 8000. PostgreSQL remains Docker
network-only. With Docker/Compose available it also validates the Compose
render without printing the rendered secret-bearing configuration. By default
the bind IPv4 must exist on this Linux host. --skip-local-bind-check is only
accepted in CI for schema/render validation; it must never be used for a real
deployment.
EOF
}

env_file=""
project="douyin-research-test"
skip_local_bind_check=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --env-file) env_file="${2:-}"; shift 2 ;;
    --project) project="${2:-}"; shift 2 ;;
    --skip-local-bind-check) skip_local_bind_check=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -f "$env_file" ]] || { echo 'ERROR: --env-file must name an existing file.' >&2; exit 2; }
[[ "$project" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || {
  echo 'ERROR: --project must be an explicit lowercase Compose project name.' >&2; exit 2;
}
env_mode="$(stat -c '%a' "$env_file" 2>/dev/null || stat -f '%Lp' "$env_file")"
[[ "$env_mode" == 600 ]] || { echo 'ERROR: --env-file must have exact mode 0600.' >&2; exit 2; }
if [[ "$skip_local_bind_check" == 1 && "${CI:-}" != true ]]; then
  echo 'ERROR: --skip-local-bind-check is restricted to CI schema/render validation.' >&2
  exit 2
fi

value_for() {
  local key="$1" count value
  count="$(awk -F= -v wanted="$key" '$1 == wanted {count++} END {print count + 0}' "$env_file")"
  [[ "$count" == 1 ]] || { echo "ERROR: $key must occur exactly once in --env-file." >&2; exit 2; }
  value="$(awk -F= -v wanted="$key" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "$env_file")"
  [[ -n "$value" && "$value" != CHANGE_ME* ]] || { echo "ERROR: $key is missing or still a placeholder." >&2; exit 2; }
  printf '%s' "$value"
}

base_url="$(value_for WINDMILL_BASE_URL)"
bind_host="$(value_for TEST_SERVER_WINDMILL_BIND_HOST)"
bind_port="$(value_for TEST_SERVER_WINDMILL_PORT)"
postgres_user="$(value_for POSTGRES_USER)"
postgres_password="$(value_for POSTGRES_PASSWORD)"
postgres_database="$(value_for POSTGRES_DB)"
research_password="$(value_for RESEARCH_DB_PASSWORD)"
research_user="$(value_for RESEARCH_DB_USER)"
research_database="$(value_for RESEARCH_DB_NAME)"
database_url="$(value_for WINDMILL_DATABASE_URL)"
internal_url="$(value_for WINDMILL_INTERNAL_URL)"
windmill_image="$(value_for WINDMILL_IMAGE)"
postgres_image="$(value_for POSTGRES_IMAGE)"

[[ "$base_url" =~ ^https://([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] && \
  [[ "$base_url" != *.example && "$base_url" != *.invalid && "$base_url" != *.test && "$base_url" != *.localhost && "$base_url" != *.local ]] || {
  echo 'ERROR: WINDMILL_BASE_URL must be a concrete public HTTPS FQDN without a path.' >&2; exit 2;
}
[[ "$bind_port" == 8000 ]] || { echo 'ERROR: TEST_SERVER_WINDMILL_PORT must be exactly 8000.' >&2; exit 2; }

# Match the existing deployment contract: `openssl rand -hex 24` yields 48
# lowercase hex characters. Keeping the password URL-safe also makes the
# required DATABASE_URL binding unambiguous without URL encoding.
for password_key in POSTGRES_PASSWORD RESEARCH_DB_PASSWORD; do
  password_value="$postgres_password"
  [[ "$password_key" == RESEARCH_DB_PASSWORD ]] && password_value="$research_password"
  [[ "$password_value" =~ ^[a-f0-9]{48}$ ]] || {
    echo "ERROR: $password_key must be a 48-character lowercase hexadecimal random value." >&2
    exit 2
  }
done
for identifier_key in POSTGRES_USER POSTGRES_DB RESEARCH_DB_USER RESEARCH_DB_NAME; do
  identifier_value="$postgres_user"
  [[ "$identifier_key" == POSTGRES_DB ]] && identifier_value="$postgres_database"
  [[ "$identifier_key" == RESEARCH_DB_USER ]] && identifier_value="$research_user"
  [[ "$identifier_key" == RESEARCH_DB_NAME ]] && identifier_value="$research_database"
  [[ "$identifier_value" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || {
    echo "ERROR: $identifier_key must be a simple lowercase SQL identifier." >&2; exit 2;
  }
done
[[ "$internal_url" == http://windmill_server:8000 ]] || {
  echo 'ERROR: WINDMILL_INTERNAL_URL must exactly equal http://windmill_server:8000.' >&2; exit 2;
}
for image_key in WINDMILL_IMAGE POSTGRES_IMAGE; do
  image_value="$windmill_image"
  [[ "$image_key" == POSTGRES_IMAGE ]] && image_value="$postgres_image"
  [[ "$image_value" =~ ^[a-z0-9][A-Za-z0-9._/:@-]*@sha256:[0-9a-f]{64}$ ]] || {
    echo "ERROR: $image_key must be a digest-pinned image reference." >&2; exit 2;
  }
done
[[ "$research_user" != "$postgres_user" && "$research_database" != "$postgres_database" ]] || {
  echo 'ERROR: research database/user must stay distinct from the Windmill PostgreSQL database/user.' >&2; exit 2;
}
expected_database_url="postgres://${postgres_user}:${postgres_password}@postgres:5432/${postgres_database}?sslmode=disable"
[[ "$database_url" == "$expected_database_url" ]] || {
  echo 'ERROR: WINDMILL_DATABASE_URL must exactly bind POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB.' >&2
  exit 2
}

IFS=. read -r octet_a octet_b octet_c octet_d extra <<<"$bind_host"
[[ -z "${extra:-}" && "$octet_a" =~ ^[0-9]+$ && "$octet_b" =~ ^[0-9]+$ && "$octet_c" =~ ^[0-9]+$ && "$octet_d" =~ ^[0-9]+$ ]] || {
  echo 'ERROR: TEST_SERVER_WINDMILL_BIND_HOST must be an IPv4 address.' >&2; exit 2;
}
octet_a=$((10#$octet_a))
octet_b=$((10#$octet_b))
octet_c=$((10#$octet_c))
octet_d=$((10#$octet_d))
for octet in "$octet_a" "$octet_b" "$octet_c" "$octet_d"; do
  (( octet <= 255 )) || { echo 'ERROR: TEST_SERVER_WINDMILL_BIND_HOST has an invalid IPv4 octet.' >&2; exit 2; }
done
if ! (( octet_a == 10 || (octet_a == 172 && octet_b >= 16 && octet_b <= 31) || (octet_a == 192 && octet_b == 168) )); then
  echo 'ERROR: TEST_SERVER_WINDMILL_BIND_HOST must be an RFC1918 private IPv4 address.' >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo 'BLOCKED: a reachable Docker daemon and Docker Compose v2 are required to validate the external-proxy deployment render.' >&2
  exit 3
fi
if [[ "$skip_local_bind_check" != 1 ]]; then
  if ! command -v ip >/dev/null 2>&1; then
    echo 'BLOCKED: iproute2 is required to verify TEST_SERVER_WINDMILL_BIND_HOST on this host.' >&2
    exit 3
  fi
  if ! ip -4 -o addr show | awk -v wanted="$bind_host" '{split($4, address, "/"); if (address[1] == wanted) found=1} END {exit !found}'; then
    echo 'BLOCKED: TEST_SERVER_WINDMILL_BIND_HOST is not configured on this host.' >&2
    exit 3
  fi
fi

# Keep the rendered configuration off stdout because it contains passwords.
docker compose --project-name "$project" --env-file "$env_file" \
  -f "$BASE_COMPOSE" -f "$EXTERNAL_PROXY_COMPOSE" config --quiet
echo 'PASS: external-proxy environment, local bind address, and Compose render are valid.'
