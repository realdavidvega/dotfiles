#!/usr/bin/env bash
#
# Claude Code `gcpAuthRefresh` command for Vertex sessions.
#
# Claude runs this when its Google credentials fail, for example when a Google
# Workspace reauth policy expires the refresh token (`invalid_rapt`). It then
# retries the request that failed. Every open session hits the same expiry at
# once, so this script makes them share one browser login:
#
#   1. Credentials already valid: exit 0 at once.
#   2. The first caller takes a lock and starts one `gcloud auth login
#      --update-adc` in its own session, detached, so Claude's 3 minute kill
#      of this script does not take the browser login down with it.
#   3. Every caller, the first included, waits for that login and exits 0
#      when the credentials work again.
#
# A caller that runs out of time before the login lands records its tmux
# pane. When the login completes, each recorded pane gets `continue` typed
# into it, so sessions that gave up resume together. Sessions outside tmux
# recover on their next prompt.
#
# Running `gcloud auth login --update-adc` by hand also counts: the detached
# login notices the credentials work and stands down.

set -uo pipefail

STATE_DIR="${GCP_REAUTH_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/gcp-reauth}"
LOCK="$STATE_DIR/login.lock"
STALLED="$STATE_DIR/stalled-panes"
LOG="$STATE_DIR/login.log"
# Claude kills the command after 180 s. Give up a little before that.
WAIT_SECS="${GCP_REAUTH_WAIT_SECS:-170}"
# A login nobody finishes is abandoned after this long, so a closed browser
# tab cannot hold the lock forever.
LOGIN_MAX_SECS="${GCP_REAUTH_LOGIN_MAX_SECS:-1800}"

if ! command -v gcloud >/dev/null 2>&1 && [ -x "$HOME/google-cloud-sdk/bin/gcloud" ]; then
  PATH="$HOME/google-cloud-sdk/bin:$PATH"
fi

adc_valid() {
  gcloud auth application-default print-access-token >/dev/null 2>&1
}

lock_pid() {
  cat "$LOCK/pid" 2>/dev/null
}

# A lock is stale when its login process is gone. A lock without a pid yet
# belongs to a caller between mkdir and the pid write, unless it is old.
lock_stale() {
  local pid
  pid="$(lock_pid)"
  if [ -n "$pid" ]; then
    ! kill -0 "$pid" 2>/dev/null
  else
    [ -n "$(find "$LOCK" -maxdepth 0 -mmin +1 2>/dev/null)" ]
  fi
}

nudge_stalled() {
  [ -f "$STALLED" ] || return 0
  local pane
  sort -u "$STALLED" | while IFS= read -r pane; do
    [ -n "$pane" ] || continue
    tmux send-keys -t "$pane" -l -- "continue" 2>/dev/null &&
      tmux send-keys -t "$pane" Enter 2>/dev/null
  done
  rm -f "$STALLED"
}

# The detached login. Runs as its own session leader and owns the lock.
run_login() {
  local account="${GCP_REAUTH_ACCOUNT:-$(gcloud config get-value account 2>/dev/null)}"
  local args=(auth login --update-adc --quiet)
  [ -n "$account" ] && args=(auth login "$account" --update-adc --quiet)

  printf '%s pid %s: gcloud %s\n' "$(date '+%F %T')" "$$" "${args[*]}"
  echo "$$" >"$LOCK/pid"

  gcloud "${args[@]}" </dev/null &
  local login=$! waited=0 ok=1

  while kill -0 "$login" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
    if [ $((waited % 15)) -eq 0 ] && adc_valid; then
      echo "credentials valid, login finished elsewhere"
      kill "$login" 2>/dev/null
      break
    fi
    if [ "$waited" -ge "$LOGIN_MAX_SECS" ]; then
      echo "no login after ${LOGIN_MAX_SECS}s, abandoning"
      kill "$login" 2>/dev/null
      break
    fi
  done
  wait "$login" 2>/dev/null

  adc_valid && ok=0
  rm -rf "$LOCK"
  if [ "$ok" -eq 0 ]; then
    echo "credentials valid"
    nudge_stalled
  else
    echo "credentials still invalid"
  fi
  return "$ok"
}

start_login() {
  # perl's setsid puts the login in a new session that Claude's process-tree
  # kill skips, and the fork reparents it away from this script.
  perl -MPOSIX -e 'fork and exit; POSIX::setsid(); exec @ARGV or die' -- \
    "$0" --run-login >>"$LOG" 2>&1 </dev/null
}

wait_for_login() {
  local waited=0
  while [ "$waited" -lt "$WAIT_SECS" ]; do
    sleep 1
    waited=$((waited + 1))
    if [ ! -d "$LOCK" ] || [ $((waited % 10)) -eq 0 ]; then
      adc_valid && return 0
      [ -d "$LOCK" ] || return 1
    fi
  done
  adc_valid && return 0
  [ -n "${TMUX_PANE:-}" ] && echo "$TMUX_PANE" >>"$STALLED"
  echo "Google login still pending, finish it in the browser. This session resumes on its own." >&2
  return 1
}

main() {
  mkdir -p "$STATE_DIR"

  if [ "${1:-}" = "--run-login" ]; then
    run_login
    return
  fi

  adc_valid && return 0

  if [ -d "$LOCK" ] && lock_stale; then
    rm -rf "$LOCK"
  fi
  if mkdir "$LOCK" 2>/dev/null; then
    start_login
  fi

  wait_for_login
}

main "$@"
