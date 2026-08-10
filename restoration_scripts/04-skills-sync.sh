#!/usr/bin/env bash

set -euo pipefail

if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  SKILLS_REGISTRY_REPO="/mnt/c/Users/david/Workspace/repos/github/tools/skills-registry"
elif [[ "$OSTYPE" =~ ^darwin ]]; then
  SKILLS_REGISTRY_REPO="$HOME/Workspace/repos/github/tools/skills-registry"
else
  echo "Unsupported OS for skills sync: $OSTYPE" >&2
  exit 1
fi

FETCH=1
for arg in "$@"; do
  case "$arg" in
    --no-fetch) FETCH=0 ;;
    -h|--help)
      echo "Usage: 04-skills-sync.sh [--no-fetch]"
      echo "  --no-fetch  Only relink skills; never touch the registry checkout."
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$(dirname "$SKILLS_REGISTRY_REPO")"

if [ ! -d "$SKILLS_REGISTRY_REPO/.git" ]; then
  git clone git@github.com:realdavidvega/skills-registry.git "$SKILLS_REGISTRY_REPO"
elif [ "$FETCH" -eq 0 ]; then
  echo "Skipping registry fetch (--no-fetch); relinking current checkout"
elif [ -n "$(git -C "$SKILLS_REGISTRY_REPO" status --porcelain)" ]; then
  # reset --hard would destroy in-progress skill authoring. Relink what is on
  # disk instead, so the sync stays safe to run mid-edit.
  echo "Registry checkout has local changes; skipping fetch/reset and relinking as-is" >&2
else
  git -C "$SKILLS_REGISTRY_REPO" fetch origin
  git -C "$SKILLS_REGISTRY_REPO" reset --hard origin/main
fi

sync_skill_links() {
  local target_dir="$1"
  local existing
  local link_target
  local skill_dir
  local skill_name

  mkdir -p "$target_dir"

  for existing in "$target_dir"/*; do
    [ -L "$existing" ] || continue
    link_target="$(readlink "$existing")"
    case "$link_target" in
      "$DOTFILES_PATH/config/opencode/skills/"*|"$SKILLS_REGISTRY_REPO/skills/"*)
        rm "$existing"
        ;;
    esac
  done

  link_skill() {
    local src="$1"
    local dest="$target_dir/$(basename "$src")"

    # ln -sfn links *inside* an existing real directory instead of replacing it,
    # which would silently create <dest>/<name> and break the skill. Refuse.
    if [ -e "$dest" ] && [ ! -L "$dest" ]; then
      echo "SKIP $(basename "$src"): $dest exists and is not a symlink" >&2
      return
    fi

    ln -sfn "$src" "$dest"
  }

  for skill_dir in "$DOTFILES_PATH/config/opencode/skills"/*; do
    [ -d "$skill_dir" ] || continue
    link_skill "$skill_dir"
  done

  for skill_dir in "$SKILLS_REGISTRY_REPO"/skills/*/*; do
    [ -d "$skill_dir" ] || continue
    link_skill "$skill_dir"
  done
}

sync_skill_links "$HOME/.claude/skills"
sync_skill_links "$HOME/.agents/skills"

echo "Skills sync complete"
