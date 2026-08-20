#!/usr/bin/env bash

set -euo pipefail

DOTFILES_PATH="${DOTFILES_PATH:-$HOME/.dotfiles}"

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Linux Mint packages: not Linux."
  return 0 2>/dev/null || exit 0
fi

if grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping Linux Mint packages: WSL detected."
  return 0 2>/dev/null || exit 0
fi

OS_ID="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
if [ "$OS_ID" != "linuxmint" ]; then
  echo "Skipping Linux Mint packages: Linux Mint not detected."
  return 0 2>/dev/null || exit 0
fi

PACKAGE_MANIFEST="$DOTFILES_PATH/os/linux/apt/packages.mint.txt"
PACKAGES=()

while IFS= read -r package || [ -n "$package" ]; do
  package="${package%%#*}"
  package="$(printf '%s' "$package" | xargs)"
  [ -n "$package" ] && PACKAGES+=("$package")
done < "$PACKAGE_MANIFEST"

if [ "${#PACKAGES[@]}" -eq 0 ]; then
  echo "No Linux Mint packages configured."
  return 0 2>/dev/null || exit 0
fi

echo "Installing ${#PACKAGES[@]} Linux Mint packages from $PACKAGE_MANIFEST"
sudo apt-get update
sudo apt-get install -y "${PACKAGES[@]}"

mkdir -p "$HOME/.local/bin"
if ! command -v bat >/dev/null 2>&1 && command -v batcat >/dev/null 2>&1; then
  [ -e "$HOME/.local/bin/bat" ] || ln -s "$(command -v batcat)" "$HOME/.local/bin/bat"
fi
if ! command -v fd >/dev/null 2>&1 && command -v fdfind >/dev/null 2>&1; then
  [ -e "$HOME/.local/bin/fd" ] || ln -s "$(command -v fdfind)" "$HOME/.local/bin/fd"
fi

echo "Linux Mint package installation complete."
