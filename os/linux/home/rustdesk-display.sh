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
  ultrawide      3440x1440
  native         The panel's own resolution, unscaled

The profile geometry becomes the X screen, which is what RustDesk captures. The
panel keeps its own timing and the driver scales between the two, so a profile
that is not 16:10 looks stretched locally and correct to the remote client.

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
# native is how the lid-open path resets the panel, not a choice the user made.
# Saving it would quietly replace the profile they picked for the next lid close.
if [ "$profile" = "native" ]; then
  save_profile=0
fi
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
    framebuffer="1920x1248"
    ;;
  macbook-hires)
    framebuffer="2560x1662"
    ;;
  iphone)
    framebuffer="1600x736"
    ;;
  ultrawide)
    framebuffer="3440x1440"
    ;;
  native)
    framebuffer=""
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

# The remote head is the built-in panel. It is the output still available with the
# lid shut, which is the only time a remote profile is the thing being looked at.
target_output() {
  if xrandr --query | grep -q '^eDP-1 connected'; then
    printf '%s\n' eDP-1
    return 0
  fi
  xrandr --query | awk '/ connected/ { print $1; exit }'
}

# The mode marked + by the driver is the panel's own timing. Scaling sits on top of
# it, so this has to stay the real mode rather than the profile's geometry.
preferred_mode() {
  xrandr --query | awk -v out="$1" '
    $1 == out { found = 1; next }
    found && /^[^ \t]/ { exit }
    found && /\+/ { print $1; exit }'
}

output="$(target_output)"
if [ -z "$output" ]; then
  echo "No connected output to drive." >&2
  exit 1
fi

native_mode="$(preferred_mode "$output")"
if [ -z "$native_mode" ]; then
  echo "Unable to read the preferred mode for $output." >&2
  exit 1
fi

# RRSetScreenSize refuses any screen size that no longer contains every active
# output. With a transform in play that refusal fires even at an exact fit, so
# shrinking the screen while the output is live cannot be made reliable: bare --fb
# is silently ignored, and the combined call comes back BadMatch. Detaching the CRTC
# first lets the screen resize freely, and the mode and transform go back in the same
# call that sets the final size. Measured good across every profile pair.
#
# RandR also applies asynchronously, so this verifies the result rather than assuming
# the request landed, and retries.
screen_size() {
  xrandr --query | awk '/^Screen/ { print $8 "x" substr($10, 1, length($10) - 1); exit }'
}

set_geometry() {
  local target="$1"
  local attempt

  for attempt in 1 2 3; do
    xrandr --output "$output" --off 2>/dev/null

    if [ "$target" = "$native_mode" ]; then
      xrandr --fb "$target" --output "$output" --mode "$native_mode" --primary \
        --scale 1x1 2>/dev/null
    else
      xrandr --fb "$target" --output "$output" --mode "$native_mode" --primary \
        --scale-from "$target" 2>/dev/null
    fi
    sleep 0.4

    if [ "$(screen_size)" = "$target" ]; then
      return 0
    fi
  done

  # A failed geometry change must never leave the panel detached and dark.
  xrandr --output "$output" --auto --primary 2>/dev/null
  return 1
}

if [ -z "$framebuffer" ]; then
  framebuffer="$native_mode"
fi

if ! set_geometry "$framebuffer"; then
  echo "Unable to set $output to $framebuffer." >&2
  exit 1
fi

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
