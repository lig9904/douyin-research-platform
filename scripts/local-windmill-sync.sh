#!/usr/bin/env bash
# Repeatable, local-only source synchronization. This script never creates,
# accepts, or persists an API token. Commands that write through the CLI need a
# workspace profile that the operator has already provisioned outside this repo.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WINDMILL_DIR="$ROOT_DIR/windmill"
WORKSPACE="content-research-local"
BASE_URL="http://127.0.0.1:18000"
CONFIG_DIR="${WMILL_CONFIG_DIR:-$HOME/.config/windmill-l3-review-local}"

usage() {
  cat <<'EOF'
Usage: scripts/local-windmill-sync.sh <preflight|metadata|sync|status>

This script deliberately cannot create or store a CLI token. metadata and sync
require a pre-existing local-only CLI workspace profile under
~/.config/windmill-l3-review-local (or WMILL_CONFIG_DIR); otherwise Windmill
CLI fails before making a write. Do not pass a token on the command line or
copy one into the repository.
EOF
}

require_stack() {
  "$ROOT_DIR/scripts/local-l3-env.sh" verify >/dev/null
}

require_cli() {
  command -v wmill >/dev/null 2>&1 || {
    echo "ERROR: Windmill CLI is not installed. Install it outside this script: npm install -g windmill-cli" >&2
    exit 1
  }
}

wmill_local() {
  WMILL_CONFIG_DIR="$CONFIG_DIR" wmill "$@"
}

cmd_preflight() {
  require_stack
  require_cli
  git -C "$ROOT_DIR" cat-file -e 1558677c40cc22239e660e269738619dfd05388d^{commit}
  test -f "$WINDMILL_DIR/wmill.yaml"
  test -f "$WINDMILL_DIR/f/content_research/research_db.resource.yaml"
  echo "[local-windmill] stack, CLI, pinned L3 dependency, and source manifest are ready."
}

cmd_metadata() {
  cmd_preflight
  (
    cd "$WINDMILL_DIR"
    wmill_local generate-metadata f/content_research
  )
  echo "[local-windmill] regenerated local locks and metadata; nothing was deployed."
}

cmd_sync() {
  cmd_preflight
  [[ -t 0 && -t 1 ]] || {
    echo "ERROR: sync requires an interactive TTY so the scoped diff can be reviewed and confirmed." >&2
    exit 1
  }
  (
    cd "$WINDMILL_DIR"
    # Do not add --yes. The CLI prints the scoped diff and asks before changing
    # the local Windmill workspace. keep-deleted is mandatory here: Secret
    # variables are intentionally absent from Git, so a mirror-style push would
    # otherwise delete locally provisioned Secrets even when skipSecrets=true.
    wmill_local sync push --workspace "$WORKSPACE" --skip-branch-validation --skip-secrets --keep-deleted
  )
  echo "[local-windmill] sync finished; run status to inspect deployed metadata."
}

cmd_status() {
  require_stack
  docker --context colima-l3-review-local compose \
    -p l3-review-local \
    --env-file "$ROOT_DIR/.env.l3-local" \
    -f "$ROOT_DIR/docker-compose.yml" \
    -f "$ROOT_DIR/docker-compose.l3-local.yml" \
    exec -T postgres psql -U postgres -d windmill -Atc \
    "select 'app=' || count(*) from app where workspace_id='$WORKSPACE'
     union all select 'raw_app=' || count(*) from raw_app where workspace_id='$WORKSPACE'
     union all select 'script_active=' || count(*) from script where workspace_id='$WORKSPACE' and archived=false
     union all select 'script_archived=' || count(*) from script where workspace_id='$WORKSPACE' and archived=true
     union all select 'flow=' || count(*) from flow where workspace_id='$WORKSPACE'
     union all select 'resource=' || count(*) from resource where workspace_id='$WORKSPACE'
     union all select 'variable_nonsecret=' || count(*) from variable where workspace_id='$WORKSPACE' and is_secret=false
     union all select 'variable_secret=' || count(*) from variable where workspace_id='$WORKSPACE' and is_secret=true
     union all select 'schedule=' || count(*) from schedule where workspace_id='$WORKSPACE'
     union all select 'trigger=' || count(*) from capture where workspace_id='$WORKSPACE';"
}

case "${1:-}" in
  preflight) cmd_preflight ;;
  metadata) cmd_metadata ;;
  sync) cmd_sync ;;
  status) cmd_status ;;
  *) usage; exit 2 ;;
esac
