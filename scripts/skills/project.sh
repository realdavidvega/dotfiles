#!/usr/bin/env bash
#
# Per-project skill activation.
#
# Neither Claude Code, Codex nor OpenCode has a per-skill enable/disable
# setting — presence in a skills directory IS the switch. So "activate skill X
# in project Y" means: symlink X into Y's agent skill directories. The mapping
# is declared in config/opencode/skills.profiles.json so it survives a restore.
#
# Usage:
#   project.sh apply [<path>]     materialize links for one project (default: cwd)
#   project.sh apply --all        every configured project present on this machine
#   project.sh add <skill>...     map skills to the current project, then apply
#   project.sh rm  <skill>...     unmap skills from the current project, then apply
#   project.sh list [<path>]      effective skills for a project (global + project)
#   project.sh status             what is configured, present, and linked
#
#   --exclude   with apply/add: also append the link paths to .git/info/exclude
#               so the links stay local to this machine and never get committed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

# Every agent reads a different per-project directory. Verified against
# Claude Code 2.1.233, Codex 0.147.0 and OpenCode 1.18.18.
PROJECT_SKILL_DIRS=".claude/skills .codex/skills .opencode/skills"

WRITE_EXCLUDE=0

usage() { sed -n '3,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

die() { echo "error: $*" >&2; exit 1; }

# The project root for a path: its git toplevel if there is one, else the path.
project_root() {
  local path="${1:-$PWD}"
  path="$(cd "$path" 2>/dev/null && pwd)" || die "no such directory: $1"
  git -C "$path" rev-parse --show-toplevel 2>/dev/null || printf '%s\n' "$path"
}

# Rewrite an absolute path back into a portable $VAR/~ form for the manifest,
# so the same entry resolves on macOS and WSL. Longest prefix wins.
contract_path() {
  local path="$1" var prefix best_var="" best_len=0
  for var in BLACK_VAULT BLACK_VAULT_REPO SKILLS_REGISTRY_REPO WORKSPACE HOME; do
    prefix="${!var:-}"
    [ -n "$prefix" ] || continue
    case "$path" in
      "$prefix"|"$prefix"/*)
        if [ "${#prefix}" -gt "$best_len" ]; then best_var="$var"; best_len="${#prefix}"; fi
        ;;
    esac
  done
  if [ -z "$best_var" ]; then printf '%s\n' "$path"; return; fi
  local rest="${path:$best_len}"
  if [ "$best_var" = "HOME" ]; then printf '~%s\n' "$rest"; else printf '$%s%s\n' "$best_var" "$rest"; fi
}

# --- apply ------------------------------------------------------------------

apply_project() {
  local root="$1"
  local skills sub target_dir src name linked=0 missing=""

  [ -d "$root" ] || { echo "skip (absent): $root"; return 0; }

  skills="$(profiles_project_skills "$root")"

  for sub in $PROJECT_SKILL_DIRS; do
    target_dir="$root/$sub"

    # Prune first so a skill removed from the manifest loses its link, then
    # relink. Only symlinks into a managed root are touched; a hand-authored
    # skill directory living in the project is never disturbed.
    skills_prune_managed "$target_dir"

    if [ -z "$skills" ]; then
      # Clean up the directory we created if nothing is mapped here anymore.
      [ -d "$target_dir" ] && rmdir "$target_dir" 2>/dev/null || true
      continue
    fi

    mkdir -p "$target_dir"
    while IFS= read -r name; do
      [ -n "$name" ] || continue
      if ! src="$(resolve_skill "$name")"; then
        case " $missing " in *" $name "*) ;; *) missing="$missing $name" ;; esac
        continue
      fi
      skills_link "$src" "$target_dir"
      linked=$((linked + 1))
    done <<< "$skills"
  done

  if [ -n "$missing" ]; then
    die "unresolvable skill(s) in $root:$missing
  Not found in any skill root. Check the name, or run 'upall --only skills' to
  refresh the registry checkout."
  fi

  if [ "$linked" -gt 0 ]; then
    echo "$root: linked $(printf '%s\n' "$skills" | grep -c .) skill(s) into ${PROJECT_SKILL_DIRS// /, }"
    [ "$WRITE_EXCLUDE" -eq 1 ] && write_exclude "$root"
    warn_untracked "$root"
  else
    echo "$root: no project skills mapped"
  fi
}

apply_all() {
  local root
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    apply_project "$root"
  done < <(profiles_project_paths)
}

# --- git hygiene ------------------------------------------------------------

# Opt-in only. These symlinks point at machine-local absolute paths, so they are
# usually noise in a shared repo — but that is your call, not this script's.
write_exclude() {
  local root="$1" exclude="$1/.git/info/exclude" sub
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || return 0
  exclude="$(git -C "$root" rev-parse --git-dir)/info/exclude"
  mkdir -p "$(dirname "$exclude")"
  for sub in $PROJECT_SKILL_DIRS; do
    grep -qxF "/$sub/" "$exclude" 2>/dev/null || printf '/%s/\n' "$sub" >> "$exclude"
  done
  echo "  excluded ${PROJECT_SKILL_DIRS// /, } via $exclude"
}

warn_untracked() {
  local root="$1" sub
  [ "$WRITE_EXCLUDE" -eq 1 ] && return 0
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 || return 0
  for sub in $PROJECT_SKILL_DIRS; do
    [ -d "$root/$sub" ] || continue
    if ! git -C "$root" check-ignore -q "$sub" 2>/dev/null; then
      echo "  note: $sub is not ignored by this repo — commit it, or re-run with --exclude to keep it local"
      return 0
    fi
  done
}

# --- manifest mutation ------------------------------------------------------

edit_mapping() {
  local action="$1"; shift
  local root; root="$(project_root "$PWD")"
  local key; key="$(contract_path "$root")"
  local name bad=""

  # Validate before touching the manifest — a typo should not leave a dangling
  # entry behind that every later `apply` then aborts on.
  if [ "$action" = "add" ]; then
    for name in "$@"; do
      resolve_skill "$name" >/dev/null || bad="$bad $name"
    done
    [ -z "$bad" ] || die "unknown skill(s):$bad
  Run 'skp skills' to list what resolves. Third-party skills must first be
  vendored in skills-registry: add a source to external-skills.sources.json
  there and run its scripts/sync-external.sh."
  fi

  python3 - "$SKILLS_PROFILES" "$action" "$root" "$key" "$@" <<'PY'
import json, os, pathlib, sys

path = pathlib.Path(sys.argv[1])
action, root, key = sys.argv[2], sys.argv[3], sys.argv[4]
names = sys.argv[5:]

manifest = json.loads(path.read_text(encoding="utf-8"))
projects = manifest.setdefault("projects", {})

def expand(p):
    return os.path.realpath(os.path.expanduser(os.path.expandvars(p)))

want = expand(root)
# Reuse an existing key that already points here, so the portable $VAR form
# written by a previous run is not replaced with a machine-specific path.
existing = next((k for k in projects if "$" not in os.path.expandvars(k) and expand(k) == want), None)
target = existing or key

current = list(projects.get(target, []))
if action == "add":
    for n in names:
        if n not in current:
            current.append(n)
elif action == "rm":
    current = [n for n in current if n not in names]

if current:
    projects[target] = sorted(current)
elif target in projects:
    del projects[target]

path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"{target}: {' '.join(sorted(current)) or '(none)'}")
PY

  apply_project "$root"
}

# --- read-only commands -----------------------------------------------------

cmd_list() {
  local root; root="$(project_root "${1:-$PWD}")"
  echo "project: $root"
  echo
  echo "global (everywhere):"
  profiles_global | sed 's/^/  /'
  echo
  echo "project-only (this repo):"
  local skills; skills="$(profiles_project_skills "$root")"
  if [ -n "$skills" ]; then printf '%s\n' "$skills" | sed 's/^/  /'; else echo "  (none)"; fi
}

cmd_status() {
  local root skills present sub linked

  echo "skill roots:"
  printf '  %-14s %s\n' "external" "$SKILLS_EXTERNAL_DIR"
  printf '  %-14s %s\n' "authored" "$SKILLS_NATIVE_DIR"
  echo "  $(list_skills | wc -l | tr -d ' ') skill(s) resolvable, $(profiles_global | wc -l | tr -d ' ') global"
  echo
  echo "projects:"
  while IFS= read -r root; do
    [ -n "$root" ] || continue
    skills="$(profiles_project_skills "$root" | tr '\n' ' ')"
    if [ -d "$root" ]; then present="present"; else present="ABSENT"; fi
    linked=0
    for sub in $PROJECT_SKILL_DIRS; do
      [ -d "$root/$sub" ] && linked=$((linked + $(find "$root/$sub" -maxdepth 1 -type l | wc -l)))
    done
    printf '  [%-7s] %s\n' "$present" "$root"
    printf '            want: %s\n' "${skills:-(none)}"
    printf '            linked: %s symlink(s) across %s dir(s)\n' "$linked" "$(printf '%s' "$PROJECT_SKILL_DIRS" | wc -w | tr -d ' ')"
  done < <(profiles_project_paths)
}

# --- main -------------------------------------------------------------------

ARGS=()
for arg in "$@"; do
  case "$arg" in
    --exclude) WRITE_EXCLUDE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) ARGS+=("$arg") ;;
  esac
done
set -- ${ARGS+"${ARGS[@]}"}

cmd="${1:-status}"
shift || true

case "$cmd" in
  apply)
    if [ "${1:-}" = "--all" ]; then apply_all; else apply_project "$(project_root "${1:-$PWD}")"; fi
    ;;
  add)
    [ $# -gt 0 ] || die "add needs at least one skill name"
    edit_mapping add "$@"
    ;;
  rm|remove)
    [ $# -gt 0 ] || die "rm needs at least one skill name"
    edit_mapping rm "$@"
    ;;
  list)   cmd_list "${1:-$PWD}" ;;
  status) cmd_status ;;
  skills) list_skills ;;
  *)      usage; exit 2 ;;
esac
