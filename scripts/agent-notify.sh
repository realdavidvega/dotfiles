#!/usr/bin/env bash
#
# Notify the phone about a detached agent session.
#
# Claude Code: `Stop` hook (a turn ended) and `Notification` hook (blocked on a
# prompt, or idle). Payload arrives on stdin.
# Codex: the `notify` program. Payload arrives as argv[1], and there is exactly
# one event, agent-turn-complete, so Codex produces no "needs you" ping.
#
# It stays silent while a tmux client is attached, so working at the desk
# produces nothing. The ping is for a session you detached from.
#
# Agent output is included only for sessions rooted in an allowlisted
# directory. Everything else reports metadata alone: repo, branch, changed-file
# count. The list fails closed, so an unrecognised path withholds the body
# rather than sending it.

set -uo pipefail

usage() {
  cat <<'EOF'
Usage: agent-notify.sh [--event stop|notification] [--label TEXT]
                       [--force] [--dry-run] [JSON]

  --event KIND  Override the event kind (default: read from the payload)
  --label TEXT  Override the subject line
  --force       Send even while a client is attached
  --dry-run     Print the message instead of sending it

Environment:
  AGENT_NOTIFY_BODY_ROOTS  Colon-separated roots whose sessions may include
                           agent output. Anything else is metadata only.
EOF
}

# Never fail an agent turn because a notification could not be built or sent.
trap 'exit 0' ERR

event=""
label=""
force=false
dry_run=false
payload=""

while (($# > 0)); do
  case "$1" in
    --event)   event="${2:-}"; shift 2 ;;
    --label)   label="${2:-}"; shift 2 ;;
    --force)   force=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *)         payload="$1"; shift ;;   # Codex passes its JSON as argv[1]
  esac
done

# Claude Code delivers the payload on stdin. Read it only when stdin is not a
# terminal, so an interactive run does not hang waiting for input.
if [[ -z "$payload" ]] && [[ ! -t 0 ]]; then
  payload="$(timeout 2 cat || true)"
fi

# ── Session, and whether anyone is looking ───────────────────────────────────
session=""
if [[ -n "${TMUX:-}" ]] && command -v tmux >/dev/null 2>&1; then
  session="$(tmux display-message -p -t "${TMUX_PANE:-}" '#{session_name}' 2>/dev/null || true)"
fi

# Send only for a tmux session with nobody attached. Outside tmux there is no
# attach state to read, and the agent is either being watched in a terminal or
# running as a background job. Neither wants a ping, so silence is the default
# and --force is the override.
if [[ "$force" != true ]]; then
  [[ -n "$session" ]] || exit 0

  attached="$(tmux list-clients -t "=$session" 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${attached:-0}" -gt 0 ]]; then
    exit 0
  fi
fi

# ── Roots whose content may leave the machine ────────────────────────────────
# Work repositories are deliberately absent. Client data travels as counts,
# never as subjects or bodies.
default_roots="$HOME/Workspace/repos/github:$HOME/Workspace/repos/external"
default_roots+=":$HOME/workspace/repos/github:$HOME/workspace/repos/external"
default_roots+=":/srv/sync/blackvault:${DOTFILES_PATH:-$HOME/.dotfiles}"
[[ -n "${BLACK_VAULT_REPO:-}" ]] && default_roots+=":$BLACK_VAULT_REPO"
[[ -n "${BLACK_VAULT:-}" ]] && default_roots+=":$BLACK_VAULT"

body_roots="${AGENT_NOTIFY_BODY_ROOTS:-$default_roots}"

# ── Build the message ────────────────────────────────────────────────────────
message="$(
  AN_PAYLOAD="$payload" AN_EVENT="$event" AN_LABEL="$label" \
  AN_SESSION="$session" AN_ROOTS="$body_roots" \
  python3 - <<'PY' 2>/dev/null
import json, os, subprocess

payload_raw = os.environ.get("AN_PAYLOAD", "")
event = os.environ.get("AN_EVENT", "")
label = os.environ.get("AN_LABEL", "")
session = os.environ.get("AN_SESSION", "")
roots = [r for r in os.environ.get("AN_ROOTS", "").split(":") if r]

try:
    payload = json.loads(payload_raw) if payload_raw.strip() else {}
except Exception:
    payload = {}

if not event:
    hook = str(payload.get("hook_event_name", "")).lower()
    kind = str(payload.get("type", "")).lower()
    event = "notification" if hook == "notification" else "stop"

cwd = payload.get("cwd") or os.getcwd()


def run(*args):
    try:
        out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
status = run("git", "status", "--porcelain")
changed = len([ln for ln in status.splitlines() if ln.strip()]) if status else 0

repo_root = run("git", "rev-parse", "--show-toplevel") or cwd
subject = label or session or os.path.basename(repo_root.rstrip("/")) or "agent"


# Fail closed: a path under no known root withholds the body.
def allowed(path):
    real = os.path.realpath(path)
    for root in roots:
        root = os.path.realpath(os.path.expanduser(root))
        if real == root or real.startswith(root + os.sep):
            return True
    return False


may_send_body = allowed(cwd)

title = ""
body = ""

if event == "notification":
    body = str(payload.get("message", "")).strip()
else:
    # Codex hands the final message over directly.
    body = str(payload.get("last-assistant-message", "")).strip()

    transcript = payload.get("transcript_path")
    if transcript and os.path.exists(transcript):
        rows = []
        try:
            with open(transcript, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            rows = []

        for row in reversed(rows):
            if row.get("type") == "ai-title" and row.get("aiTitle"):
                title = str(row["aiTitle"]).strip()
                break

        if not body:
            for row in reversed(rows):
                if row.get("type") != "assistant":
                    continue
                for block in reversed(row.get("message", {}).get("content", []) or []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        body = block["text"].strip()
                        break
                if body:
                    break

lines = []
if event == "notification":
    lines.append("\U0001F514 " + subject + " needs you")
else:
    lines.append("✅ " + subject + " finished")

if title and may_send_body:
    lines.append(title)

meta = []
if branch:
    meta.append(branch)
meta.append(str(changed) + " changed" if changed else "clean")
lines.append(" · ".join(meta))

if body and may_send_body:
    body = " ".join(body.split())
    if len(body) > 400:
        body = body[:400].rstrip() + "..."
    lines.append("“" + body + "”")
elif not may_send_body:
    lines.append("(body withheld: not an allowlisted root)")

print("\n".join(lines))
PY
)"

if [[ -z "$message" ]]; then
  message="${label:-${session:-agent}} finished a turn on $(hostname -s)"
fi

if [[ "$dry_run" == true ]]; then
  printf '%s\n' "$message"
  exit 0
fi

# Prefer the bridge when it is configured: it posts into the session's own
# Telegram topic with buttons, so the reply goes straight back to this pane.
# Without it, fall back to a plain message, which is all a machine running no
# bridge can offer.
bridge=""
for candidate in \
  /srv/services/agents/bin/agent-bridge.py \
  "${DOTFILES_PATH:-$HOME/.dotfiles}/scripts/agent-bridge.py"; do
  if [[ -r "$candidate" ]]; then
    bridge="$candidate"
    break
  fi
done

bridge_env="${AGENT_BRIDGE_ENV:-/srv/services/agents/bridge.env}"

if [[ -n "$bridge" ]] && [[ -r "$bridge_env" ]] && command -v python3 >/dev/null 2>&1; then
  case "$payload" in
    *'"hook_event_name":"Notification"'*|*'"hook_event_name": "Notification"'*)
      resolved_event="notification" ;;
    *) resolved_event="stop" ;;
  esac
  [[ -n "$event" ]] && resolved_event="$event"

  if python3 "$bridge" notify \
      --session "${session:-agent}" \
      --event "$resolved_event" \
      --text "$message" >/dev/null 2>&1; then
    exit 0
  fi
  printf 'agent-notify: bridge send failed, falling back to a plain message.\n' >&2
fi

# secrets.sh is sourced by interactive shells only, and a hook does not get one.
if [[ -z "${TELEGRAM_BOT_ID:-}" ]] || [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  # shellcheck disable=SC1091
  source "${DOTFILES_PATH:-$HOME/.dotfiles}/secrets/secrets.sh" 2>/dev/null || true
fi

if [[ -z "${TELEGRAM_BOT_ID:-}" ]] || [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
  printf 'agent-notify: no Telegram credentials available, staying quiet.\n' >&2
  exit 0
fi

curl -fsS --max-time 10 \
  -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_ID}/sendMessage" \
  -d "chat_id=${TELEGRAM_CHAT_ID}" \
  -d "disable_web_page_preview=true" \
  --data-urlencode "text=${message}" \
  >/dev/null 2>&1 || true

exit 0
