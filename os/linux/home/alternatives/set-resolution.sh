#!/bin/bash
sleep 3
xrandr --newmode "3440x1440R" 319.75 3440 3488 3520 3600 1440 1443 1453 1481 +hsync -vsync
xrandr --addmode DP-2 "3440x1440R"
xrandr --output DP-2 --mode "3440x1440R" --primary
xrandr --output eDP-1 --off
