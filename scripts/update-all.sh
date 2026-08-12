#!/usr/bin/env bash
#
# Update the tools that `brew upgrade` does NOT own.
#
# Brew keeps formulae and casks current, but a lot of this setup is installed by
# self-updating vendor scripts (codex), bunx installers (omo), uv, npm, or a git
# checkout (skills-registry). Those drift silently. This script updates them and
# — just as importantly — runs a doctor pass that catches the failure mode that
# is genuinely hard to notice: a stale binary earlier in $PATH shadowing the
# current one.
#
# Usage:
#   scripts/update-all.sh                    # doctor + update everything
#   scripts/update-all.sh --check            # doctor only, change nothing
#   scripts/update-all.sh --only codex,omo   # update a subset
#   scripts/update-all.sh --skip brew        # update everything but brew
#   scripts/update-all.sh --list             # show component names

set -uo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"

COMPONENTS=(brew casks ytdlp codex claude opencode omo uv npm skills)

CHECK_ONLY=0
ONLY=""
SKIP=""

FAILED=()
SKIPPED=()
WARNINGS=()

# --- output helpers ---------------------------------------------------------

if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""
fi

section() { printf '\n%s==> %s%s\n' "$C_BOLD$C_BLUE" "$*" "$C_RESET"; }
ok()      { printf '  %s✔%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn()    { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; WARNINGS+=("$*"); }
fail()    { printf '  %s✘%s %s\n' "$C_RED" "$C_RESET" "$*"; }
note()    { printf '  %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }

usage() {
  # Print the comment header (everything after the shebang, up to the first
  # non-comment line) as the help text.
  awk 'NR==1 && /^#!/ {next} /^#/ {sub(/^# ?/,""); print; next} {exit}' "$0"
  exit 0
}

# --- argument parsing -------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    --check)  CHECK_ONLY=1 ;;
    --only)   ONLY="${2:?--only requires a comma-separated list}"; shift ;;
    --only=*) ONLY="${1#*=}" ;;
    --skip)   SKIP="${2:?--skip requires a comma-separated list}"; shift ;;
    --skip=*) SKIP="${1#*=}" ;;
    --list)   printf '%s\n' "${COMPONENTS[@]}"; exit 0 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

in_list() {
  local needle="$1" list="$2" item
  IFS=',' read -ra item <<< "$list"
  for i in "${item[@]}"; do [ "$i" = "$needle" ] && return 0; done
  return 1
}

wants() {
  local name="$1"
  [ -n "$ONLY" ] && { in_list "$name" "$ONLY" || return 1; }
  [ -n "$SKIP" ] && { in_list "$name" "$SKIP" && return 1; }
  return 0
}

# Run an update step, recording failure without aborting the whole run.
run_step() {
  local label="$1"; shift
  if "$@"; then
    return 0
  fi
  fail "$label failed"
  FAILED+=("$label")
  return 1
}

have() { command -v "$1" >/dev/null 2>&1; }

# --- doctor -----------------------------------------------------------------

# The yt-dlp trap: a root-owned 2023 standalone binary in /usr/local/bin sat
# ahead of an up-to-date Homebrew keg that had never been linked. `yt-dlp
# --version` reported 2023, downloads silently produced only a thumbnail and a
# storyboard .mhtml, and nothing in `brew upgrade` could ever fix it. Anything
# installed by more than one route deserves this check.
SHADOW_CHECK_BINS=(yt-dlp ffmpeg node python3 codex claude opencode git)

# Prefixes that legitimately win over Homebrew: version managers and installers
# that are deliberately on $PATH first. A shadow from anywhere else — classically
# /usr/local/bin on an Apple Silicon box, where a years-old `sudo`-installed
# binary outlives every memory of installing it — is what we actually want to
# hear about.
shadow_is_intentional() {
  case "$1" in
    "$HOME"/.local/bin/*) return 0 ;;                      # hermes, codex, uv
    "$HOME"/.local/share/dotfiles/python-venv/bin/*) return 0 ;;
    "$HOME"/.rbenv/*|"$HOME"/.sdkman/*|"$HOME"/.cargo/*) return 0 ;;
    "$HOME"/.bun/*|"$HOME"/.opencode/*|"$HOME"/.nvm/*) return 0 ;;
    "$HOME"/miniconda3/*|"$HOME"/anaconda3/*) return 0 ;;
    "$DOTFILES_PATH"/*) return 0 ;;
    *) return 1 ;;
  esac
}

doctor_shadowing() {
  section "Doctor: PATH shadowing"
  local bin resolved brew_path brew_prefix found=0

  have brew || { note "brew not installed; skipping"; return 0; }
  brew_prefix="$(brew --prefix 2>/dev/null)"

  for bin in "${SHADOW_CHECK_BINS[@]}"; do
    have "$bin" || continue
    resolved="$(command -v "$bin")"

    # Only meaningful when Homebrew also provides the binary.
    brew_path="$brew_prefix/bin/$bin"
    [ -e "$brew_path" ] || continue
    [ "$resolved" = "$brew_path" ] && continue

    if shadow_is_intentional "$resolved"; then
      note "$bin uses $resolved (intentional override, not Homebrew)"
      continue
    fi

    warn "$bin resolves to $resolved but Homebrew provides $brew_path"
    note "fix: rm the stale copy (may need sudo), or reorder \$PATH"
    found=1
  done

  [ "$found" -eq 0 ] && ok "no shadowed binaries detected"
  return 0
}

doctor_unlinked_kegs() {
  have brew || return 0
  section "Doctor: unlinked Homebrew kegs"
  local out
  out="$(brew doctor 2>&1 | grep -A20 'not symlinked\|are not linked' || true)"
  if [ -n "$out" ]; then
    warn "Homebrew reports unlinked kegs:"
    printf '%s\n' "$out" | sed 's/^/    /'
    note "fix: brew link <formula>  (add --overwrite if it conflicts)"
  else
    ok "all kegs linked"
  fi
}

doctor_versions() {
  section "Doctor: installed versions"
  local bin
  for bin in yt-dlp codex claude opencode node uv; do
    if have "$bin"; then
      printf '  %-10s %s %s(%s)%s\n' "$bin" \
        "$("$bin" --version 2>&1 | head -1)" "$C_DIM" "$(command -v "$bin")" "$C_RESET"
    else
      printf '  %-10s %snot installed%s\n' "$bin" "$C_DIM" "$C_RESET"
    fi
  done
}

# --- update steps -----------------------------------------------------------

update_brew() {
  have brew || { SKIPPED+=("brew: not installed"); return 0; }
  section "Homebrew formulae"
  brew update && brew upgrade
}

update_casks() {
  have brew || { SKIPPED+=("casks: brew not installed"); return 0; }
  section "Homebrew casks"
  # --greedy catches casks that self-update and therefore never look outdated.
  brew upgrade --cask --greedy
}

update_ytdlp() {
  section "yt-dlp"
  if ! have yt-dlp; then
    SKIPPED+=("ytdlp: not installed")
    note "not installed"
    return 0
  fi
  # Installed via brew here, so the brew/casks steps do the actual upgrading.
  # What matters is confirming the binary you actually invoke is the new one.
  local resolved; resolved="$(command -v yt-dlp)"
  ok "$(yt-dlp --version 2>&1 | head -1) at $resolved"
}

update_codex() {
  have codex || { SKIPPED+=("codex: not installed"); return 0; }
  section "Codex CLI"
  codex update
}

update_claude() {
  have brew || { SKIPPED+=("claude: brew not installed"); return 0; }
  section "Claude Code"
  brew upgrade --cask claude-code
}

update_opencode() {
  have brew || { SKIPPED+=("opencode: brew not installed"); return 0; }
  section "OpenCode"
  brew upgrade anomalyco/tap/opencode
}

update_omo() {
  have bunx || { SKIPPED+=("omo: bunx not installed"); return 0; }
  section "omo / LazyCodex codex plugin"
  # The codex plugin marketplace entry for omo is a local path pointing at its
  # own cache, so `codex plugin` cannot re-fetch it. The upstream bunx installer
  # is the only supported update path. --platform codex deliberately leaves the
  # dotfiles-managed (and git-crypt encrypted) opencode config alone.
  bunx oh-my-openagent@latest install --no-tui --platform codex --no-codex-autonomous
}

update_uv() {
  have uv || { SKIPPED+=("uv: not installed"); return 0; }
  section "uv tools"
  uv tool upgrade --all
}

update_npm() {
  have npm || { SKIPPED+=("npm: not installed"); return 0; }
  section "npm globals"
  npm update -g
}

update_skills() {
  local script="$DOTFILES_PATH/restoration_scripts/04-skills-sync.sh"
  [ -f "$script" ] || { SKIPPED+=("skills: $script not found"); return 0; }
  section "skills registry"
  bash "$script"
}

# --- main -------------------------------------------------------------------

doctor_shadowing
doctor_unlinked_kegs

if [ "$CHECK_ONLY" -eq 1 ]; then
  doctor_versions
  section "Summary"
  if [ ${#WARNINGS[@]} -gt 0 ]; then
    fail "${#WARNINGS[@]} warning(s) — see above"
    exit 1
  fi
  ok "all checks passed"
  exit 0
fi

for component in "${COMPONENTS[@]}"; do
  wants "$component" || continue
  run_step "$component" "update_$component"
done

doctor_versions

section "Summary"
for s in "${SKIPPED[@]:-}"; do [ -n "$s" ] && note "skipped $s"; done

if [ ${#FAILED[@]} -gt 0 ]; then
  fail "failed: ${FAILED[*]}"
  exit 1
fi

if [ ${#WARNINGS[@]} -gt 0 ]; then
  warn "${#WARNINGS[@]} warning(s) — see doctor output above"
fi

ok "update complete"
