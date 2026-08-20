#!/usr/bin/env bash

set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Mint NVM setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping Mint NVM setup: WSL detected."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
if [ "$OS_ID" != "linuxmint" ]; then
  echo "Skipping Mint NVM setup: Linux Mint not detected."
  return 0 2>/dev/null || exit 0
fi

NVM_VERSION="v0.40.4"
export NVM_DIR="$HOME/.nvm"

if [ ! -e "$NVM_DIR" ]; then
  git clone --branch "$NVM_VERSION" --depth 1 https://github.com/nvm-sh/nvm.git "$NVM_DIR"
elif [ ! -d "$NVM_DIR/.git" ] || [ ! -f "$NVM_DIR/nvm.sh" ]; then
  echo "Blocked: $NVM_DIR exists but is not an NVM checkout."
  return 1 2>/dev/null || exit 1
fi

# shellcheck source=/dev/null
source "$NVM_DIR/nvm.sh"

nvm install --lts
nvm alias default 'lts/*'
nvm use default

echo "NVM and Node LTS are ready."
