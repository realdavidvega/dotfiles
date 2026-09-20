#!/usr/bin/env bash
#
# Run a service only while this host holds its lease.
#
#   leased-service.sh <role> --start <cmd> --stop <cmd> [--status <cmd>]
#
# The lease is decided by black-system from claim files in the vault, so every
# host reaches the same answer without contacting the others. This script only
# turns that answer into a running or stopped service.
#
# Intended for the singletons that are not the Telegram transport: the LiveSync
# bridge and the Syncthing introducer. The transport supervises itself, because
# it is the same process that does the polling.
#
# Run it on a timer. Each invocation renews, asks, and reconciles once.

set -uo pipefail

ROLE="${1:-}"
[ -n "$ROLE" ] || { echo "usage: leased-service.sh <role> --start <cmd> --stop <cmd>" >&2; exit 2; }
shift

START="" ; STOP="" ; STATUS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --start)  START="$2"; shift 2 ;;
    --stop)   STOP="$2";  shift 2 ;;
    --status) STATUS="$2"; shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done
[ -n "$START" ] && [ -n "$STOP" ] || { echo "--start and --stop are required" >&2; exit 2; }

BLACK_SYSTEM_BIN="${BLACK_SYSTEM_BIN:-$HOME/.local/share/black-system/bin/black-system.py}"
PYTHON="${BLACK_SYSTEM_PYTHON:-/opt/homebrew/bin/python3.12}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

running() {
  [ -n "$STATUS" ] || return 2
  eval "$STATUS" >/dev/null 2>&1
}

# Renew first, then ask. Publishing before electing is what stops the only host
# alive from waiting forever for a claim that only it could have written.
if "$PYTHON" "$BLACK_SYSTEM_BIN" lease "$ROLE" --renew --state active >/dev/null 2>&1; then
  held=1
else
  held=0
fi

if [ "$held" -eq 1 ]; then
  if running; then
    exit 0                      # already ours and already up
  fi
  echo "$ROLE: holding the lease, starting"
  eval "$START"
  exit $?
fi

# Not ours. Stopping an already-stopped service is cheap, and leaving a second
# copy of a singleton running is not, so this errs towards stopping.
if running || [ -z "$STATUS" ]; then
  echo "$ROLE: lease held elsewhere, stopping"
  eval "$STOP"
fi
# Record that we are standing down rather than silently holding a stale claim.
"$PYTHON" "$BLACK_SYSTEM_BIN" lease "$ROLE" --renew --state standby >/dev/null 2>&1
exit 0
