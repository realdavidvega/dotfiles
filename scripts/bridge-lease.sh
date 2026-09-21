#!/usr/bin/env bash
# Run the LiveSync bridge only while this host holds the bridge lease.
# Everything it touches lives outside the encrypted home, so it works on an
# unattended boot when /home/black is still locked.
export BLACK_SYSTEM_BIN=/srv/services/system/bin/black-system.py
export BLACK_SYSTEM_PYTHON=/usr/bin/python3
export BLACK_SYSTEM_ENV=/srv/services/system/system.env
exec /srv/services/bin/leased-service.sh bridge \
  --start  "cd /srv/services/livesync-bridge && docker compose up -d --force-recreate" \
  --stop   "cd /srv/services/livesync-bridge && docker compose stop" \
  --status "/srv/services/bin/bridge-status.sh"
