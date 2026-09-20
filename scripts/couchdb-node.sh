#!/usr/bin/env bash
#
# Bring up a CouchDB node of the personal cloud on this machine.
#
#   couchdb-node.sh init|start|stop|status|endpoint|logs
#
# CouchDB is the one component here that is not a singleton: multi-master
# replication is what it is for, so every host may hold a copy and the phone
# can reach whichever is up. The LiveSync bridge and the Syncthing introducer
# are leased instead, because two of either corrupts rather than scales.
#
# It deliberately reuses the hub's own compose file and CORS settings from
# os/linux/srv/couchdb. A node provisioned from a different stack would answer
# on a different database name and never replicate with the hub.
#
# Linux and macOS. The runtime is docker or podman, whichever answers.

set -uo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
SOURCE_DIR="$DOTFILES_PATH/os/linux/srv/couchdb"
DATABASE="${COUCHDB_DATABASE:-blackvault}"
NETWORK="personalcloud"

if [ "$(uname -s)" = "Darwin" ]; then
  ROOT="${COUCHDB_ROOT:-$HOME/.local/share/personal-cloud/couchdb}"
  SUDO=""
else
  ROOT="${COUCHDB_ROOT:-/srv/services/couchdb}"
  SUDO="sudo"
fi
ENV_FILE="$ROOT/.env"

die() { printf '%s\n' "$*" >&2; exit 1; }

# Prefer whichever runtime actually answers. A docker CLI with no daemon is
# the normal state on a Mac that has podman, and silently failing on it wastes
# the most time of anything here.
runtime() {
  if [ -n "${COUCHDB_RUNTIME:-}" ]; then printf '%s\n' "$COUCHDB_RUNTIME"; return 0; fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    printf 'docker\n'; return 0
  fi
  if command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1; then
    printf 'podman\n'; return 0
  fi
  return 1
}

RUNTIME="$(runtime)" || die "No container runtime is answering.
  macOS: podman machine init && podman machine start   (or start Docker Desktop)
  Linux: sudo systemctl enable --now docker"

compose() { $SUDO "$RUNTIME" compose --project-directory "$ROOT" -f "$ROOT/compose.yaml" "$@"; }

ensure_network() {
  $SUDO "$RUNTIME" network inspect "$NETWORK" >/dev/null 2>&1 && return 0
  $SUDO "$RUNTIME" network create "$NETWORK" >/dev/null || die "could not create the $NETWORK network"
}

stage() {
  [ -d "$SOURCE_DIR" ] || die "missing $SOURCE_DIR, is DOTFILES_PATH right?"
  $SUDO mkdir -p "$ROOT/data" "$ROOT/local.d"
  $SUDO cp "$SOURCE_DIR/compose.yaml" "$ROOT/compose.yaml"
  $SUDO cp "$SOURCE_DIR"/local.d/*.ini "$ROOT/local.d/"
}

require_credentials() {
  [ -r "$ENV_FILE" ] || die "Missing $ENV_FILE.
Create it with the SAME credentials as the hub, so the phone's saved
connections differ only by host, and so replication authenticates:

  umask 077
  printf 'COUCHDB_USER=obsidian\nCOUCHDB_PASSWORD=<from KeePass>\n' > $ENV_FILE"
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
}

# require_valid_user applies to /_up as well, so an unauthenticated probe gets
# a 401 from a server that is working perfectly. Authenticate, and treat any
# answer at all as proof it is listening.
wait_for_couchdb() {
  local retries=30
  while [ "$retries" -gt 0 ]; do
    curl --fail --silent --user "$COUCHDB_USER:$COUCHDB_PASSWORD" \
      "http://127.0.0.1:5984/_up" >/dev/null && return 0
    sleep 2; retries=$((retries - 1))
  done
  die "CouchDB did not become healthy in time. Try: $0 logs"
}

provision_database() {
  local base="http://127.0.0.1:5984"
  curl --fail --silent --user "$COUCHDB_USER:$COUCHDB_PASSWORD" -X PUT "$base/$DATABASE" >/dev/null 2>&1
  curl --fail --silent --user "$COUCHDB_USER:$COUCHDB_PASSWORD" "$base/$DATABASE" >/dev/null \
    || die "database $DATABASE is not reachable with those credentials"
  printf 'database %s ready\n' "$DATABASE"
}

node_name() {
  tailscale status --json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'
}

case "${1:-}" in
  init)
    stage
    require_credentials
    ensure_network
    compose up -d || die "could not start CouchDB"
    wait_for_couchdb
    provision_database
    # Loopback only, fronted by tailscale serve. Obsidian on iOS refuses a
    # connection without a valid certificate, which is what serve supplies.
    tailscale serve --bg --https=443 http://127.0.0.1:5984 \
      || printf 'could not configure tailscale serve, do it by hand\n' >&2
    printf 'endpoint: https://%s\n' "$(node_name)"
    ;;
  start)  require_credentials; ensure_network; compose up -d ;;
  stop)   compose stop ;;
  status)
    printf 'runtime: %s\nroot: %s\n' "$RUNTIME" "$ROOT"
    compose ps
    require_credentials
    curl --fail --silent --user "$COUCHDB_USER:$COUCHDB_PASSWORD" \
      "http://127.0.0.1:5984/_up" || printf 'CouchDB is not answering locally\n'
    printf '\n'
    ;;
  endpoint) printf 'https://%s\n' "$(node_name)" ;;
  logs)   compose logs --follow couchdb ;;
  *) sed -n '3,18p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
