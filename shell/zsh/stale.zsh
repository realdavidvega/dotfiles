# Find and clear folders nobody has used lately, for freeing disk space.
#
# A folder is stale when no file inside it was read or written within the last
# N days (default 14). Only files count, because a size scan such as du updates
# the access time of every directory it reads.
#
# The unit is a whole folder, never single files. Caches such as uv, pixi and
# Playwright keep each package as a folder of files, and a package with some of
# its files removed still looks installed to the tool that owns it.
#
#   stale-ls ~/Library/Caches [days]      preview stale subfolders with sizes
#   stale-trash ~/Library/Caches [days]   move them to the Trash (recoverable)
#   stale-rm ~/Library/Caches [days]      delete them outright
#
# Moving to the Trash frees no space until the Trash is emptied.

# NUL-separated paths of the stale subfolders of $1
stale() {
  local root="${1:?usage: stale <dir> [days]}" days="${2:-14}" d
  [[ -d "$root" ]] || { echo "stale: not a directory: $root" >&2; return 1; }
  for d in "$root"/*(N/) "$root"/.*(N/); do
    [[ -z "$(find "$d" -type f \( -atime -"$days" -o -mtime -"$days" \) -print -quit 2>/dev/null)" ]] \
      && printf '%s\0' "$d"
  done
  return 0
}

stale-ls() {
  setopt localoptions pipefail
  stale "$@" | xargs -0 -r du -sh 2>/dev/null
}

# macOS 15 and later ship /usr/bin/trash. Linux uses gio or trash-cli.
_stale_trash_cmd() {
  if [[ -x /usr/bin/trash ]]; then echo /usr/bin/trash
  elif (( $+commands[gio] )); then echo "gio trash"
  elif (( $+commands[trash-put] )); then echo trash-put
  else echo "stale-trash: no trash command found (install trash-cli)" >&2; return 1
  fi
}

stale-trash() {
  setopt localoptions pipefail
  local cmd
  cmd="$(_stale_trash_cmd)" || return 1
  stale "$@" | xargs -0 -r ${=cmd}
}

stale-rm() {
  setopt localoptions pipefail
  stale "$@" | xargs -0 -r rm -rf
}
