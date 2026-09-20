#!/usr/bin/env bash
#
# Keep this Mac's Black System transport in the right state while it acts as a
# standby for the Mint hub.
#
# The daemon rereads BLACK_SYSTEM_ACTIVE_HOST before every poll, so rewriting
# that one line is enough to stand it down. It is not enough to bring it back:
# a standby exits 0, and launchd's KeepAlive only restarts unsuccessful exits,
# so a stood-down daemon stays stopped until something starts it. This script
# is that something.
#
# Demotion is immediate, promotion is delayed. Preferring the hub on the first
# sign of life keeps the dangerous state (two pollers on one token) short,
# while the delay stops a flapping link from handing the bot back and forth.
#
# Temporary. It is replaced by the lease in black-system, which lets every host
# reach the same conclusion from claim files instead of from reachability.

set -uo pipefail

ROOT="${BLACK_SYSTEM_ROOT:-$HOME/.local/share/black-system}"
ENV_FILE="$ROOT/system.env"
STATE="$ROOT/watchdog-state"
LOG="$ROOT/watchdog.log"
LABEL="com.realdavidvega.black-system"

HUB_HOST="${BLACK_SYSTEM_HUB_HOSTNAME:-black-MacBookPro}"   # what the hub's gethostname() returns
SELF="$(python3 -c 'import socket; print(socket.gethostname())')"
DOWN_CHECKS_BEFORE_PROMOTE="${BLACK_SYSTEM_PROMOTE_AFTER:-5}"

HOST_CTX="${BLACK_SYSTEM_HOST_CTX:-$HOME/Workspace/repos/github/tools/skills-registry/skills/engineering/repo-sneakernet/scripts/host-context.sh}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$LOG"; }

[ -f "$ENV_FILE" ] || { log "no env file at $ENV_FILE, nothing to do"; exit 0; }

# A promotion without a token would only produce an error loop, and the daemon
# would exit unsuccessfully, which launchd *does* restart. Refuse instead.
if ! grep -q '^BLACK_SYSTEM_BOT_TOKEN=.\+' "$ENV_FILE"; then
  log "token not configured, staying dormant"
  exit 0
fi

current_owner() { sed -n 's/^BLACK_SYSTEM_ACTIVE_HOST=//p' "$ENV_FILE" | tail -1; }

set_owner() {
  local want="$1" tmp
  [ "$(current_owner)" = "$want" ] && return 1
  tmp="$(mktemp "$ROOT/.system.env.XXXXXX")" || return 1
  # Rewrite in place rather than append, so the file keeps one declaration.
  sed "s/^BLACK_SYSTEM_ACTIVE_HOST=.*/BLACK_SYSTEM_ACTIVE_HOST=$want/" "$ENV_FILE" >"$tmp" || { rm -f "$tmp"; return 1; }
  chmod 600 "$tmp" && mv -f "$tmp" "$ENV_FILE" || { rm -f "$tmp"; return 1; }
  return 0
}

hub_online() {
  [ -x "$HOST_CTX" ] || [ -f "$HOST_CTX" ] || return 2
  bash "$HOST_CTX" --probe-hub 2>/dev/null \
    | python3 -c 'import json,sys
try: print("yes" if json.load(sys.stdin).get("hub_online") else "no")
except Exception: print("unknown")' 2>/dev/null
}

status="$(hub_online)"

case "$status" in
  yes)
    printf '0\n' >"$STATE"
    if set_owner "$HUB_HOST"; then
      log "hub online, stood down in favour of $HUB_HOST"
    fi
    ;;
  no)
    downs="$(cat "$STATE" 2>/dev/null || echo 0)"
    downs=$((downs + 1))
    printf '%s\n' "$downs" >"$STATE"
    if [ "$downs" -lt "$DOWN_CHECKS_BEFORE_PROMOTE" ]; then
      log "hub offline ($downs/$DOWN_CHECKS_BEFORE_PROMOTE before promoting)"
      exit 0
    fi
    if set_owner "$SELF"; then
      log "hub offline for $downs checks, promoted $SELF"
    fi
    # Start it whether or not the declaration changed: a previous promotion
    # may have been followed by a clean exit for some other reason.
    if launchctl kickstart "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      log "kickstarted $LABEL"
    else
      log "could not kickstart $LABEL, is the agent loaded?"
    fi
    ;;
  *)
    # Cannot tell. Change nothing, which leaves whatever the last decision was.
    log "hub state unknown, leaving owner as $(current_owner)"
    ;;
esac
