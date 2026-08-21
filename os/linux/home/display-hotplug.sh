#!/bin/bash

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"

# Give the kernel time to settle after the hotplug event
sleep "${DISPLAY_HOTPLUG_DELAY:-2}"

lid_is_closed() {
    local state_file

    for state_file in /proc/acpi/button/lid/*/state; do
        [ -r "$state_file" ] || continue
        grep -qi 'closed' "$state_file" && return 0
    done

    return 1
}

lid_state() {
    if lid_is_closed; then
        printf '%s\n' closed
    else
        printf '%s\n' open
    fi
}

watch_lid() {
    local current_state
    local previous_state
    local runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

    exec 9>"$runtime_dir/dotfiles-display-lid.lock"
    flock -n 9 || return 0

    previous_state="$(lid_state)"
    while sleep 1; do
        current_state="$(lid_state)"
        if [ "$current_state" != "$previous_state" ]; then
            DISPLAY_HOTPLUG_DELAY=0 "$0"
            previous_state="$current_state"
        fi
    done
}

if [ "${1:-}" = "--watch-lid" ]; then
    watch_lid
    exit 0
fi

# Check if DP-2 is connected
if xrandr --query | grep -q '^DP-2 connected'; then
    # Re-add the custom mode when the external monitor returns
    xrandr --newmode "3440x1440R" 319.75 3440 3488 3520 3600 1440 1443 1453 1481 +hsync -vsync 2>/dev/null || true
    xrandr --addmode DP-2 "3440x1440R" 2>/dev/null || true

    if lid_is_closed; then
        xrandr --output DP-2 --mode "3440x1440R" --primary \
            --output eDP-1 --off
    else
        xrandr --output DP-2 --mode "3440x1440R" --primary \
            --output eDP-1 --auto --left-of DP-2
    fi
else
    # Use the internal display while the external monitor is disconnected
    xrandr --output eDP-1 --auto --primary
fi

exit 0
