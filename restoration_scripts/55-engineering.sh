#!/usr/bin/env bash

set -u

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping engineering applier setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
PRODUCT_NAME="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

if [ "$OS_ID" != "linuxmint" ]; then
  echo "Skipping engineering applier setup: requires Linux Mint."
  return 0 2>/dev/null || exit 0
fi

# Same declaration as 52-black-system.sh: only the hub writes engineering task
# fields, so only the hub runs the applier.
ROLE_FILE="${BLACK_SYSTEM_ROLE_FILE:-/etc/black-system-role}"
ROLE="${BLACK_SYSTEM_ROLE:-$([ -r "$ROLE_FILE" ] && tr -d '[:space:]' <"$ROLE_FILE")}"
if [ "$ROLE" != "hub" ] && [ "$PRODUCT_NAME" != "MacBookPro12,1" ]; then
  echo "Skipping engineering applier setup: this host is not declared a hub."
  echo "Declare it with: echo hub | sudo tee $ROLE_FILE"
  return 0 2>/dev/null || exit 0
fi

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
ROOT="/srv/services/engineering"
UNIT_SOURCE_DIR="$DOTFILES_PATH/os/linux/srv/engineering"

# The unit runs as its User=, and the staged tree, its lock and the identity
# below must all be that user's.
UNIT_USER="$(sed -n 's/^User=//p' "$UNIT_SOURCE_DIR/engineering-apply.service")"
if [ "$(id -un)" != "$UNIT_USER" ]; then
  echo "blocked: run this as $UNIT_USER, the unit's user. It asks sudo only to install the units."
  return 1 2>/dev/null || exit 1
fi
SYSTEM_ENV="/srv/services/system/system.env"
STAGE_REF="${ENGINEERING_STAGE_REF:-origin/main}"
# Published path in the staged tree, then its path in skills-registry.
STAGED_FILES=(
  "engineering-vault/SKILL.md skills/engineering/engineering-vault/SKILL.md"
  "engineering-vault/scripts/engineering.py skills/engineering/engineering-vault/scripts/engineering.py"
  "sdlc-init/assets/check_gates.py skills/engineering/sdlc-init/assets/check_gates.py"
  "project-wiki/scripts/lint_wiki.py skills/engineering/project-wiki/scripts/lint_wiki.py"
)

find_checkout() {
  local name="$1" candidate
  for candidate in \
    "/srv/services/$name" \
    "$(dirname "$(readlink -f "$DOTFILES_PATH")")/$name" \
    "$HOME/Workspace/repos/github/tools/$name" \
    "$HOME/workspace/repos/github/tools/$name"; do
    if [ -d "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

REGISTRY="${SKILLS_REGISTRY_REPO:-$(find_checkout skills-registry || true)}"
if [ -z "$REGISTRY" ] || ! git -C "$REGISTRY" rev-parse --git-dir >/dev/null 2>&1; then
  echo 'blocked: no skills-registry checkout found, set SKILLS_REGISTRY_REPO'
  return 1 2>/dev/null || exit 1
fi
if ! git -C "$REGISTRY" fetch -q origin; then
  echo "warning: could not fetch skills-registry, staging $STAGE_REF as last fetched"
fi
STAGED_SHA="$(git -C "$REGISTRY" rev-parse --verify -q "$STAGE_REF^{commit}" 2>/dev/null || true)"
if [ -z "$STAGED_SHA" ]; then
  echo "blocked: $STAGE_REF does not name a commit in ${REGISTRY:-skills-registry}"
  return 1 2>/dev/null || exit 1
fi

GIT_NAME="$(git config --global user.name || true)"
GIT_EMAIL="$(git config --global user.email || true)"
if [ -z "$GIT_NAME" ] || [ -z "$GIT_EMAIL" ]; then
  echo 'blocked: set git config --global user.name and user.email first'
  return 1 2>/dev/null || exit 1
fi
if [ ! -w "$SYSTEM_ENV" ]; then
  echo "blocked: $SYSTEM_ENV is missing or not writable, and the unit reads it. Run 52-black-system.sh first."
  return 1 2>/dev/null || exit 1
fi

# Staged from a commit rather than the working tree, so a branch or a half-made
# edit in the checkout never becomes the code the hub runs.
if ! install -d -m 0755 "$ROOT" || ! NEXT="$(mktemp -d "$ROOT/.next.XXXXXX")"; then
  echo "blocked: cannot write $ROOT. Create it once with: sudo install -d -o $UNIT_USER $ROOT"
  return 1 2>/dev/null || exit 1
fi
for ENTRY in "${STAGED_FILES[@]}"; do
  TARGET="$NEXT/${ENTRY%% *}"
  mkdir -p "$(dirname "$TARGET")"
  if ! git -C "$REGISTRY" show "$STAGED_SHA:${ENTRY#* }" >"$TARGET"; then
    echo "blocked: ${ENTRY#* } is missing at $STAGE_REF"
    rm -rf "$NEXT"
    return 1 2>/dev/null || exit 1
  fi
done
chmod 0755 "$NEXT/engineering-vault/scripts/engineering.py"

# The commit identity comes first, so the home's own config, included after it
# and read whenever the ecryptfs home is unlocked, overrides it.
git config --file "$NEXT/gitconfig" user.name "$GIT_NAME"
git config --file "$NEXT/gitconfig" user.email "$GIT_EMAIL"
git config --file "$NEXT/gitconfig" include.path "$HOME/.gitconfig"
printf 'skills-registry %s %s\n' "$STAGED_SHA" "$STAGE_REF" >"$NEXT/STAGED"

# Swapped in under the applier's own lock, so no run reads a half-staged tree.
# Each part is moved aside before its replacement lands, and the old copies are
# removed only once every part is in place.
if ! flock "$ROOT/apply.lock" bash -c '
  set -e
  root="$1" next="$2"
  parts=(engineering-vault sdlc-init project-wiki gitconfig STAGED)
  for name in "${parts[@]}"; do
    if [ -e "$root/$name.old" ]; then
      echo "blocked: recover $root/$name.old before staging again" >&2
      exit 1
    fi
  done
  restore_live_parts() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" != 0 ]; then
      for name in "${parts[@]}"; do
        if [ -e "$root/$name.old" ]; then
          if [ -e "$root/$name" ]; then
            mv "$root/$name" "$next/$name" || continue
          fi
          mv "$root/$name.old" "$root/$name" || echo "restore failed: $root/$name.old" >&2
        fi
      done
    fi
    exit "$status"
  }
  trap restore_live_parts EXIT
  trap "exit 1" HUP INT TERM
  for name in "${parts[@]}"; do
    if [ -e "$root/$name" ]; then mv "$root/$name" "$root/$name.old"; fi
    mv "$next/$name" "$root/$name"
  done
  trap - EXIT HUP INT TERM
  for name in "${parts[@]}"; do
    rm -rf "$root/$name.old"
  done
  rmdir "$next"
' _ "$ROOT" "$NEXT"; then
  echo "blocked: the swap into $ROOT failed. Existing parts were restored where possible. Check any .old paths before retrying."
  return 1 2>/dev/null || exit 1
fi
echo "staged: $ROOT from skills-registry $(git -C "$REGISTRY" rev-parse --short "$STAGED_SHA") ($STAGE_REF)"

SPACE="07 - Engineering/01 - Personal"
if ! grep -Eq '^[[:space:]]*(export[[:space:]]+)?ENGINEERING_VAULTS=' "$SYSTEM_ENV"; then
  VAULT="$(sed -n 's/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}BLACK_VAULT=//p' "$SYSTEM_ENV" | tail -1)"
  printf '\n# The engineering space whose task queue engineering-apply.timer applies.\nENGINEERING_VAULTS=%s\n' \
    "${VAULT:-/srv/sync/blackvault}/$SPACE" >>"$SYSTEM_ENV"
  echo "added: ENGINEERING_VAULTS to $SYSTEM_ENV"
else
  # A value naming the vault root predates the spaces, and engineering.py
  # refuses it, so it moves to the personal space.
  CURRENT="$(sed -n 's/^[[:space:]]*\(export[[:space:]]*\)\{0,1\}ENGINEERING_VAULTS=//p' "$SYSTEM_ENV" | tail -1)"
  if [ -d "$CURRENT/07 - Engineering" ] && [ ! -f "$CURRENT/00 - Engineering.md" ]; then
    sed -i "s|^\([[:space:]]*\(export[[:space:]]*\)\{0,1\}ENGINEERING_VAULTS=\).*|\1$CURRENT/$SPACE|" "$SYSTEM_ENV"
    echo "moved: ENGINEERING_VAULTS in $SYSTEM_ENV to $CURRENT/$SPACE"
  fi
fi

if ! systemd-analyze verify "$UNIT_SOURCE_DIR/engineering-apply.service" "$UNIT_SOURCE_DIR/engineering-apply.timer"; then
  echo 'blocked: the engineering-apply units did not pass systemd verification'
  return 1 2>/dev/null || exit 1
fi
FAILED=0
INSTALLED=0
for UNIT in engineering-apply.service engineering-apply.timer; do
  SOURCE="$UNIT_SOURCE_DIR/$UNIT"
  TARGET="/etc/systemd/system/$UNIT"
  if [ -f "$TARGET" ] && cmp -s "$SOURCE" "$TARGET"; then
    echo "current: $TARGET"
  elif sudo -n install -o root -g root -m 0644 "$SOURCE" "$TARGET"; then
    echo "installed: $TARGET"
    INSTALLED=1
  else
    echo "staged only: sudo install -m 0644 $SOURCE $TARGET"
    FAILED=1
  fi
done
if [ "$INSTALLED" = 1 ]; then
  sudo -n systemctl daemon-reload || FAILED=1
fi
if [ "$FAILED" = 1 ]; then
  echo 'then: sudo systemctl daemon-reload'
  return 1 2>/dev/null || exit 1
fi
if ! systemctl is-enabled --quiet engineering-apply.timer 2>/dev/null; then
  echo 'activation remains explicit: sudo systemctl enable --now engineering-apply.timer'
fi
