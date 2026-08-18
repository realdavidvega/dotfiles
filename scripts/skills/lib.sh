#!/usr/bin/env bash
#
# Shared skill resolution for the global sync (restoration_scripts/04-skills-sync.sh)
# and the per-project materializer (scripts/skills/project.sh).
#
# A skill is a directory containing SKILL.md. It can come from three roots. The
# index is built in precedence order, later roots winning on a name collision:
#
#   1. $SKILLS_REGISTRY_REPO/external-skills/*/*  third-party, vendored at a
#                                                 pinned SHA by that repo's
#                                                 scripts/sync-external.sh
#   2. config/opencode/skills/*                   dotfiles-native
#   3. $SKILLS_REGISTRY_REPO/skills/*/*           registry-native
#
# Third-party content sits lowest so your own work always wins. Registry-native
# beating dotfiles-native is deliberate and pre-existing: playlist-sync and
# yt-dlp live in both trees and have always resolved to the registry copy.
#
# The registry's validate-skills.sh enforces that names are unique across its
# two trees, which is what keeps this flat namespace honest.
#
# Deliberately avoids bash 4 associative arrays — macOS still ships bash 3.2.
# The index is a TAB-separated "name<TAB>dir" string instead.

# shellcheck shell=bash

: "${DOTFILES_PATH:="$HOME/.dotfiles"}"

if [ -z "${SKILLS_REGISTRY_REPO:-}" ]; then
  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    SKILLS_REGISTRY_REPO="/mnt/c/Users/david/Workspace/repos/github/tools/skills-registry"
  elif [[ "$OSTYPE" =~ ^darwin ]]; then
    SKILLS_REGISTRY_REPO="$HOME/Workspace/repos/github/tools/skills-registry"
  else
    echo "Unsupported OS for skills sync: $OSTYPE" >&2
    exit 1
  fi
fi

SKILLS_EXTERNAL_DIR="$SKILLS_REGISTRY_REPO/external-skills"
SKILLS_NATIVE_DIR="$DOTFILES_PATH/config/opencode/skills"
SKILLS_PROFILES="$DOTFILES_PATH/config/opencode/skills.profiles.json"

# Roots whose symlinks this tooling owns and may therefore delete. Anything
# linked from elsewhere — or any real directory — is left alone.
MANAGED_ROOTS=(
  "$SKILLS_EXTERNAL_DIR/"
  "$SKILLS_NATIVE_DIR/"
  "$SKILLS_REGISTRY_REPO/skills/"
)

SKILL_INDEX=""

# Populate SKILL_INDEX with "name<TAB>dir" lines, last write winning.
skills_build_index() {
  local dir name

  SKILL_INDEX=""

  _index_add() {
    local d="$1"
    [ -d "$d" ] || return 0
    [ -f "$d/SKILL.md" ] || return 0
    # Plain concatenation, not $(printf ...) — command substitution strips the
    # trailing newline and would mash every entry onto one line.
    SKILL_INDEX="${SKILL_INDEX}$(basename "$d")	${d}"$'\n'
  }

  # external-skills is <domain>/<skill>, the same two-level shape as skills/.
  for dir in "$SKILLS_EXTERNAL_DIR"/*/*; do _index_add "$dir"; done
  for dir in "$SKILLS_NATIVE_DIR"/*; do _index_add "$dir"; done
  for dir in "$SKILLS_REGISTRY_REPO"/skills/*/*; do _index_add "$dir"; done
}

# resolve_skill <name> -> prints the source directory, or fails.
resolve_skill() {
  local name="$1" hit
  [ -n "$SKILL_INDEX" ] || skills_build_index
  # tail -1 implements "later root wins" without an associative array.
  hit="$(printf '%s\n' "$SKILL_INDEX" | awk -F'\t' -v n="$name" '$1 == n {print $2}' | tail -1)"
  [ -n "$hit" ] || return 1
  printf '%s\n' "$hit"
}

# list_skills -> every resolvable skill name, deduped and sorted.
list_skills() {
  [ -n "$SKILL_INDEX" ] || skills_build_index
  printf '%s\n' "$SKILL_INDEX" | awk -F'\t' 'NF {print $1}' | sort -u
}

# skills_prune_managed <target_dir>
# Remove only symlinks that point into a root we own. Real directories and
# foreign symlinks survive — that is what keeps hand-installed skills such as
# skill-creator and recall alive in ~/.agents/skills.
skills_prune_managed() {
  local target_dir="$1" existing link_target root
  [ -d "$target_dir" ] || return 0

  for existing in "$target_dir"/*; do
    [ -L "$existing" ] || continue
    link_target="$(readlink "$existing")"
    for root in "${MANAGED_ROOTS[@]}"; do
      case "$link_target" in
        "$root"*) rm "$existing"; break ;;
      esac
    done
  done
}

# skills_link <src_dir> <target_dir>
# ln -sfn links *inside* an existing real directory instead of replacing it,
# which would silently create <dest>/<name> and break the skill. Refuse.
skills_link() {
  local src="$1" target_dir="$2"
  local dest="$target_dir/$(basename "$src")"

  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "SKIP $(basename "$src"): $dest exists and is not a symlink" >&2
    return 0
  fi

  ln -sfn "$src" "$dest"
}

# profiles_global -> the skill names classified as global, one per line.
profiles_global() {
  _profiles_query global
}

# profiles_project_paths -> the configured project paths, ~ and $VAR expanded.
profiles_project_paths() {
  _profiles_query project_paths
}

# profiles_project_skills <path> -> skills mapped to that project.
profiles_project_skills() {
  _profiles_query project_skills "$1"
}

_profiles_query() {
  [ -f "$SKILLS_PROFILES" ] || {
    echo "Missing skills profile manifest: $SKILLS_PROFILES" >&2
    return 1
  }
  python3 - "$SKILLS_PROFILES" "$@" <<'PY'
import json, os, pathlib, sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if manifest.get("version") != 1:
    raise SystemExit("Unsupported skills.profiles.json version")

mode = sys.argv[2]

def expand(p):
    # os.path.expandvars leaves unset $VARs literal, which would produce a
    # nonsense path; treat those as "not configured on this machine".
    return os.path.realpath(os.path.expanduser(os.path.expandvars(p)))

if mode == "global":
    for name in manifest.get("global", []):
        print(name)
elif mode == "project_paths":
    for raw in manifest.get("projects", {}):
        if "$" in os.path.expandvars(raw):
            continue
        print(expand(raw))
elif mode == "project_skills":
    want = expand(sys.argv[3])
    for raw, skills in manifest.get("projects", {}).items():
        if "$" in os.path.expandvars(raw):
            continue
        if expand(raw) == want:
            for name in skills:
                print(name)
PY
}
