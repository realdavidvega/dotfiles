#!/usr/bin/env bash

set -u

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Black System setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
PRODUCT_NAME="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

if [ "$OS_ID" != "linuxmint" ]; then
  echo "Skipping Black System setup: requires Linux Mint."
  return 0 2>/dev/null || exit 0
fi

# Which machines carry the transport is a declaration, not a hardware match, so
# a second hub can exist. MacBookPro12,1 is accepted without a marker so the
# original hub keeps provisioning itself unchanged.
ROLE_FILE="${BLACK_SYSTEM_ROLE_FILE:-/etc/black-system-role}"
ROLE="${BLACK_SYSTEM_ROLE:-$([ -r "$ROLE_FILE" ] && tr -d '[:space:]' <"$ROLE_FILE")}"
if [ "$ROLE" != "hub" ] && [ "$PRODUCT_NAME" != "MacBookPro12,1" ]; then
  echo "Skipping Black System setup: this host is not declared a hub."
  echo "Declare it with: echo hub | sudo tee $ROLE_FILE"
  return 0 2>/dev/null || exit 0
fi

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
BLACK_SYSTEM_ROOT="/srv/services/system"

find_checkout() {
  local name="$1" candidate
  for candidate in \
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


SKILLS_REGISTRY_REPO="${SKILLS_REGISTRY_REPO:-$(find_checkout skills-registry || true)}"
BLACK_SYSTEM_REPO="${BLACK_SYSTEM_REPO:-$(find_checkout black-system || true)}"
if [ ! -x "$BLACK_SYSTEM_REPO/deploy/install.sh" ]; then
  echo 'blocked: clone the private realdavidvega/black-system repo beside dotfiles first'
  return 1 2>/dev/null || exit 1
fi
if ! bash "$SKILLS_REGISTRY_REPO/skills/engineering/repo-sneakernet/scripts/host-context.sh" --require-push; then
  echo 'blocked: reconnect to the Mint tailnet before deploying Black System'
  return 1 2>/dev/null || exit 1
fi
if ! bash "$BLACK_SYSTEM_REPO/deploy/install.sh" \
  --root "$BLACK_SYSTEM_ROOT" \
  --user "$(id -un)" --python /usr/bin/python3; then
  return 1 2>/dev/null || exit 1
fi
UNIT_SOURCE="$BLACK_SYSTEM_ROOT/deploy/black-system.service"
UNIT="/etc/systemd/system/black-system.service"
if ! systemd-analyze verify "$UNIT_SOURCE"; then
  echo 'blocked: generated unit did not pass systemd verification'
  return 1 2>/dev/null || exit 1
fi
if [ -f "$UNIT" ] && cmp -s "$UNIT_SOURCE" "$UNIT"; then
  echo "current: $UNIT"
elif sudo -n install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT"; then
  sudo -n systemctl daemon-reload || { return 1 2>/dev/null || exit 1; }
  echo "installed: $UNIT (activation remains explicit)"
else
  echo "staged only: sudo install -m 0644 $UNIT_SOURCE $UNIT"
  echo 'then: sudo systemctl daemon-reload && sudo systemctl restart black-system'
  return 1 2>/dev/null || exit 1
fi
echo 'Black System deployed from its private repository. See its deployment runbook.'
