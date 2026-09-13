#!/usr/bin/env bash
#
# Claude Code status line: 5-hour and weekly usage, cached for the bridge.
#
# Claude Code hands rate_limits to the status line and to nothing else, so
# this is the one place a watcher can learn how close the subscription is to
# its limit. agent-bridge.py reads the cache and posts to the Limits topic.
#
# The line stays short, "5h 42% · 7d 18%", and is empty until the first reply
# of a session. The cache is rewritten when the numbers change, or once a
# minute so the bridge can tell fresh data from stale.

export AGENT_LIMITS_CACHE="${AGENT_LIMITS_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/agent-limits/claude.json}"

exec python3 -c '
import json, os, sys, tempfile, time

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

limits = data.get("rate_limits") if isinstance(data, dict) else None
if not isinstance(limits, dict) or not limits:
    sys.exit(0)

parts = []
for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
    used = (limits.get(key) or {}).get("used_percentage")
    if isinstance(used, (int, float)):
        parts.append(f"{label} {used:.0f}%")
print(" · ".join(parts))

path = os.environ["AGENT_LIMITS_CACHE"]
now = time.time()
try:
    with open(path, encoding="utf-8") as handle:
        cached = json.load(handle)
    if cached.get("rate_limits") == limits and now - float(cached.get("observed_at", 0)) < 60:
        sys.exit(0)
except Exception:
    pass

try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, name = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".claude-")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"observed_at": now, "rate_limits": limits}, handle)
    os.replace(name, path)
except Exception:
    pass
'
