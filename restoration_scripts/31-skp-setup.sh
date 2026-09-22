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

export SKP_REPO SKILLS_REGISTRY_REPO DOTFILES_PATH
paths="$HOME/.skp/paths.json"
if [ ! -e "$paths" ]; then
  python3 - "$paths" <<'PYTHON'
import json
import os
import sys
from pathlib import Path

home = Path.home()
workspace = Path(os.environ.get("WORKSPACE", home / "Workspace"))
paths = {
    "WORKSPACE": str(workspace),
    "DOTFILES_PATH": os.environ["DOTFILES_PATH"],
    "SKP_REPO": os.environ["SKP_REPO"],
    "SKILLS_REGISTRY_REPO": os.environ["SKILLS_REGISTRY_REPO"],
}
paths = {name: value for name, value in paths.items() if value}
for name in ("cortex", "academy", "projects"):
    key = name.upper() + "_REPO"
    paths[key] = os.environ.get(key, str(workspace / "repos/work" / name))
for candidate in (
    os.environ.get("BLACK_VAULT"),
    "/srv/sync/blackvault",
    os.environ.get("BLACK_VAULT_REPO"),
    str(home / "Documents/Black Vault"),
    str(workspace / "repos/github/tools/black-vault"),
):
    if candidate and (Path(candidate) / "AGENTS.md").is_file() and (
        Path(candidate) / "00 - Black"
    ).is_dir():
        paths["BLACK_VAULT"] = candidate
        paths["BLACK_VAULT_REPO"] = candidate
        break
# profiles.json addresses the transport project as $BLACK_SYSTEM_REPO, so an
# unresolvable value there silently drops its skills with `skip (absent)`.
# /srv is searched first for the reason 52-black-system.sh gives: on a machine
# with an encrypted home it is the only location readable before login, so that
# is where the checkout lives and a $WORKSPACE guess points at nothing.
for candidate in (
    os.environ.get("BLACK_SYSTEM_REPO"),
    "/srv/services/black-system",
    str(workspace / "repos/github/tools/black-system"),
):
    if candidate and (Path(candidate) / "deploy/install.sh").is_file():
        paths["BLACK_SYSTEM_REPO"] = candidate
        break
Path(sys.argv[1]).write_text(json.dumps({"version": 1, "paths": paths}, indent=2) + "\n")
PYTHON
  echo "seeded: $paths"
fi

export SKP_REPO SKILLS_REGISTRY_REPO
export PATH="$SKP_REPO/bin:$PATH"

if [ -f "$sources" ] && [ -f "$profiles" ]; then
  "$SKP_REPO/bin/skp" sync
else
  echo "skp is installed, but sync needs both $sources and $profiles."
fi
