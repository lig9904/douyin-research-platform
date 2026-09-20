#!/usr/bin/env bash
# Read-only preflight for the host that will later receive the test deployment.
# It never opens an SSH connection, changes host state, or reads application secrets.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/test-server-preflight.sh --domain <public-fqdn> [options]

Options (or matching TEST_SERVER_PREFLIGHT_* environment variables):
  --domain <fqdn>             HTTPS hostname to reserve (required)
  --min-disk-gib <integer>    Minimum free space on / (default: 20)
  --ports <csv>               Required free TCP ports (default: 80,443)
  --egress-url <https-url>    Required HTTPS egress endpoint; repeatable
  --allow-pending-dns         Report missing hostname DNS as a warning
  --skip-egress               Do not perform outbound HTTP checks
  --help                      Show this help

Defaults for outbound checks are Docker Hub and GitHub. Run this script on the
candidate Linux host. It is diagnostic only: it does not install packages,
create files, alter firewall rules, or contact a deployment server.
EOF
}

failures=0
warnings=0
pass() { printf 'PASS %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; warnings=$((warnings + 1)); }
fail() { printf 'FAIL %s\n' "$*" >&2; failures=$((failures + 1)); }

domain="${TEST_SERVER_PREFLIGHT_DOMAIN:-}"
min_disk_gib="${TEST_SERVER_PREFLIGHT_MIN_DISK_GIB:-20}"
ports_csv="${TEST_SERVER_PREFLIGHT_PORTS:-80,443}"
allow_pending_dns="${TEST_SERVER_PREFLIGHT_ALLOW_PENDING_DNS:-0}"
skip_egress="${TEST_SERVER_PREFLIGHT_SKIP_EGRESS:-0}"
egress_urls=()
if [[ -n "${TEST_SERVER_PREFLIGHT_EGRESS_URLS:-}" ]]; then
  # Deliberately split only this documented space-delimited environment value.
  read -r -a egress_urls <<<"$TEST_SERVER_PREFLIGHT_EGRESS_URLS"
fi

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --domain) domain="${2:-}"; shift 2 ;;
    --min-disk-gib) min_disk_gib="${2:-}"; shift 2 ;;
    --ports) ports_csv="${2:-}"; shift 2 ;;
    --egress-url) egress_urls+=("${2:-}"); shift 2 ;;
    --allow-pending-dns) allow_pending_dns=1; shift ;;
    --skip-egress) skip_egress=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'ERROR: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$domain" ]]; then
  printf 'ERROR: --domain (or TEST_SERVER_PREFLIGHT_DOMAIN) is required.\n' >&2
  exit 2
fi
if [[ ! "$domain" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || \
  [[ "$domain" == *.example || "$domain" == *.invalid || "$domain" == *.test || \
     "$domain" == *.localhost || "$domain" == *.local || "$domain" == localhost ]]; then
  printf 'ERROR: HTTPS domain must be a concrete public FQDN, not a placeholder: %s\n' "$domain" >&2
  exit 2
fi
if [[ ! "$min_disk_gib" =~ ^[1-9][0-9]*$ ]]; then
  printf 'ERROR: --min-disk-gib must be a positive integer.\n' >&2
  exit 2
fi
if [[ ! "$ports_csv" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
  printf 'ERROR: --ports must be comma-separated TCP port numbers.\n' >&2
  exit 2
fi
IFS=',' read -r -a ports <<<"$ports_csv"
for port in "${ports[@]}"; do
  if (( port < 1 || port > 65535 )); then
    printf 'ERROR: invalid TCP port: %s\n' "$port" >&2
    exit 2
  fi
done
if [[ "$allow_pending_dns" != 0 && "$allow_pending_dns" != 1 ]] || [[ "$skip_egress" != 0 && "$skip_egress" != 1 ]]; then
  printf 'ERROR: boolean preflight environment values must be 0 or 1.\n' >&2
  exit 2
fi
if [[ "${#egress_urls[@]}" -eq 0 ]]; then
  egress_urls=(https://registry-1.docker.io/v2/ https://github.com/)
fi

if [[ "$(uname -s)" == Linux ]]; then
  pass 'Linux host detected'
else
  fail "Linux is required (detected: $(uname -s))"
fi

if docker --version >/dev/null 2>&1 && docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; then
  pass 'Docker client and daemon are available'
else
  fail 'Docker client/daemon is unavailable'
fi
if docker compose version >/dev/null 2>&1; then
  pass 'Docker Compose v2 is available'
else
  fail 'Docker Compose v2 is unavailable'
fi

available_kib="$(df -Pk / | awk 'NR == 2 {print $4}')"
if [[ "$available_kib" =~ ^[0-9]+$ ]] && (( available_kib >= min_disk_gib * 1024 * 1024 )); then
  pass "root filesystem has at least ${min_disk_gib} GiB free"
else
  fail "root filesystem needs ${min_disk_gib} GiB free (available KiB: ${available_kib:-unknown})"
fi

if command -v ss >/dev/null 2>&1; then
  listening="$(ss -ltnH 2>/dev/null || true)"
  for port in "${ports[@]}"; do
    if awk -v wanted="$port" '$4 ~ (":" wanted "$") { found=1 } END { exit !found }' <<<"$listening"; then
      fail "required TCP port ${port} is already listening"
    else
      pass "required TCP port ${port} is free"
    fi
  done
else
  fail 'ss is required to inspect TCP port availability'
fi

if getent ahosts "$domain" >/dev/null 2>&1; then
  pass "DNS resolves HTTPS domain ${domain}"
elif [[ "$allow_pending_dns" == 1 ]]; then
  warn "DNS does not yet resolve ${domain}; permitted by --allow-pending-dns"
else
  fail "DNS does not resolve HTTPS domain ${domain}"
fi

if [[ "$skip_egress" == 1 ]]; then
  warn 'outbound HTTPS checks were explicitly skipped'
else
  for egress_url in "${egress_urls[@]}"; do
    if [[ ! "$egress_url" =~ ^https://[^[:space:]@/?#]+(/[^[:space:]?#]*)?/?$ ]]; then
      fail 'egress URL must be credential-free HTTPS without query or fragment'
      continue
    fi
    egress_label="${egress_url#https://}"
    egress_label="${egress_label%%/*}"
    status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --connect-timeout 5 --max-time 15 "$egress_url" 2>/dev/null || true)"
    if [[ "$status" =~ ^[234][0-9][0-9]$ ]]; then
      pass "outbound HTTPS reachable: ${egress_label} (${status})"
    else
      fail "outbound HTTPS unavailable: ${egress_label} (HTTP ${status:-000})"
    fi
  done
fi

if (( failures > 0 )); then
  printf 'RESULT failed=%d warnings=%d\n' "$failures" "$warnings" >&2
  exit 1
fi
printf 'RESULT passed warnings=%d\n' "$warnings"
