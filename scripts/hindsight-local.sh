#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: hindsight-local.sh [--help]

Starts a local Hindsight backend for the OpenCode plugin on http://localhost:8888
using the local Ollama installation by default.
EOF
}

case "${1:-}" in
  --help|-h)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    printf 'Unknown argument: %s\n\n' "$1" >&2
    usage >&2
    exit 1
    ;;
esac

if ! command -v uvx >/dev/null 2>&1 && ! command -v uv >/dev/null 2>&1; then
  printf 'uv/uvx is required to launch local Hindsight.\n' >&2
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  printf 'ollama is required for the local Hindsight LLM provider.\n' >&2
  exit 1
fi

if ! ollama list | rg -q '^gemma4:12b\s'; then
  printf 'Required Ollama model gemma4:12b is not installed. Run: ollama pull gemma4:12b\n' >&2
  exit 1
fi

export HINDSIGHT_API_URL="${HINDSIGHT_API_URL:-http://localhost:8888}"
export HINDSIGHT_API_LLM_PROVIDER="${HINDSIGHT_API_LLM_PROVIDER:-ollama}"
export HINDSIGHT_API_LLM_MODEL="${HINDSIGHT_API_LLM_MODEL:-gemma4:12b}"
export HINDSIGHT_API_LLM_BASE_URL="${HINDSIGHT_API_LLM_BASE_URL:-http://127.0.0.1:11434/v1}"
export HINDSIGHT_API_DATABASE_URL="${HINDSIGHT_API_DATABASE_URL:-pg0://hindsight-mcp}"

if command -v uvx >/dev/null 2>&1; then
  exec uvx --from hindsight-api hindsight-local-mcp
fi

exec uv tool run --from hindsight-api hindsight-local-mcp
