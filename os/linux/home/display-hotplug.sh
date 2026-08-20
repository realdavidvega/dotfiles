#!/bin/bash
export DISPLAY=:0
export XAUTHORITY=/home/black/.Xauthority
export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/1000/bus"

# Give the kernel time to settle after the hotplug event
sleep 2

# Check if DP-2 is connected
if xrandr | grep "DP-2 connected" > /dev/null 2>&1; then
    # Re-add the custom mode when the external monitor returns
    xrandr --newmode "3440x1440R" 319.75 3440 3488 3520 3600 1440 1443 1453 1481 +hsync -vsync 2>/dev/null
    xrandr --addmode DP-2 "3440x1440R"
    xrandr --output DP-2 --mode "3440x1440R" --primary
    xrandr --output eDP-1 --off
else
    # Use the internal display while the external monitor is disconnected
    xrandr --output eDP-1 --auto
fi
