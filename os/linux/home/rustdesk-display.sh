#!/usr/bin/env bash

set -euo pipefail

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

PROFILE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/rustdesk-display"
PROFILE_FILE="$PROFILE_DIR/profile"

usage() {
  cat <<'EOF'
Usage: rustdesk-display <profile|apply|restore|status>

Profiles:
  macbook        1920x1248, readable on a 3024x1964 Retina client
  macbook-hires  2560x1662, more remote desktop space at the same aspect ratio
  iphone         1600x736, phone-friendly iPhone 17 Pro landscape ratio
  ultrawide      3440x1440 at 50 Hz

Commands:
  apply          Reapply the last selected profile (defaults to macbook)
  restore        Select macbook and restore the pre-phone interface settings
  status         Show the saved profile and active XRandR output
EOF
}

restore_interface() {
  local key

  [ -e "$PROFILE_DIR/phone-active" ] || return 0

  if command -v gsettings >/dev/null 2>&1; then
    for key in text-scaling-factor cursor-size; do
      if [ -r "$PROFILE_DIR/$key" ]; then
        gsettings set org.cinnamon.desktop.interface "$key" "$(<"$PROFILE_DIR/$key")"
      fi
    done
  fi

  rm -f "$PROFILE_DIR/phone-active" \
    "$PROFILE_DIR/text-scaling-factor" \
    "$PROFILE_DIR/cursor-size"
}

apply_phone_interface() {
  command -v gsettings >/dev/null 2>&1 || return 0
  gsettings writable org.cinnamon.desktop.interface text-scaling-factor | grep -qx true || return 0

  mkdir -p "$PROFILE_DIR"
  if [ ! -e "$PROFILE_DIR/phone-active" ]; then
    gsettings get org.cinnamon.desktop.interface text-scaling-factor > \
      "$PROFILE_DIR/text-scaling-factor"
    gsettings get org.cinnamon.desktop.interface cursor-size > \
      "$PROFILE_DIR/cursor-size"
    : > "$PROFILE_DIR/phone-active"
  fi

  gsettings set org.cinnamon.desktop.interface text-scaling-factor 1.25
  gsettings set org.cinnamon.desktop.interface cursor-size 32
}

profile="${1:-apply}"

if [ "$profile" = "restore" ]; then
  profile="macbook"
fi

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
  iphone)
    mode="1600x736R"
    framebuffer="1600x736"
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

if [ "$profile" = "iphone" ] && ! xrandr --query | grep -q '1600x736R'; then
  xrandr --newmode "1600x736R" 79.75 1600 1648 1680 1760 736 739 749 757 +hsync -vsync
  xrandr --addmode DUMMY0 "1600x736R"
fi

xrandr --output DUMMY0 --mode "$mode" --primary --fb "$framebuffer"

if [ "$profile" = "iphone" ]; then
  apply_phone_interface
else
  restore_interface
fi

if [ "$save_profile" -eq 1 ]; then
  mkdir -p "$PROFILE_DIR"
  printf '%s\n' "$profile" > "$PROFILE_FILE"
fi

printf 'RustDesk display profile: %s (%s)\n' "$profile" "$framebuffer"
