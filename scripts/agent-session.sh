#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: agent-session.sh <command> [args]

Persistent tmux sessions for coding agents, local or over SSH.

Commands:
  open <name> [dir]   Create, or attach to, a session named <name>. When
                      somebody is already attached it joins as a second client
                      automatically, so this is the only command usually needed
  ls                  List sessions
  mobile <name>       Force the second-client attach, even if nobody is on
  kill <name>         Kill session <name>
  remote [args...]    Run this same command on the hub over SSH
  unlock              Unlock the hub's encrypted home (hub only)
  help                Show this help

Options for open:
  --agent <a>         claude (default), codex, opencode, or shell
  --dir <path>        Working directory, overriding the resolved default
  --resume            Pick up the agent's most recent conversation in that
                      directory instead of starting a blank one

Environment:
  AGENT_SESSION_ROOT  Default working directory
  AGENT_SESSION_HOST  SSH host for `remote` (default: mint)

A session gets three windows: agent, shell, git. The agent is typed into a
shell rather than run as the window's command, so that when it exits or
crashes the window survives holding its output.

`remote` is the only distinction that depends on where you are: it runs the
command on the hub instead of here. Everything else reads the session's state.
EOF
}

# A non-login SSH command (`ssh host 'cmd'`) does not source the shell rc, so
# ~/.local/bin is missing and claude and codex are not found. Both agents
# install there.
if [[ -d "$HOME/.local/bin" ]]; then
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) PATH="$HOME/.local/bin:$PATH" ;;
  esac
  export PATH
fi

HUB_CONFIG="/srv/services/agents/tmux.conf"
HUB_SCRIPT="/srv/services/agents/bin/agent-session"

# On the hub, ~/.tmux.conf lives in an encrypted home that is unreadable until
# someone logs in. Fall back to the plaintext copy under /srv so a session
# started before that unlock still has this configuration.
resolve_config() {
  if [[ -r "$HOME/.tmux.conf" ]]; then
    printf '%s\n' "$HOME/.tmux.conf"
  elif [[ -r "$HUB_CONFIG" ]]; then
    printf '%s\n' "$HUB_CONFIG"
  fi
}

# `tmux -f` is honoured only by the command that actually starts the server,
# and `tmux start-server` will not do it: a server with no sessions exits
# immediately. So the config rides on new-session, which is what starts the
# server here.
TMUX_CONF="$(resolve_config)"

tmux_with_conf() {
  if [[ -n "$TMUX_CONF" ]]; then
    tmux -f "$TMUX_CONF" "$@"
  else
    tmux "$@"
  fi
}

# When a server is already up, -f is ignored and the new session would inherit
# the default configuration. Apply it explicitly, and before any window exists,
# because base-index is read at window-creation time.
ensure_config() {
  [[ -n "$TMUX_CONF" ]] || return 0

  if tmux list-sessions >/dev/null 2>&1; then
    tmux source-file "$TMUX_CONF" 2>/dev/null || true
  fi
}

resolve_root() {
  local candidate

  for candidate in \
    "${AGENT_SESSION_ROOT:-}" \
    "${BLACK_VAULT_REPO:-}" \
    "${BLACK_VAULT:-}" \
    /srv/sync/blackvault; do
    if [[ -n "$candidate" ]] && [[ -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  pwd
}

# The binary, for the on-PATH check. Kept apart from the command line below,
# which carries arguments.
agent_binary() {
  case "$1" in
    claude|codex|opencode) printf '%s\n' "$1" ;;
    shell)                 printf '\n' ;;
    *)
      printf 'Unknown agent: %s (expected claude, codex, opencode or shell)\n' "$1" >&2
      exit 1
      ;;
  esac
}

# What gets typed into the pane. With --resume each agent picks up its most
# recent conversation in this directory rather than starting a blank one, which
# is what you want after a kill or a reboot.
agent_command() {
  local agent="$1" resume="$2"

  if [[ "$resume" != true ]]; then
    agent_binary "$agent"
    return
  fi

  case "$agent" in
    claude)   printf 'claude --continue\n' ;;
    codex)    printf 'codex resume --last\n' ;;
    opencode) printf 'opencode --continue\n' ;;
    shell)    printf '\n' ;;
    *)        agent_binary "$agent" ;;
  esac
}

# Inside tmux, attaching is an error. Switching is what was meant.
enter_session() {
  local name="$1"

  if [[ -n "${TMUX:-}" ]]; then
    tmux switch-client -t "$name"
  else
    tmux attach-session -t "$name"
  fi
}

cmd_open() {
  local name="" dir="" agent="claude" agent_cmd agent_bin resume=false

  while (($# > 0)); do
    case "$1" in
      --agent)  agent="${2:?--agent needs a value}"; shift 2 ;;
      --dir)    dir="${2:?--dir needs a value}"; shift 2 ;;
      --resume) resume=true; shift ;;
      -h|--help) usage; exit 0 ;;
      *)
        if [[ -z "$name" ]]; then
          name="$1"
        elif [[ -z "$dir" ]]; then
          dir="$1"
        else
          printf 'Unexpected argument: %s\n' "$1" >&2
          exit 1
        fi
        shift
        ;;
    esac
  done

  if [[ -z "$name" ]]; then
    printf 'A session name is required.\n\n' >&2
    usage >&2
    exit 1
  fi

  # tmux treats . and : as address separators in a session name.
  name="${name//[.:]/-}"

  if tmux has-session -t "=$name" 2>/dev/null; then
    # Somebody is already watching, and we are not inside tmux ourselves, so
    # join as a second client rather than resizing the session under them.
    if [[ -z "${TMUX:-}" ]] && [[ "$(attached_clients "$name")" -gt 0 ]]; then
      attach_grouped "$name"
    else
      enter_session "$name"
    fi
    return 0
  fi

  ensure_config

  [[ -n "$dir" ]] || dir="$(resolve_root)"

  if [[ ! -d "$dir" ]]; then
    printf 'Working directory does not exist: %s\n' "$dir" >&2
    exit 1
  fi

  agent_bin="$(agent_binary "$agent")"
  agent_cmd="$(agent_command "$agent" "$resume")"

  if [[ -n "$agent_bin" ]] && ! command -v "$agent_bin" >/dev/null 2>&1; then
    printf 'Agent %s is not on PATH. Is the home unlocked? See: agent-session.sh unlock\n' \
      "$agent_bin" >&2
    exit 1
  fi

  tmux_with_conf new-session -d -s "$name" -c "$dir" -n agent
  tmux new-window -t "$name" -c "$dir" -n shell
  tmux new-window -t "$name" -c "$dir" -n git
  tmux select-window -t "$name:agent"

  if [[ -n "$agent_cmd" ]]; then
    tmux send-keys -t "$name:agent" "$agent_cmd" Enter
  fi

  enter_session "$name"
}

# Attach as an additional client, in a session of its own that shares the
# windows. Used whenever somebody is already attached, so a second client never
# reshapes the first one's view.
attach_grouped() {
  local name="$1" client="${1}-m"

  # A grouped session shares the windows but keeps its own size and its own
  # current window, so a second client does not reshape the first one's view.
  #
  # Cleanup is a detach hook rather than `destroy-unattached on`, which reaps
  # the session the moment it is created, before there is a client to attach.
  if ! tmux has-session -t "=$client" 2>/dev/null; then
    tmux new-session -d -t "$name" -s "$client"
    tmux set-hook -t "$client" client-detached "kill-session -t '$client'"
  fi

  enter_session "$client"
}

cmd_mobile() {
  local name="${1:?A session name is required}"

  name="${name//[.:]/-}"

  if ! tmux has-session -t "=$name" 2>/dev/null; then
    printf 'No session named %s. Create it with: agent-session.sh open %s\n' "$name" "$name" >&2
    exit 1
  fi

  attach_grouped "$name"
}

# How many clients are watching a session right now.
attached_clients() {
  tmux list-clients -t "=$1" 2>/dev/null | grep -c . || true
}

cmd_remote() {
  local host="${AGENT_SESSION_HOST:-mint}" remote_args quoted arg

  quoted=""
  for arg in "$@"; do
    quoted+=" $(printf '%q' "$arg")"
  done
  [[ -n "$quoted" ]] || quoted=" ls"

  # The hub copy under /srv works whether or not the encrypted home is mounted.
  remote_args="if [ -x $HUB_SCRIPT ]; then $HUB_SCRIPT${quoted};"
  remote_args+=" else \$HOME/.dotfiles/scripts/agent-session.sh${quoted}; fi"

  exec ssh -t "$host" "$remote_args"
}

cmd_unlock() {
  if ! command -v ecryptfs-mount-private >/dev/null 2>&1; then
    printf 'ecryptfs-mount-private not found. This command is for the Mint hub.\n' >&2
    exit 1
  fi

  if mount | grep -q "on $HOME type ecryptfs"; then
    printf 'Home is already unlocked.\n'
    return 0
  fi

  printf 'Enter the login passphrase to unlock %s:\n' "$HOME"
  ecryptfs-mount-private
}

if ! command -v tmux >/dev/null 2>&1; then
  printf 'tmux is not installed or not on PATH.\n' >&2
  exit 1
fi

# Bare `agent-session` shows usage. Doing something silently on no arguments is
# a bad default for a tool that creates and kills sessions.
command="${1:-help}"
[[ $# -gt 0 ]] && shift

case "$command" in
  open)        cmd_open "$@" ;;
  ls|list)     tmux list-sessions 2>/dev/null || printf 'No sessions.\n' ;;
  mobile)      cmd_mobile "$@" ;;
  kill)        tmux kill-session -t "=${1:?A session name is required}" ;;
  remote)      cmd_remote "$@" ;;
  unlock)      cmd_unlock ;;
  help|-h|--help) usage ;;
  *)
    printf 'Unknown command: %s\n\n' "$command" >&2
    usage >&2
    exit 1
    ;;
esac
