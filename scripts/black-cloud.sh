#!/usr/bin/env bash

set -euo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
STACK_DIR="$DOTFILES_PATH/services/black-cloud"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/black-cloud"
ENV_FILE="$CONFIG_DIR/livesync.env"
DATABASE="black-vault"

usage() {
  printf '%s\n' \
    'Usage: black-cloud.sh <command>' \
    '' \
    'Commands:' \
    '  init       Create credentials, start CouchDB, and provision the vault database' \
    '  start      Start CouchDB' \
    '  stop       Stop CouchDB' \
    '  status     Show container and local CouchDB health' \
    '  endpoint   Print the private Tailscale HTTPS endpoint' \
    '  logs       Follow CouchDB logs'
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing command: %s\n' "$1" >&2
    exit 1
  fi
}

compose() {
  sudo docker compose --env-file "$ENV_FILE" -f "$STACK_DIR/compose.yaml" "$@"
}

load_credentials() {
  if [ ! -r "$ENV_FILE" ]; then
    printf 'Missing credentials: %s\nRun black-cloud.sh init first.\n' "$ENV_FILE" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

create_credentials() {
  if [ -e "$ENV_FILE" ]; then
    return
  fi
  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  umask 077
  local password
  password="$(openssl rand -base64 36 | tr -d '\n')"
  printf 'COUCHDB_USER=blackvault\nCOUCHDB_PASSWORD=%s\n' "$password" > "$ENV_FILE"
  printf 'Created private credentials at %s\n' "$ENV_FILE"
}

wait_for_couchdb() {
  local retries=30
  while [ "$retries" -gt 0 ]; do
    if curl --fail --silent "http://127.0.0.1:5984/_up" >/dev/null; then
      return
    fi
    sleep 2
    retries=$((retries - 1))
  done
  printf 'CouchDB did not become healthy in time.\n' >&2
  exit 1
}

provision_database() {
  local base_url="http://127.0.0.1:5984"
  curl --fail --silent --show-error \
    --user "$COUCHDB_USER:$COUCHDB_PASSWORD" \
    -X PUT "$base_url/$DATABASE" >/dev/null || true
  curl --fail --silent --show-error \
    --user "$COUCHDB_USER:$COUCHDB_PASSWORD" \
    "$base_url/$DATABASE" >/dev/null
  printf 'CouchDB database %s is ready.\n' "$DATABASE"
}

tailscale_dns_name() {
  sudo tailscale status --json | jq -r '.Self.DNSName | sub("[.]$"; "")'
}

case "${1:-}" in
  init)
    require_command docker
    require_command openssl
    require_command curl
    create_credentials
    load_credentials
    sudo systemctl enable --now docker
    compose up -d
    wait_for_couchdb
    provision_database
    sudo tailscale serve --bg --https=443 http://127.0.0.1:5984
    printf 'Private endpoint: https://%s\n' "$(tailscale_dns_name)"
    ;;
  start)
    load_credentials
    compose up -d
    ;;
  stop)
    load_credentials
    compose stop
    ;;
  status)
    load_credentials
    compose ps
    curl --fail --silent --show-error http://127.0.0.1:5984/_up | jq .
    ;;
  endpoint)
    require_command tailscale
    printf 'https://%s\n' "$(tailscale_dns_name)"
    ;;
  logs)
    load_credentials
    compose logs --follow couchdb
    ;;
  *)
    usage
    exit 1
    ;;
esac
