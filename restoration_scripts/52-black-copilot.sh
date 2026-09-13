#!/usr/bin/env bash

set -u

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Black Copilot setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
PRODUCT_NAME="$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)"

if [ "$OS_ID" != "linuxmint" ] || [ "$PRODUCT_NAME" != "MacBookPro12,1" ]; then
  echo "Skipping Black Copilot setup: requires Linux Mint on MacBookPro12,1."
  return 0 2>/dev/null || exit 0
fi

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
COPILOT_ROOT="/srv/services/copilot"
SYSTEM_ROOT="$DOTFILES_PATH/os/linux/system"
FAILED=0

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

# Plaintext copies under /srv, not links into the encrypted home, so the
# service works at boot before anyone logs in. Same reasoning as the bridge.
echo "Staging Black Copilot under $COPILOT_ROOT..."
mkdir -p "$COPILOT_ROOT/bin" "$COPILOT_ROOT/journal-copilot/scripts" || FAILED=1

install -m 0755 "$DOTFILES_PATH/scripts/black-copilot.py" "$COPILOT_ROOT/bin/black-copilot.py" \
  && echo "staged: $COPILOT_ROOT/bin/black-copilot.py" || FAILED=1

SKILLS_REGISTRY_REPO="${SKILLS_REGISTRY_REPO:-$(find_checkout skills-registry || true)}"
SKILL_SCRIPTS="$SKILLS_REGISTRY_REPO/skills/obsidian/journal-copilot/scripts"
if [ -d "$SKILL_SCRIPTS" ]; then
  for script in "$SKILL_SCRIPTS"/*.py; do
    case "$(basename "$script")" in test_*) continue ;; esac
    install -m 0644 "$script" "$COPILOT_ROOT/journal-copilot/scripts/" || FAILED=1
  done
  echo "staged: journal-copilot scripts from $SKILL_SCRIPTS"
else
  echo "blocked: no journal-copilot scripts at $SKILL_SCRIPTS"
  FAILED=1
fi

ENV_FILE="$COPILOT_ROOT/copilot.env"
if [ -f "$ENV_FILE" ]; then
  echo "current: $ENV_FILE (left alone, it holds a token)"
else
  install -m 0600 "$DOTFILES_PATH/os/linux/srv/copilot/copilot.env.sample" "$ENV_FILE" \
    && echo "seeded: $ENV_FILE (fill it in, then enable black-copilot.service)" || FAILED=1
fi

# Installed, not enabled. Enabling gives a chat write access to the vault.
UNIT_SOURCE="$SYSTEM_ROOT/etc/systemd/system/black-copilot.service"
UNIT="/etc/systemd/system/black-copilot.service"
if [ -f "$UNIT" ] && cmp -s "$UNIT_SOURCE" "$UNIT"; then
  echo "current: $UNIT"
elif sudo install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT"; then
  sudo systemctl daemon-reload || FAILED=1
  echo "staged: $UNIT"
  echo "        enable it once copilot.env is filled in:"
  echo "        sudo systemctl enable --now black-copilot"
else
  echo "blocked: could not install $UNIT (needs sudo on a terminal)"
  FAILED=1
fi

if [ "$FAILED" -ne 0 ]; then
  echo "Black Copilot setup finished with problems. Review the output above."
  return 1 2>/dev/null || exit 1
fi
echo "Black Copilot setup complete."
