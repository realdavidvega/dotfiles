#!/usr/bin/env bash
#
# Mirror a tmux copy into the desktop clipboard.
#
# tmux hands copies to the outer terminal as OSC 52, which reaches the phone
# and the Mac, but GNOME Terminal (VTE) drops it. Claude Code would call xclip
# itself, except it skips that whenever its environment says SSH, and a pane
# keeps the SSH_CONNECTION and the missing DISPLAY of whichever attach created
# it. tmux.conf therefore runs this after every buffer write, so a copy lands
# in the desk's clipboard whatever the pane's environment says.
#
# On macOS a copy-mode selection goes to pbcopy as well, so it lands even when
# iTerm blocks OSC 52. The buffer hooks pass --buffer-hook and are ignored
# there, because a hook cannot tell which buffer fired it, and paneport's
# named load-buffer would otherwise put stale text on the clipboard.
#
# Text arrives on stdin. Anywhere without a clipboard to write it is
# discarded, because OSC 52 already covers those clients. It always exits 0,
# since tmux reports a failing hook in the status line and a missing clipboard
# is not worth an error.

session_env() {
  tmux show-environment "$1" 2>/dev/null | sed -n "s/^$1=//p"
}

if [ "$(uname -s)" = Linux ]; then
  wayland=${WAYLAND_DISPLAY:-$(session_env WAYLAND_DISPLAY)}
  display=${DISPLAY:-$(session_env DISPLAY)}
  # A pane created over SSH has neither, but the desk's X server is still there.
  [ -z "$wayland" ] && [ -z "$display" ] && [ -S /tmp/.X11-unix/X0 ] && display=:0

  # Both tools fork a process that keeps owning the selection. Its stdout and
  # stderr must not stay attached to tmux, or the job never finishes.
  if [ -n "$wayland" ] && command -v wl-copy >/dev/null; then
    WAYLAND_DISPLAY=$wayland wl-copy >/dev/null 2>&1
    exit 0
  elif [ -n "$display" ] && command -v xclip >/dev/null; then
    DISPLAY=$display xclip -selection clipboard >/dev/null 2>&1
    exit 0
  fi
elif [ "$(uname -s)" = Darwin ] && [ "${1:-}" != --buffer-hook ] && command -v pbcopy >/dev/null; then
  # An empty selection must not clear the clipboard. pbcopy reads Mac Roman
  # unless LC_CTYPE says UTF-8, and LC_ALL would override LC_CTYPE.
  tmp=$(mktemp "${TMPDIR:-/tmp}/tmux-clipboard.XXXXXX") || { cat >/dev/null; exit 0; }
  cat >"$tmp"
  [ -s "$tmp" ] && env -u LC_ALL LC_CTYPE=UTF-8 pbcopy <"$tmp" >/dev/null 2>&1
  rm -f "$tmp"
  exit 0
fi

cat >/dev/null
exit 0
