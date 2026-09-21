#!/usr/bin/env bash
# Obsidian CLI shim for WSL.
#
# Obsidian runs as a Windows application, so WSL has no native binary to install
# at /usr/local/bin/obsidian the way macOS does. This wraps the Windows
# executable and keeps the command name the vault's rules assume.
#
# The app prints a blank line and two startup notices to stdout before every
# result, an asar load line and an installer-age warning. They would corrupt any
# caller parsing the output, so they are filtered here rather than at each call
# site. Windows line endings are stripped for the same reason. Only leading
# blank lines are dropped, since a blank line inside a result is content.
set -uo pipefail

for _exe in \
  "/mnt/c/Users/$USER/AppData/Local/Obsidian/Obsidian.exe" \
  "/mnt/c/Users/david/AppData/Local/Obsidian/Obsidian.exe" \
  "/mnt/c/Program Files/Obsidian/Obsidian.exe"
do
  [ -x "$_exe" ] && OBSIDIAN_EXE="$_exe" && break
done

if [ -z "${OBSIDIAN_EXE:-}" ]; then
  echo "obsidian: Obsidian.exe not found. Is Obsidian installed on Windows?" >&2
  exit 127
fi

"$OBSIDIAN_EXE" "$@" 2>&1 \
  | tr -d '\r' \
  | grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} Loading updated app package ' \
  | grep -vF 'Your Obsidian installer is out of date.' \
  | awk 'NF || seen { seen = 1; print }'

exit "${PIPESTATUS[0]}"
