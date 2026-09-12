#!/usr/bin/env bash

set -euo pipefail

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Mint uv setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping Mint uv setup: WSL detected."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
if [ "$OS_ID" != "linuxmint" ]; then
  echo "Skipping Mint uv setup: Linux Mint not detected."
  return 0 2>/dev/null || exit 0
fi

UV_VERSION="0.12.5"

if command -v uv >/dev/null 2>&1; then
  echo "uv is already installed: $(uv --version)"
  return 0 2>/dev/null || exit 0
fi

curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" |
  env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh

echo "uv $UV_VERSION installed in $HOME/.local/bin."
