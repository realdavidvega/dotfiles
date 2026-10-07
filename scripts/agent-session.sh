#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: agent-session.sh <command> [args]

Persistent tmux sessions for coding agents, local or over SSH.

Shared commands (the same verbs as Telegram):
  ls                      List sessions and what is running
  open <name> [agent]      Attach, starting the session if it is not running
  close <name>            Stop a session, keeping its saved conversation
  peek <name> [lines]      Read the agent pane, default 30 lines, maximum 60
  say <name> <text...>     Type text literally and press Enter
  esc <name>              Send Escape to the agent pane
  enter <name>            Press Enter in the agent pane
  help                    Show this help

Terminal commands:
  mobile <name>           Force an additional terminal client
  remote [args...]         Run the same command on the hub over SSH
  unlock                  Unlock the hub's encrypted home (hub only)

Agents: claude (default), codex, opencode, shell.

Starting a session continues the agent's last conversation in that directory
when there is one, and starts a fresh one otherwise.

Options for open:
  --fresh                 Start a blank conversation even if one exists
  --resume                Continue the last conversation, never start fresh
  --agent <a>              Select the agent (alternative to the positional agent)
  --dir <path>             Override the working directory
  --detached              Start or reuse without attaching

Where a session starts:
  A session named after a git repository under AGENT_SESSION_REPOS starts in
  it. An exact name wins, otherwise the only repository named <name>-something,
  so skp finds tools/skp and skills finds tools/skills-registry. Any other name
  starts in the default root, the vault.

Explicit forms, kept for scripts and the Telegram buttons:
  new <name> [agent]       open --fresh --detached
  resume <name> [agent]    open --resume --detached
  kill <name>             close

Examples:
  ags open skp
  ags open vault codex --fresh
  ags say vault "continue with the tests"
  ags remote open skp

Telegram: /open skp, /open vault codex fresh, /close skp, /ls.
Inside the vault topic: /peek, /say TEXT, /esc, /enter.
On the hub, starting a session gives it its Telegram topic.

Environment:
  AGENT_SESSION_ROOT   Default working directory
  AGENT_SESSION_REPOS  Where named repositories are looked up, colon separated
                       (default: ~/Workspace/repos:/srv/services)
  AGENT_SESSION_HOST   SSH host for `remote` (default: mint)

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

# A session named after a repository starts in it, so `/open skp` from a phone
# lands in the checkout without typing a path. An exact name wins. Failing
# that, the only repository named NAME-something, so skills finds
# skills-registry. Two candidates is ambiguous and finds nothing.
repo_for_name() {
  local name="$1" base dir exact="" bases
  local -a prefixed=()

  [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
  IFS=: read -ra bases <<< "${AGENT_SESSION_REPOS:-$HOME/Workspace/repos:/srv/services}"

  for base in "${bases[@]}"; do
    [[ -n "$base" && -d "$base" ]] || continue
    while IFS= read -r dir; do
      [[ -e "$dir/.git" ]] || continue
      if [[ "${dir##*/}" == "$name" ]]; then
        exact="$dir"
        break 2
      fi
      prefixed+=("$dir")
    done < <(find "$base" -mindepth 1 -maxdepth 3 -type d \( -name "$name" -o -name "$name-*" \) \
               -not -path '*/.*' 2>/dev/null)
  done

  if [[ -n "$exact" ]]; then
    printf '%s\n' "$exact"
  elif ((${#prefixed[@]} == 1)); then
    printf '%s\n' "${prefixed[0]}"
  else
    return 1
  fi
}

session_dir() {
  repo_for_name "$1" || resolve_root
}

# Whether the agent has a conversation in this directory that continuing would
# pick up. Each agent keys its history by the working directory, so this is
# the same lookup claude --continue and codex resume --last make. opencode and
# the shell have nothing to find, so they start fresh unless told otherwise.
has_conversation() {
  local agent="$1" dir codex_home

  dir="$(cd "$2" && pwd -P)" || return 1
  case "$agent" in
    claude)
      compgen -G "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/${dir//[^A-Za-z0-9]/-}/*.jsonl" >/dev/null
      ;;
    codex)
      # The first line of each rollout is its session_meta, carrying the cwd.
      codex_home="${CODEX_HOME:-$HOME/.codex}"
      [[ -d "$codex_home/sessions" ]] || return 1
      find "$codex_home/sessions" -name 'rollout-*.jsonl' -print0 2>/dev/null \
        | xargs -0 -r head -qn1 2>/dev/null | grep -qF "\"cwd\":\"$dir\""
      ;;
    *) return 1 ;;
  esac
}

# Tell the Telegram bridge about a lifecycle change on this host, so a session
# started at a terminal gets its topic without a trip through /bind. It runs in
# the background and says nothing. No bridge, no configuration or no network
# all mean nothing happens, and the terminal never waits on Telegram. The
# bridge sets AGENT_BRIDGE_QUIET when it calls this launcher, since it
# announces its own sessions.
announce_session() {
  local bridge="${AGENT_BRIDGE_BIN:-/srv/services/agents/bin/agent-bridge.py}"
  local env_file="${AGENT_BRIDGE_ENV:-/srv/services/agents/bridge.env}"

  [[ -z "${AGENT_BRIDGE_QUIET:-}" ]] || return 0
  [[ -r "$bridge" && -r "$env_file" ]] || return 0
  command -v python3 >/dev/null 2>&1 || return 0

  ( AGENT_BRIDGE_ENV="$env_file" python3 "$bridge" topic "$@" >/dev/null 2>&1 & ) || true
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
  local name="" dir="" agent="claude" agent_cmd agent_bin mode=auto resume=false detached=false
  local conversation

  while (($# > 0)); do
    case "$1" in
      --agent)  agent="${2:?--agent needs a value}"; shift 2 ;;
      --dir)    dir="${2:?--dir needs a value}"; shift 2 ;;
      --resume) mode=resume; shift ;;
      --fresh)  mode=fresh; shift ;;
      --detached) detached=true; shift ;;
      -h|--help) usage; exit 0 ;;
      *)
        if [[ -z "$name" ]]; then
          name="$1"
        elif [[ "$1" =~ ^(claude|codex|opencode|shell)$ ]]; then
          agent="$1"
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
    if [[ "$detached" == true ]]; then
      printf '%s is already running. Use ags open %s to attach.\n' "$name" "$name"
      return 0
    fi
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

  [[ -n "$dir" ]] || dir="$(session_dir "$name")"

  if [[ ! -d "$dir" ]]; then
    printf 'Working directory does not exist: %s\n' "$dir" >&2
    exit 1
  fi

  agent_bin="$(agent_binary "$agent")"
  if [[ "$mode" == resume ]] || { [[ "$mode" == auto ]] && has_conversation "$agent" "$dir"; }; then
    resume=true
  fi
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

  if [[ "$resume" == true ]]; then
    conversation="continuing its last conversation"
  else
    conversation="fresh conversation"
  fi
  announce_session "$name" --text "🟢 $name started at a terminal, $agent in $dir, $conversation."

  if [[ "$detached" == true ]]; then
    # The first line is what Telegram shows, so it stands on its own.
    printf 'Started %s with %s in %s, %s.\n' "$name" "$agent" "$dir" "$conversation"
    printf 'Use ags open %s to attach.\n' "$name"
  else
    enter_session "$name"
  fi
}

# Read the agent window even when the user is viewing shell or git.
# The foreground program is observable. An agent's internal task state is not.
cmd_ls() {
  local sessions sid name windows attached detail window running directory
  local program path attachment
  if ! sessions="$(tmux list-sessions -F $'#{session_id}\t#{session_name}\t#{session_windows}\t#{session_attached}' 2>&1)"; then
    case "$sessions" in
      *"no server running"*|*"No such file or directory"*) printf 'No sessions.\n'; return 0 ;;
      *) printf '%s\n' "$sessions" >&2; return 1 ;;
    esac
  fi

  printf '%-16s  %-14s  %-12s  %-7s  %s\n' SESSION RUNNING ATTACHMENT WINDOWS DIRECTORY
  while IFS=$'\t' read -r sid name windows attached; do
    running="" directory=""
    detail="$(tmux list-windows -t "$sid" -F $'#{window_name}\t#{pane_current_command}\t#{pane_current_path}' 2>/dev/null)" || continue
    while IFS=$'\t' read -r window program path; do
      if [[ "$window" == agent ]]; then
        running="$program" directory="$path"
        break
      fi
    done <<< "$detail"
    if [[ -z "$running" ]]; then
      detail="$(tmux display-message -p -t "$sid" $'#{pane_current_command}\t#{pane_current_path}' 2>/dev/null)" || continue
      IFS=$'\t' read -r running directory <<< "$detail"
    fi
    case "$running" in
      bash|zsh|sh|fish|dash|ksh) running="shell ($running)" ;;
    esac
    attachment=detached
    if [[ "$attached" -gt 0 ]]; then
      attachment="$attached attached"
    fi
    printf '%-16s  %-14s  %-12s  %-7s  %s\n' "$name" "${running:-unknown}" "$attachment" "$windows" "$directory"
  done <<< "$sessions"
}

# Shared lifecycle verbs are detached so a command has the same effect from
# a terminal or Telegram. open remains the explicit terminal attachment step.
cmd_start() {
  local verb="$1" name="${2:-}" agent=claude
  local -a options=(--detached)
  if [[ "$name" == -h || "$name" == --help ]]; then usage; return 0; fi
  if [[ -z "$name" || "$name" == -* ]]; then
    printf 'Usage: ags %s <session> [claude|codex|opencode|shell] [--dir PATH]\n' "$verb" >&2
    return 1
  fi
  if [[ "$name" == *[.:[:space:]]* ]]; then
    printf 'Session names cannot contain spaces, dots or colons.\n' >&2
    return 1
  fi
  shift 2
  if [[ $# -gt 0 && "$1" != -* ]]; then agent="$1"; shift; fi
  while (($# > 0)); do
    case "$1" in
      --agent) agent="${2:?--agent needs a value}"; shift 2 ;;
      --dir) options+=(--dir "${2:?--dir needs a value}"); shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) printf 'Unexpected argument: %s\n' "$1" >&2; return 1 ;;
    esac
  done
  agent_binary "$agent" >/dev/null
  if [[ "$verb" == resume ]]; then options+=(--resume); else options+=(--fresh); fi
  cmd_open "$name" --agent "$agent" "${options[@]}"
}

agent_pane() {
  local name="$1" rows window active pane selected=""
  if ! rows="$(tmux list-windows -t "=$name" -F $'#{window_name}\t#{window_active}\t#{pane_id}' 2>/dev/null)"; then
    printf 'No session named %s. Use ags open %s.\n' "$name" "$name" >&2
    return 1
  fi
  while IFS=$'\t' read -r window active pane; do
    if [[ "$window" == agent ]]; then printf '%s\n' "$pane"; return 0; fi
    [[ "$active" != 1 ]] || selected="$pane"
  done <<< "$rows"
  if [[ -z "$selected" ]]; then
    printf 'No pane found for %s.\n' "$name" >&2
    return 1
  fi
  printf '%s\n' "$selected"
}

cmd_pane() {
  local verb="$1" name="${2:-}" pane lines=30 text
  if [[ "$name" == -h || "$name" == --help ]]; then usage; return 0; fi
  if [[ -z "$name" ]]; then printf 'Usage: ags %s <session> [argument]\n' "$verb" >&2; return 1; fi
  shift 2
  case "$verb" in
    peek)
      if [[ $# -gt 1 || "${1:-30}" == *[!0-9]* || -z "${1:-30}" ]]; then
        printf 'Usage: ags peek <session> [1-60]\n' >&2; return 1
      fi
      lines="${1:-30}"
      if [[ ${#lines} -gt 6 ]]; then printf 'Line count is too large.\n' >&2; return 1; fi
      lines=$((10#$lines))
      ((lines > 0)) || lines=1
      ((lines <= 60)) || lines=60
      ;;
    say)
      if [[ $# -eq 0 || -z "$*" ]]; then printf 'Usage: ags say <session> TEXT\n' >&2; return 1; fi
      ;;
    esc|enter)
      if [[ $# -gt 0 ]]; then printf 'Usage: ags %s <session>\n' "$verb" >&2; return 1; fi
      ;;
  esac
  pane="$(agent_pane "$name")" || return 1
  case "$verb" in
    peek)
      text="$(tmux capture-pane -p -t "$pane" -S "-$lines")" || return 1
      # Trim empty rows at the bottom, then retain the newest requested rows.
      printf '%s\n' "$text" | awk -v n="$lines" '{ rows[NR]=$0; if ($0 ~ /[^[:space:]]/) last=NR } END { if (!last) print "(pane is empty)"; else for (i=(last>n ? last-n+1 : 1); i<=last; i++) print rows[i] }'
      ;;
    say)
      tmux send-keys -t "$pane" -l -- "$*"
      tmux send-keys -t "$pane" Enter
      printf 'Sent text to %s.\n' "$name"
      ;;
    esc) tmux send-keys -t "$pane" Escape; printf 'Sent Escape to %s.\n' "$name" ;;
    enter) tmux send-keys -t "$pane" Enter; printf 'Sent Enter to %s.\n' "$name" ;;
  esac
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

# Closing stops the agent and the tmux session. The conversation is a file on
# disk, so the next open picks it up again.
cmd_close() {
  local name="${1:?A session name is required}"

  tmux kill-session -t "=$name"
  announce_session "$name" --no-create \
    --text "closed $name at a terminal. /open $name picks the conversation up again."
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
  new|resume)  cmd_start "$command" "$@" ;;
  peek|say|esc|enter) cmd_pane "$command" "$@" ;;
  ls|list)     cmd_ls ;;
  mobile)      cmd_mobile "$@" ;;
  close|kill)  cmd_close "$@" ;;
  remote)      cmd_remote "$@" ;;
  unlock)      cmd_unlock ;;
  help|-h|--help) usage ;;
  *)
    printf 'Unknown command: %s\n\n' "$command" >&2
    usage >&2
    exit 1
    ;;
esac
