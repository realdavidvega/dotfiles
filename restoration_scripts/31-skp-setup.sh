#!/usr/bin/env bash

set -euo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
SKP_REPO="${SKP_REPO:-}"
SKILLS_REGISTRY_REPO="${SKILLS_REGISTRY_REPO:-}"

find_checkout() {
  local name="$1"
  local dotfiles_checkout
  local candidate

  dotfiles_checkout="$(readlink -f "$DOTFILES_PATH")"
  for candidate in \
    "$(dirname "$dotfiles_checkout")/$name" \
    "$HOME/Workspace/repos/github/tools/$name" \
    "$HOME/workspace/repos/github/tools/$name"
  do
    if [ -d "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

if [ -z "$SKP_REPO" ]; then
  SKP_REPO="$(find_checkout skp || true)"
fi
if [ -z "$SKILLS_REGISTRY_REPO" ]; then
  SKILLS_REGISTRY_REPO="$(find_checkout skills-registry || true)"
fi

if [ ! -x "$SKP_REPO/bin/skp" ]; then
  echo "Skipping skp setup: no skp checkout with bin/skp was found."
  echo "Clone it beside the dotfiles checkout, then rerun this script."
  return 0 2>/dev/null || exit 0
fi

mkdir -p "$HOME/.local/bin" "$HOME/.skp"

launcher="$HOME/.local/bin/skp"
if [ -e "$launcher" ] && [ ! -L "$launcher" ]; then
  echo "Cannot manage $launcher because it is not a symlink."
  return 1 2>/dev/null || exit 1
fi
ln -sfn "$SKP_REPO/bin/skp" "$launcher"
echo "linked: $launcher -> $SKP_REPO/bin/skp"

sources="$HOME/.skp/sources"
if [ ! -e "$sources" ]; then
  if [ -d "$SKILLS_REGISTRY_REPO/skills" ]; then
    printf '%s\n' \
      '$SKILLS_REGISTRY_REPO/external-skills' \
      '$SKILLS_REGISTRY_REPO/skills' > "$sources"
    echo "seeded: $sources"
  else
    echo "Skipping sources seed: skills-registry checkout was not found."
  fi
fi

profiles="$HOME/.skp/profiles.json"
baseline="$DOTFILES_PATH/config/opencode/skills.profiles.json"
if [ ! -e "$profiles" ] && [ -f "$baseline" ]; then
  cp "$baseline" "$profiles"
  echo "seeded: $profiles"
fi

export SKP_REPO SKILLS_REGISTRY_REPO
export PATH="$SKP_REPO/bin:$PATH"

if [ -f "$sources" ] && [ -f "$profiles" ]; then
  "$SKP_REPO/bin/skp" sync
else
  echo "skp is installed, but sync needs both $sources and $profiles."
fi
