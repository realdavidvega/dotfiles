#!/usr/bin/env bash

set -euo pipefail

echo "Syncing OpenCode skills from pinned manifest..."

if ! command -v git &> /dev/null; then
  echo "git is required for skills sync" >&2
  exit 1
fi

"$DOTFILES_PATH/scripts/skills/sync.sh"
"$DOTFILES_PATH/scripts/skills/verify.sh"

echo "Skills sync complete"
