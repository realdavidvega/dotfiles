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

mkdir -p "$(dirname "$SKILLS_REGISTRY_REPO")"

if [ -d "$SKILLS_REGISTRY_REPO/.git" ]; then
  git -C "$SKILLS_REGISTRY_REPO" fetch origin
  git -C "$SKILLS_REGISTRY_REPO" reset --hard origin/main
else
  git clone git@github.com:realdavidvega/skills-registry.git "$SKILLS_REGISTRY_REPO"
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

  for skill_dir in "$DOTFILES_PATH/config/opencode/skills"/*; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    ln -sfn "$skill_dir" "$target_dir/$skill_name"
  done

  for skill_dir in "$SKILLS_REGISTRY_REPO"/skills/*/*; do
    [ -d "$skill_dir" ] || continue
    skill_name="$(basename "$skill_dir")"
    ln -sfn "$skill_dir" "$target_dir/$skill_name"
  done
}

sync_skill_links "$HOME/.claude/skills"
sync_skill_links "$HOME/.agents/skills"

echo "Skills sync complete"
