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

if ! command -v opencode >/dev/null 2>&1; then
  printf 'opencode is not installed or not on PATH.\n' >&2
  exit 1
fi

workspace="$(resolve_workspace)"
maybe_start_postgres "$workspace"

cd "$workspace"
exec opencode "${opencode_args[@]}"
