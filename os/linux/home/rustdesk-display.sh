#!/usr/bin/env bash

set -euo pipefail

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

PROFILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/rustdesk-display"
PROFILE_FILE="$PROFILE_DIR/profile"

usage() {
  cat <<'EOF'
Usage: rustdesk-display <profile|apply|status>

Profiles:
  macbook        1920x1248, readable on a 3024x1964 Retina client
  macbook-hires  2560x1662, more remote desktop space at the same aspect ratio
  ultrawide      3440x1440 at 50 Hz

Commands:
  apply          Reapply the last selected profile (defaults to macbook)
  status         Show the saved profile and active XRandR output
EOF
}

profile="${1:-apply}"

if [ "$profile" = "status" ]; then
  if [ -r "$PROFILE_FILE" ]; then
    printf 'Saved profile: %s\n' "$(<"$PROFILE_FILE")"
  else
    printf 'Saved profile: macbook (default)\n'
  fi
  xrandr --current | sed -n '1,4p'
  exit 0
fi

save_profile=1
if [ "$profile" = "apply" ]; then
  save_profile=0
  if [ -r "$PROFILE_FILE" ]; then
    profile="$(<"$PROFILE_FILE")"
  else
    profile="macbook"
  fi
fi

case "$profile" in
  macbook)
    mode="1920x1248R"
    framebuffer="1920x1248"
    ;;
  macbook-hires)
    mode="2560x1662R"
    framebuffer="2560x1662"
    ;;
  ultrawide)
    mode="3440x1440R50"
    framebuffer="3440x1440"
    ;;
  -h | --help | help)
    usage
    exit 0
    ;;
  *)
    printf 'Unknown RustDesk display profile: %s\n\n' "$profile" >&2
    usage >&2
    exit 2
    ;;
esac

if ! xrandr --query | grep -q '^DUMMY0 connected'; then
  echo "DUMMY0 is not active. Install the managed Xorg dummy configuration and restart the display manager." >&2
  exit 1
fi

xrandr --output DUMMY0 --mode "$mode" --primary --fb "$framebuffer"

if [ "$save_profile" -eq 1 ]; then
  mkdir -p "$PROFILE_DIR"
  printf '%s\n' "$profile" > "$PROFILE_FILE"
fi

printf 'RustDesk display profile: %s (%s)\n' "$profile" "$framebuffer"
