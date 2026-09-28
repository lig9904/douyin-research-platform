#!/usr/bin/env bash
# Read-only Windmill object readiness check for a separately provisioned test
# server. It deliberately never invokes `get`, `sync`, `push`, or any command
# that mutates the target workspace. Secret variables are checked only against
# a caller-supplied, value-free metadata export; the CLI is never asked to list
# or fetch variables because versions of that API may include values.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/test-server-windmill-preflight.sh \
    --workspace <local-cli-profile-name> --profile <wmill-config-directory> \
    --app <path> --script <path> [--script <path> ...] \
    --resource <path> [--resource <path> ...] \
    --secret-variable <path> [--secret-variable <path> ...] \
    --nonsecret-variable <path> [--nonsecret-variable <path> ...] \
    --variable-metadata <value-free-json-file>

All object paths are required explicitly. --workspace is the locally configured
Windmill CLI profile name (which may differ from the remote workspace ID).
--profile is an existing, operator-provisioned Windmill CLI config directory;
this script neither creates nor modifies it. The metadata file must contain exactly:
  {"variables":[{"path":"f/example/name","is_secret":true|false}]}

It is a safe metadata attestation, not a variable export: variable values,
tokens, and arbitrary metadata fields are rejected. Required --secret-variable
paths must be is_secret=true; required --nonsecret-variable paths must be false.
Only `wmill app|script|resource list --json` are called against the server.
EOF
}

workspace=""
profile=""
metadata=""
app_paths=()
script_paths=()
resource_paths=()
secret_variable_paths=()
nonsecret_variable_paths=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace) workspace="${2:-}"; shift 2 ;;
    --profile) profile="${2:-}"; shift 2 ;;
    --variable-metadata) metadata="${2:-}"; shift 2 ;;
    --app) app_paths+=("${2:-}"); shift 2 ;;
    --script) script_paths+=("${2:-}"); shift 2 ;;
    --resource) resource_paths+=("${2:-}"); shift 2 ;;
    --secret-variable) secret_variable_paths+=("${2:-}"); shift 2 ;;
    --nonsecret-variable) nonsecret_variable_paths+=("${2:-}"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

require_value() {
  local label="$1" value="$2"
  [[ -n "$value" ]] || { echo "ERROR: --$label is required." >&2; exit 2; }
}
require_items() {
  local label="$1"; shift
  ((${#@} > 0)) || { echo "ERROR: at least one --$label is required." >&2; exit 2; }
  local item
  for item in "$@"; do
    [[ -n "$item" ]] || { echo "ERROR: --$label requires a non-empty path." >&2; exit 2; }
  done
}

require_value workspace "$workspace"
require_value profile "$profile"
require_value variable-metadata "$metadata"
require_items app "${app_paths[@]}"
require_items script "${script_paths[@]}"
require_items resource "${resource_paths[@]}"
require_items secret-variable "${secret_variable_paths[@]}"
require_items nonsecret-variable "${nonsecret_variable_paths[@]}"
[[ -d "$profile" ]] || { echo "ERROR: profile directory does not exist." >&2; exit 2; }
[[ -f "$metadata" ]] || { echo "ERROR: variable metadata file does not exist." >&2; exit 2; }
command -v wmill >/dev/null 2>&1 || { echo "ERROR: wmill CLI is not installed." >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 2; }

# The filter accepts common list response shapes but emits only a path list.
# It keeps raw remote JSON in a pipe, never a shell variable, file, or output.
safe_paths_from_cli() {
  local kind="$1"
  WMILL_CONFIG_DIR="$profile" wmill --workspace "$workspace" "$kind" list --json 2>/dev/null |
    python3 -c '
import json, sys
payload = json.load(sys.stdin)
items = payload.get("items", payload) if isinstance(payload, dict) else payload
if not isinstance(items, list):
    raise SystemExit("invalid list response")
paths = []
for item in items:
    if isinstance(item, str):
        paths.append(item)
    elif isinstance(item, dict) and isinstance(item.get("path"), str):
        paths.append(item["path"])
    else:
        raise SystemExit("invalid list item")
print("\n".join(sorted(set(paths))))
'
}

contains_path() {
  local paths="$1" wanted="$2"
  [[ $'\n'"$paths"$'\n' == *$'\n'"$wanted"$'\n'* ]]
}

check_remote_kind() {
  local kind="$1"; shift
  local listed path
  if ! listed="$(safe_paths_from_cli "$kind")"; then
    echo "ERROR: unable to read $kind metadata from Windmill." >&2
    exit 1
  fi
  for path in "$@"; do
    if ! contains_path "$listed" "$path"; then
      echo "MISSING $kind path=$path" >&2
      exit 1
    fi
    echo "READY $kind path=$path"
  done
}

check_metadata_variables() {
  python3 - "$metadata" "${secret_variable_paths[@]}" --nonsecret "${nonsecret_variable_paths[@]}" <<'PY'
import json
import sys
from pathlib import Path

metadata_path = Path(sys.argv[1])
args = sys.argv[2:]
separator = args.index("--nonsecret")
secrets = args[:separator]
nonsecrets = args[separator + 1:]

try:
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit("ERROR: variable metadata is unreadable or invalid JSON")

if not isinstance(payload, dict) or set(payload) != {"variables"}:
    raise SystemExit("ERROR: variable metadata must contain only variables")
variables = payload["variables"]
if not isinstance(variables, list):
    raise SystemExit("ERROR: variable metadata variables must be a list")

actual = {}
for entry in variables:
    if not isinstance(entry, dict) or set(entry) != {"path", "is_secret"}:
        raise SystemExit("ERROR: variable metadata contains non-metadata fields")
    path, is_secret = entry.get("path"), entry.get("is_secret")
    if not isinstance(path, str) or not path or type(is_secret) is not bool:
        raise SystemExit("ERROR: variable metadata entry is invalid")
    if path in actual:
        raise SystemExit("ERROR: variable metadata contains duplicate paths")
    actual[path] = is_secret

for path in secrets:
    if actual.get(path) is not True:
        raise SystemExit(f"MISSING_OR_INVALID secret-variable path={path}")
    print(f"READY secret-variable path={path} is_secret=true")
for path in nonsecrets:
    if actual.get(path) is not False:
        raise SystemExit(f"MISSING_OR_INVALID nonsecret-variable path={path}")
    print(f"READY nonsecret-variable path={path} is_secret=false")
PY
}

check_remote_kind app "${app_paths[@]}"
check_remote_kind script "${script_paths[@]}"
check_remote_kind resource "${resource_paths[@]}"
check_metadata_variables
echo "[test-server-windmill] read-only object preflight passed; no variable values were requested."
