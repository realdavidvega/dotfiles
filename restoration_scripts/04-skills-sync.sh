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

CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
if [ -L "$CLAUDE_SKILLS_DIR" ]; then
  rm "$CLAUDE_SKILLS_DIR"
fi
mkdir -p "$CLAUDE_SKILLS_DIR"

for existing in "$CLAUDE_SKILLS_DIR"/*; do
  [ -L "$existing" ] && rm "$existing"
done

for skill_dir in "$DOTFILES_PATH/config/opencode/skills"/*; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  case "$skill_name" in
    playlist-sync|yt-dlp|podcast-extraction)
      continue
      ;;
  esac
  ln -sfn "$skill_dir" "$CLAUDE_SKILLS_DIR/$skill_name"
done

for skill_dir in "$SKILLS_REGISTRY_REPO"/skills/*/*; do
  [ -d "$skill_dir" ] || continue
  skill_name="$(basename "$skill_dir")"
  ln -sfn "$skill_dir" "$CLAUDE_SKILLS_DIR/$skill_name"
done

echo "Skills sync complete"
