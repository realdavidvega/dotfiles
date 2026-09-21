#!/usr/bin/env bash

set -u

# Obsidian ships a CLI, but only as a native binary the app installs itself.
# Under WSL the app is a Windows process, so there is nothing for it to install
# on the Linux side. Link the wrapper into $DOTFILES_PATH/bin, which is already
# ahead of brew on PATH, so `obsidian` resolves by name the way it does on macOS.

if [ "$(uname -s)" != "Linux" ]; then
  echo "Skipping Obsidian CLI setup: not Linux."
  return 0 2>/dev/null || exit 0
fi

if ! grep -qiE '(microsoft|wsl)' /proc/version 2>/dev/null; then
  echo "Skipping Obsidian CLI setup: not WSL."
  return 0 2>/dev/null || exit 0
fi

WRAPPER="$DOTFILES_PATH/scripts/obsidian-wsl.sh"
TARGET="$DOTFILES_PATH/bin/obsidian"

if [ ! -f "$WRAPPER" ]; then
  echo "missing wrapper: $WRAPPER"
  return 1 2>/dev/null || exit 1
fi

chmod +x "$WRAPPER"
mkdir -p "$DOTFILES_PATH/bin"

if [ "$(readlink -f "$TARGET" 2>/dev/null)" = "$(readlink -f "$WRAPPER")" ]; then
  echo "current: $TARGET"
else
  ln -sfn "$WRAPPER" "$TARGET"
  echo "linked: $TARGET -> $WRAPPER"
fi

# The wrapper only finds the app if Obsidian is installed on the Windows side.
if "$TARGET" files total >/dev/null 2>&1; then
  echo "obsidian CLI responds"
else
  echo "obsidian CLI did not respond. Is Obsidian running, and is the CLI enabled"
  echo "in Settings, General, Advanced?"
fi
