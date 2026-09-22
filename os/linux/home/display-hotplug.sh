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

# Going dark behind a shut lid is DPMS, not brightness, and certainly not switching
# the output off. All three look the same from the outside and are very different
# underneath. Measured on this machine at the iphone profile:
#
#   xrandr --output eDP-1 --off   screen collapses to 320x200, capture 259 KB
#   brightness 0                  screen 1600x736, capture 4.71 MB
#   xset dpms force off           screen 1600x736, capture 4.71 MB
#
# So the output has to stay enabled or a remote client has nothing to capture.
# Between the other two, a brightness of 0 is saved by systemd-backlight@.service at
# shutdown and restored at boot, which brings the machine up with an invisible login
# screen. DPMS keeps no state across a reboot and any input undoes it, so it cannot
# strand the panel dark. It also held for 30s under Cinnamon without being overridden.
# Seconds of X input idleness before the panel blanks while the lid is shut.
PANEL_BLANK_SECONDS="${PANEL_BLANK_SECONDS:-60}"

# This machine runs with no DPMS timeouts of its own, xset reports 0 0 0, so a bare
# force off is one shot: the next input wakes the panel and nothing ever blanks it
# again, which is how the panel ends up lit behind a shut lid hours later. Arming an
# off timeout makes the blank re-arm itself after every wake.
panel_arm_blank() {
    xset +dpms 2>/dev/null || return 1
    xset dpms 0 0 "$PANEL_BLANK_SECONDS" 2>/dev/null || true
}

# Cinnamon's power stack can also switch DPMS off outright instead of moving the
# timeout, and DPMSDisable leaves the timeouts untouched: xset keeps reporting
# Off: 60 while the panel is lit and /sys/class/drm/*/dpms reads On. An off-seconds
# check alone therefore reads 60, concludes nothing drifted and never re-arms, which
# is how a closed lid ends up glowing until somebody opens it. Read the enable flag
# alongside the timeout so both kinds of drift are caught.
panel_blank_state_now() {
    xset -q 2>/dev/null | awk '
        /Standby:/ { off = $6 }
        /DPMS is/ { enabled = $3 }
        END { print off, enabled }
    '
}

# X keeps its own idea of the DPMS level, and a modeset underneath it can relight the
# panel without that bookkeeping moving. xset then reports Monitor is Off while the
# connector reads On and the backlight sits at its normal level, measured here at
# 1101 of 1388 with bl_power 0 behind a shut lid. X issues no further blank in that
# state, because as far as it is concerned the panel is already dark, so the desync
# has to be broken from outside. The connector is the authority, not xset.
panel_hardware_is_lit() {
    local node

    for node in /sys/class/drm/*-eDP-1/dpms; do
        [ -r "$node" ] || continue
        grep -qx 'On' "$node" && return 0
    done

    return 1
}

panel_x_thinks_blanked() {
    xset -q 2>/dev/null | grep -q 'Monitor is Off'
}

panel_off() {
    panel_arm_blank || return 0
    xset dpms force off 2>/dev/null || true
}

panel_on() {
    # An open lid must never blank on a timer of ours, so put the timeouts back.
    xset dpms 0 0 0 2>/dev/null || true
    xset dpms force on 2>/dev/null || true
}

# Only lid transitions act. There used to be a second branch here that reran the
# whole path once a second for as long as the lid was shut and eDP-1 was still on,
# to undo something re-enabling the panel behind a closed lid. Keeping eDP-1 enabled
# with the lid shut is now the intended state, so that branch matched forever and
# reapplied the profile every second, fighting anything else touching the screen.
watch_lid() {
    local current_state
    local previous_state
    local ticks
    local desyncs
    local runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

    exec 9>"$runtime_dir/dotfiles-display-lid.lock"
    flock -n 9 || return 0

    previous_state="$(lid_state)"
    ticks=0
    desyncs=0
    while sleep 1; do
        current_state="$(lid_state)"
        if [ "$current_state" != "$previous_state" ]; then
            DISPLAY_HOTPLUG_DELAY=0 "$0"
            previous_state="$current_state"
            ticks=0
            desyncs=0
            continue
        fi

        # Cinnamon's screensaver and power stack write DPMS too, either putting the
        # timeout back to zero or switching DPMS off entirely, and both silently turn
        # the lid-closed blank back into a one shot. Re-assert the timeout and the
        # enable flag, never the blank itself, so this can never darken a panel
        # somebody is looking at.
        ticks=$((ticks + 1))
        if [ "$ticks" -ge 30 ]; then
            ticks=0
            if [ "$current_state" = closed ]; then
                if [ "$(panel_blank_state_now)" != "$PANEL_BLANK_SECONDS Enabled" ]; then
                    panel_arm_blank || true
                fi

                # Forcing the blank is safe only where X already believes the
                # panel is dark. That is the desync, and it means nobody can be
                # looking at what this turns off. A remote viewer is unaffected
                # either way, since DPMS leaves the framebuffer intact.
                #
                # X lags its own bookkeeping for about nine seconds after every
                # wake, reporting Monitor is Off while the panel is already lit,
                # and that transient clears on its own. Blanking inside it would
                # darken a panel somebody just woke, so only a desync still there
                # on the next check counts as the stuck state worth breaking.
                if panel_x_thinks_blanked && panel_hardware_is_lit; then
                    desyncs=$((desyncs + 1))
                    if [ "$desyncs" -ge 2 ]; then
                        desyncs=0
                        xset dpms force off 2>/dev/null || true
                    fi
                else
                    desyncs=0
                fi
            fi
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
    if lid_is_closed; then
        # Switching the only output off leaves the X screen with no framebuffer, so
        # a remote client sees nothing to capture. That emptiness is exactly what the
        # dummy head used to paper over. Keep the panel configured at the saved remote
        # geometry and blank the panel with DPMS, so nothing is lit inside a closed
        # clamshell and RustDesk still has something real to read.
        "$HOME/.local/bin/rustdesk-display" apply >/dev/null 2>&1 ||
            xrandr --output eDP-1 --auto --primary
        panel_off
    else
        panel_on
        "$HOME/.local/bin/rustdesk-display" native >/dev/null 2>&1 ||
            xrandr --output eDP-1 --auto --primary
    fi
fi

exit 0
