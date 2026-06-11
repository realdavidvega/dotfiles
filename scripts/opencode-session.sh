#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: opencode-session.sh [--with-postgres] [--help] [opencode args...]

Launch OpenCode for the Black Vault workspace using dotfiles-managed paths.

Options:
  --with-postgres  Best-effort start of a local postgres service via docker compose
  --help           Show this help
EOF
}

hindsight_url="${HINDSIGHT_API_URL:-http://localhost:8888}"

start_postgres=false
opencode_args=()

while (($# > 0)); do
  case "$1" in
    --with-postgres)
      start_postgres=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      opencode_args+=("$1")
      shift
      ;;
  esac
done

repo_has_content() {
  local repo_path="$1"

  [[ -d "$repo_path/.git" ]] || return 1

  if [[ -n "$(ls -A "$repo_path" 2>/dev/null | rg -v '^\.git$' || true)" ]]; then
    return 0
  fi

  return 1
}

resolve_workspace() {
  if [[ -n "${BLACK_VAULT_REPO:-}" ]] && repo_has_content "$BLACK_VAULT_REPO"; then
    printf '%s\n' "$BLACK_VAULT_REPO"
    return 0
  fi

  if [[ -n "${BLACK_VAULT:-}" ]] && [[ -d "$BLACK_VAULT" ]]; then
    printf '%s\n' "$BLACK_VAULT"
    return 0
  fi

  printf 'Unable to resolve Black Vault workspace. Checked BLACK_VAULT_REPO and BLACK_VAULT.\n' >&2
  exit 1
}

maybe_start_postgres() {
  local workspace="$1"
  local compose_file

  [[ "$start_postgres" == true ]] || return 0

  if ! command -v docker >/dev/null 2>&1; then
    printf 'docker not found; skipping postgres startup.\n' >&2
    return 0
  fi

  for compose_file in \
    "$workspace/docker-compose.yml" \
    "$workspace/docker-compose.yaml" \
    "$workspace/compose.yml" \
    "$workspace/compose.yaml"; do
    if [[ -f "$compose_file" ]]; then
      printf 'Starting postgres via %s...\n' "$compose_file" >&2
      docker compose -f "$compose_file" up -d postgres >/dev/null
      return 0
    fi
  done

  printf 'No docker compose file with postgres setup found in %s; continuing without starting postgres.\n' "$workspace" >&2
}

hindsight_healthcheck() {
  curl -fsS --max-time 2 "$hindsight_url/health" >/dev/null 2>&1
}

maybe_start_hindsight() {
  local launcher
  local startup_wait_seconds=45

  case "$hindsight_url" in
    http://localhost:8888|http://127.0.0.1:8888) ;;
    *)
      return 0
      ;;
  esac

  if hindsight_healthcheck; then
    return 0
  fi

  launcher="${DOTFILES_PATH:-}/scripts/hindsight-local.sh"
  if [[ -z "$launcher" ]] || [[ ! -x "$launcher" ]]; then
    printf 'Hindsight launcher not found at %s; continuing without auto-start.\n' "$launcher" >&2
    return 0
  fi

  if ! command -v tmux >/dev/null 2>&1; then
    printf 'tmux not found; continuing without Hindsight auto-start.\n' >&2
    return 0
  fi

  if ! tmux has-session -t hindsight-backend 2>/dev/null; then
    printf 'Starting local Hindsight backend via %s...\n' "$launcher" >&2
    tmux new-session -d -s hindsight-backend "$launcher"
  fi

  for _ in $(seq 1 "$startup_wait_seconds"); do
    if hindsight_healthcheck; then
      return 0
    fi
    sleep 1
  done

  printf 'Hindsight backend did not become healthy at %s within %ss; continuing anyway.\n' "$hindsight_url" "$startup_wait_seconds" >&2
}

if ! command -v opencode >/dev/null 2>&1; then
  printf 'opencode is not installed or not on PATH.\n' >&2
  exit 1
fi

workspace="$(resolve_workspace)"
maybe_start_postgres "$workspace"
maybe_start_hindsight

cd "$workspace"
exec opencode "${opencode_args[@]}"
