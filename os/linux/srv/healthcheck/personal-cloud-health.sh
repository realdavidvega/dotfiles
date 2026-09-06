#!/usr/bin/env bash
# Personal cloud health check. Runs from a system timer on the hub.
#
# The failure this exists for: livesync-bridge restarted, produced no output,
# and "docker ps" still reported Up. Sync silently stopped for 8 minutes and
# nothing noticed. A passive liveness check cannot tell "idle" from "dead",
# so the bridge check compares log activity against container start time.
#
# No "set -o pipefail": grep -q closes pipes early and SIGPIPEs upstream.
set -u

FAIL=0
warn() { echo "FAIL: $*" >&2; FAIL=1; }
ok()   { echo "ok:   $*"; }

# --- system services ---
for unit in syncthing@black.service docker.service; do
  if systemctl is-active --quiet "$unit"; then ok "$unit active"
  else warn "$unit is not active"; fi
done

# --- containers running ---
for c in couchdb livesync-bridge; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = "true" ]; then
    ok "$c running"
  else
    warn "$c not running"
  fi
done

# --- CouchDB actually answers ---
if curl -sS -m 10 -o /dev/null http://127.0.0.1:5984/_up 2>/dev/null; then
  ok "couchdb responding"
else
  warn "couchdb not responding on 127.0.0.1:5984"
fi

# --- tailscale serve still fronting it, with a valid certificate ---
# Derived at runtime, never hardcoded: this file lives in a public repo.
FQDN=$(tailscale status --json 2>/dev/null | python3 -c "
import json,sys
print((json.load(sys.stdin).get('Self',{}).get('DNSName') or '').rstrip('.'))" 2>/dev/null)
if [ -z "$FQDN" ]; then
  warn "cannot resolve this node's MagicDNS name from tailscale"
  CODE="skip"
else
  CODE=$(curl -sS -m 15 -o /dev/null -w '%{http_code}:%{ssl_verify_result}' \
          "https://$FQDN/" 2>/dev/null || echo "000:x")
fi
case "$CODE" in
  skip)  : ;;
  401:0) ok "tailscale serve + certificate valid (401, verify 0)" ;;
  000:*) warn "tailscale serve unreachable or certificate invalid ($CODE)" ;;
  *)     ok  "tailscale serve reachable ($CODE)" ;;
esac

# --- Syncthing peers connected ---
STCONF=/srv/services/syncthing/config.xml
if [ -r "$STCONF" ]; then
  KEY=$(python3 -c "
import xml.etree.ElementTree as ET
print(ET.parse('$STCONF').getroot().find('gui').find('apikey').text)" 2>/dev/null)
  CONN=$(curl -sS -m 10 -H "X-API-Key: $KEY" \
    http://127.0.0.1:8384/rest/system/connections 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin).get('connections',{})
print(sum(1 for c in d.values() if c.get('connected')))" 2>/dev/null || echo 0)
  if [ "${CONN:-0}" -ge 1 ]; then ok "syncthing: $CONN peer(s) connected"
  else warn "syncthing has no connected peers"; fi
fi

# --- the bridge: restarted but silent is the real failure ---
STARTED=$(docker inspect -f '{{.State.StartedAt}}' livesync-bridge 2>/dev/null)
if [ -n "$STARTED" ]; then
  START_EPOCH=$(date -d "$STARTED" +%s 2>/dev/null || echo 0)
  UP=$(( $(date +%s) - START_EPOCH ))
  LINES=$(docker logs --since "$STARTED" livesync-bridge 2>&1 | wc -l)
  if [ "$UP" -gt 300 ] && [ "$LINES" -eq 0 ]; then
    warn "livesync-bridge up ${UP}s with zero log output since start: restarting"
    docker restart livesync-bridge >/dev/null 2>&1 \
      && echo "      restarted" || echo "      RESTART FAILED" >&2
  else
    ok "livesync-bridge alive (${UP}s up, ${LINES} log lines)"
  fi
fi

# --- disk headroom ---
AVAIL=$(df --output=avail -BG /srv 2>/dev/null | tail -1 | tr -dc '0-9')
if [ "${AVAIL:-0}" -lt 10 ]; then warn "/srv has only ${AVAIL}G free"
else ok "/srv has ${AVAIL}G free"; fi

exit "$FAIL"
