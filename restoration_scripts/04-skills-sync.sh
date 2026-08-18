#!/usr/bin/env bash
#
# Refresh the skills-registry checkout and materialize skill symlinks.
#
# Two scopes, both declared in config/opencode/skills.profiles.json:
#
#   global   linked into ~/.claude/skills, ~/.agents/skills and ~/.codex/skills,
#            so they load in every session everywhere.
#   project  linked only into the projects that name them — handled by
#            scripts/skills/project.sh, invoked at the end of this script.
#
# A skill absent from the `global` list is opt-in. There is no per-skill
# enable/disable setting in Claude Code, Codex or OpenCode; presence in the
# directory is the only switch, which is why this is done with symlinks.

set -euo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
# shellcheck source=../scripts/skills/lib.sh
source "$DOTFILES_PATH/scripts/skills/lib.sh"

# Every agent's global skill root. OpenCode reads the first two; Codex reads
# ~/.agents/skills and ~/.codex/skills; Claude Code reads ~/.claude/skills.
GLOBAL_SKILL_DIRS=(
  "$HOME/.claude/skills"
  "$HOME/.agents/skills"
  "$HOME/.codex/skills"
)

FETCH=1
PROJECTS=1
for arg in "$@"; do
  case "$arg" in
    --no-fetch) FETCH=0 ;;
    --no-projects) PROJECTS=0 ;;
    -h|--help)
      echo "Usage: 04-skills-sync.sh [--no-fetch] [--no-projects]"
      echo "  --no-fetch     Only relink skills; never touch the registry checkout."
      echo "  --no-projects  Skip the per-project pass; refresh global skills only."
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

skills_build_index

GLOBAL_SKILLS="$(profiles_global)"

# Resolve everything up front. A typo in the manifest should abort loudly rather
# than quietly leave a skill missing from every session.
MISSING=""
while IFS= read -r name; do
  [ -n "$name" ] || continue
  resolve_skill "$name" >/dev/null || MISSING="$MISSING $name"
done <<< "$GLOBAL_SKILLS"

if [ -n "$MISSING" ]; then
  echo "Unresolvable global skill(s) in $SKILLS_PROFILES:$MISSING" >&2
  echo "Not found under any skill root. Fix the name or the source tree." >&2
  exit 1
fi

for target_dir in "${GLOBAL_SKILL_DIRS[@]}"; do
  mkdir -p "$target_dir"

  # Prune before linking, so demoting a skill out of `global` removes its link
  # on the next run for free. Only symlinks into a root we own are removed —
  # real directories and foreign symlinks (skill-creator, recall, signet, …)
  # are left exactly as they are.
  skills_prune_managed "$target_dir"

  while IFS= read -r name; do
    [ -n "$name" ] || continue
    skills_link "$(resolve_skill "$name")" "$target_dir"
  done <<< "$GLOBAL_SKILLS"
done

echo "Global skills synced: $(printf '%s\n' "$GLOBAL_SKILLS" | grep -c .) skill(s) into ${#GLOBAL_SKILL_DIRS[@]} directories"

if [ "$PROJECTS" -eq 1 ]; then
  echo
  bash "$DOTFILES_PATH/scripts/skills/project.sh" apply --all
fi

echo "Skills sync complete"
