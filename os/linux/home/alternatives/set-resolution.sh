#!/bin/bash
sleep 3
DISPLAY_HOTPLUG_DELAY=0 "$HOME/.local/bin/display-hotplug.sh" >/dev/null 2>&1 || true
