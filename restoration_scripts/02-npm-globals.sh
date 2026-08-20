#!/usr/bin/env bash

set -euo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"
DOTLY_PATH="${DOTLY_PATH:-$DOTFILES_PATH/modules/dotly}"

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Mint npm globals: not Linux."
  return 0 2>/dev/null || exit 0
fi

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping Mint npm globals: WSL detected."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
if [ "$OS_ID" != "linuxmint" ]; then
  echo "Skipping Mint npm globals: Linux Mint not detected."
  return 0 2>/dev/null || exit 0
fi

export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  echo "NVM is unavailable. Run 02-nvm-setup.sh first."
  return 1 2>/dev/null || exit 1
fi

# shellcheck source=/dev/null
source "$NVM_DIR/nvm.sh"
nvm use default

# Reuse dotly's package-manager importer without running its all-manager command,
# which would also feed the WSL apt dump to native Linux Mint.
# shellcheck source=/dev/null
source "$DOTLY_PATH/scripts/package/src/dump.sh"
package::npm_import

echo "npm globals restored through dotly."
