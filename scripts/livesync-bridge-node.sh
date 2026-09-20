#!/usr/bin/env bash
#
# Stage and run the LiveSync bridge on this machine.
#
#   livesync-bridge-node.sh stage|build|start|stop|status|logs
#
# The bridge translates between the vault on disk and CouchDB. It is a
# SINGLETON: two bridges authoring the same document ids into a replicated
# database produce conflict revisions, which is corruption rather than
# contention. So this never starts itself. Let the lease decide:
#
#   leased-service.sh bridge \
#     --start  "livesync-bridge-node.sh start" \
#     --stop   "livesync-bridge-node.sh stop" \
#     --status "livesync-bridge-node.sh status"
#
# Upstream ships no image and carries no licence, so the image is built from a
# pinned commit. Bump it deliberately, never automatically.

set -uo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
SOURCE_DIR="$DOTFILES_PATH/os/linux/srv/livesync-bridge"
BRIDGE_REPO="https://github.com/vrtmrz/livesync-bridge.git"
BRIDGE_COMMIT="${BRIDGE_COMMIT:-c3760beaa0851214da4860903445d7f6420ca025}"
NETWORK="personalcloud"

if [ "$(uname -s)" = "Darwin" ]; then
  ROOT="${BRIDGE_ROOT:-$HOME/.local/share/personal-cloud/livesync-bridge}"
  VAULT="${BLACK_VAULT:-$HOME/Workspace/repos/github/tools/black-vault}"
  # Rootless podman maps container root to the invoking host user, so files the
  # bridge writes land owned by you. A hardcoded 1000 is the hub's uid and is
  # wrong here.
  BRIDGE_UID="${BRIDGE_UID:-0}"
  SUDO=""
else
  ROOT="${BRIDGE_ROOT:-/srv/services/livesync-bridge}"
  VAULT="${BLACK_VAULT:-/srv/sync/blackvault}"
  BRIDGE_UID="${BRIDGE_UID:-1000}"
  SUDO="sudo"
fi

die() { printf '%s\n' "$*" >&2; exit 1; }

# Every image here is public. The user's ~/.docker/config.json may name
# credential helpers that fail, and a failing helper turns an anonymous pull
# into an authentication error rather than falling back. Point both clients at
# a clean, empty auth file so a public pull stays a public pull.
AUTH_DIR="${REGISTRY_AUTH_DIR:-$HOME/.local/share/personal-cloud/registry-auth}"
mkdir -p "$AUTH_DIR" && printf '{}' > "$AUTH_DIR/config.json"
export DOCKER_CONFIG="$AUTH_DIR"
export REGISTRY_AUTH_FILE="$AUTH_DIR/config.json"


runtime() {
  if [ -n "${BRIDGE_RUNTIME:-}" ]; then printf '%s\n' "$BRIDGE_RUNTIME"; return 0; fi
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && { printf 'docker\n'; return 0; }
  command -v podman >/dev/null 2>&1 && podman info >/dev/null 2>&1 && { printf 'podman\n'; return 0; }
  return 1
}
RUNTIME="$(runtime)" || die "No container runtime is answering. Start podman machine or Docker Desktop."

compose() { $SUDO "$RUNTIME" compose --project-directory "$ROOT" -f "$ROOT/compose.yaml" "$@"; }

stage() {
  [ -d "$VAULT" ] || die "vault not found at $VAULT"
  mkdir -p "$ROOT"
  if [ ! -d "$ROOT/.git" ]; then
    git clone --quiet "$BRIDGE_REPO" "$ROOT" || die "could not clone the bridge"
  fi
  git -C "$ROOT" fetch --quiet origin || true
  git -C "$ROOT" checkout --quiet "$BRIDGE_COMMIT" || die "could not pin $BRIDGE_COMMIT"

  # Upstream's own compose file makes "compose" ambiguous about which to use.
  [ -f "$ROOT/docker-compose.yml" ] && mv "$ROOT/docker-compose.yml" "$ROOT/docker-compose.yml.upstream"

  cat > "$ROOT/Dockerfile.node" <<DOCKER
# The uid matters: files the bridge writes into the vault must stay owned by
# whoever else writes it, Syncthing on the hub and you on a desktop.
FROM docker.io/denoland/deno:2.6.9
WORKDIR /app
COPY . .
RUN deno install --frozen \\
 && mkdir -p /deno-dir/location_data \\
 && chown -R ${BRIDGE_UID}:${BRIDGE_UID} /deno-dir /app
USER ${BRIDGE_UID}:${BRIDGE_UID}
CMD ["deno", "task", "run"]
DOCKER

  cat > "$ROOT/compose.yaml" <<COMPOSE
services:
  bridge:
    build:
      context: .
      dockerfile: Dockerfile.node
    image: localhost/livesync-bridge:node
    container_name: livesync-bridge
    # Never unless-stopped. The lease decides whether this host runs the
    # bridge, and a container that restarts itself would ignore that.
    restart: "no"
    volumes:
      - ./dat:/app/dat
      - ${VAULT}:/app/data/blackvault
      - bridge_local_storage:/deno-dir/location_data
    networks:
      - ${NETWORK}

volumes:
  bridge_local_storage:

networks:
  ${NETWORK}:
    external: true
COMPOSE

  mkdir -p "$ROOT/dat"
  [ -f "$ROOT/dat/config.json" ] || cp "$SOURCE_DIR/dat/config.sample.json" "$ROOT/dat/config.sample.json"
  printf 'staged %s at %s\n' "$BRIDGE_COMMIT" "$ROOT"
}

require_config() {
  [ -f "$ROOT/dat/config.json" ] || die "Missing $ROOT/dat/config.json.
Copy $ROOT/dat/config.sample.json, put the CouchDB password from KeePass in it,
point the couchdb peer at https://xebia-macbook.tailefee2c.ts.net, then:
  chmod 600 $ROOT/dat/config.json"
  chmod 600 "$ROOT/dat/config.json"
}

ensure_network() {
  $SUDO "$RUNTIME" network inspect "$NETWORK" >/dev/null 2>&1 \
    || $SUDO "$RUNTIME" network create "$NETWORK" >/dev/null
}

case "${1:-}" in
  stage)  stage ;;
  build)  stage; compose build ;;
  start)  require_config; ensure_network; compose up -d ;;
  stop)   compose down --remove-orphans 2>/dev/null || compose stop ;;
  status) $SUDO "$RUNTIME" ps --filter name=livesync-bridge --format '{{.Names}} {{.Status}}' | grep -q livesync-bridge ;;
  logs)   compose logs --follow bridge ;;
  *) sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
