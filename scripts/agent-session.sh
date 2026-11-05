#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: agent-session.sh <command> [args]

Persistent tmux sessions for coding agents, local or over SSH.

Shared commands (the same verbs as Telegram):
  ls [--all] [--json]     List sessions, then stopped records. --all adds
                          closed and expired ones, --json prints one array
  open <name> [agent]      Attach, starting the session if it is not running
  close <name> [--forget]  Stop a session and mark its record closed, so a
                          later open resumes its conversation. --forget
                          deletes the record instead
  peek <name> [lines]      Read the agent pane, default 30 lines, maximum 60
  say <name> <text...>     Type text literally and press Enter
  esc <name>              Send Escape to the agent pane
  enter <name>            Press Enter in the agent pane
  help                    Show this help

Terminal commands:
  mobile <name>           Force an additional terminal client
  remote [args...]         Run the same command on the hub over SSH
  adopt [name [agent]]     Write records for running sessions started before
                          records existed
  restore [--dry-run]      Start every open record that is not running, each
                          in its own conversation. A record idle for
                          AGENT_SESSION_EXPIRE_DAYS is expired instead
  history [name]          List each name's conversations, newest first
  unlock                  Unlock the hub's encrypted home, then run restore
                          (hub only). Runs restore when already unlocked too

Agents: claude (default for a new name), codex, opencode, shell. A recorded
name keeps its record's agent unless another is given.

Each session has a record, which keeps its directory, agent and Claude
conversation. Opening a recorded name resumes its own conversation. A new name
starts a fresh conversation, and never continues another session's.

Options for open:
  --fresh                 Start a blank conversation
  --resume                Continue the newest conversation in the directory
                          that no record holds, when the record has none
  --conversation <id>      Resume this Claude conversation (see ags history)
  --agent <a>              Select the agent (alternative to the positional agent)
  --dir <path>             Override the working directory
  --repo                  Look for a repository named after the session
  --detached              Start or reuse without attaching
  --resolve               Print the directory open --repo would start in, and
                          exit without running tmux

Where a session starts, first match wins:
  --dir, then the session's record, then with --repo a git repository under
  AGENT_SESSION_REPOS (an exact name, otherwise the only one named
  <name>-something, so skills finds tools/skills-registry), falling back to
  the default root. Without --repo, the current directory, moved up to its
  git top level. / and the home directory are refused, use --dir . to start
  there anyway.

Explicit forms, kept for scripts and the Telegram buttons:
  new <name> [agent]       open --fresh --detached --repo
  resume <name> [agent]    open --resume --detached --repo
  kill <name>             close

Examples:
  ags open skp --repo
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
  AGENT_SESSION_STATE  Where records live (default: /srv/services/agents/state
                       on the hub, else ~/.local/state/ags)
  AGENT_SESSION_LOCKED 1 or 0 overrides the encrypted-home check
  AGENT_SESSION_EXPIRE_DAYS  Idle days before restore expires a record
                       (default 14, 0 turns expiry off)
  AGENT_SESSION_RESTORE_GAP  Seconds between restore's starts (default 5)
  PANEPORT_QUIET       Set by paneport, so a session it opens is not announced
  AGENT_BRIDGE_QUIET   Set by the Telegram bridge, for the same reason

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

# Variables a running Claude sets for its children, and those its Bash tool
# adds. A tmux server started from inside a Claude tool run would hand them to
# every pane, and an inherited CLAUDE_CODE_ENTRYPOINT makes the pane's Claude
# record itself as a one-shot run, which the pin hook refuses.
CLAUDE_CHILD_VARS="CLAUDECODE CLAUDE_PID CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_SESSION_ID
CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_SESSION_ATTENDED CLAUDE_PROJECT_DIR CLAUDE_ENV_FILE
CLAUDE_CODE_EXECPATH CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN CLAUDE_EFFORT
COREPACK_ENABLE_AUTO_PIN AGENT_SESSION_VIA"

# Whether a variable describes the calling process rather than the sessions,
# so it must stay out of tmux's global environment. The bridge's variables hold
# its bot token, except the two paths announcing reads.
scrubbed_var() {
  local name="$1" value="${2:-}" each
  case "$name" in
    AGENT_BRIDGE_BIN|AGENT_BRIDGE_ENV) return 1 ;;
    AGENT_BRIDGE_*|PANEPORT_*) return 0 ;;
    GIT_EDITOR) [[ "$value" == true ]]; return ;;
  esac
  for each in $CLAUDE_CHILD_VARS; do
    [[ "$name" != "$each" ]] || return 0
  done
  return 1
}

# The command that starts the server also seeds tmux's global environment, which
# every later pane inherits. The callers' quiet flags, paneport's settings and
# the variables above describe this one call, so they stay out of it, or a
# session opened later at a terminal would go unannounced.
tmux_with_conf() {
  local name
  local -a scrub=()
  for name in $(compgen -e); do
    if scrubbed_var "$name" "${!name:-}"; then
      scrub+=(-u "$name")
    fi
  done
  if [[ -n "$TMUX_CONF" ]]; then
    env ${scrub[@]+"${scrub[@]}"} tmux -f "$TMUX_CONF" "$@"
  else
    env ${scrub[@]+"${scrub[@]}"} tmux "$@"
  fi
}

# A server that is already running may have been started dirty, before this
# launcher scrubbed anything. Clean its global environment before a new pane
# inherits it. The server's environment, not this process's, says what is set.
clean_server_environment() {
  local line name
  tmux list-sessions >/dev/null 2>&1 || return 0
  while IFS= read -r line; do
    [[ "$line" == *=* ]] || continue
    name="${line%%=*}"
    if scrubbed_var "$name" "${line#*=}"; then
      tmux set-environment -g -u "$name" 2>/dev/null || true
    fi
  done < <(tmux show-environment -g 2>/dev/null || true)
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

physical() {
  (cd "$1" 2>/dev/null && pwd -P)
}

lower() {
  printf '%s\n' "$1" | tr 'A-Z' 'a-z'
}

is_uuid() {
  [[ "$1" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$ ]]
}

# The value of a string field in one line of JSON, without jq or Python.
json_string() {
  sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

json_number() {
  sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n 1
}

json_escape() {
  local value="${1//\\/\\\\}"
  printf '%s' "${value//\"/\\\"}"
}

file_mtime() {
  date -r "$1" +%s 2>/dev/null
}

# An ISO time from a transcript as epoch seconds. GNU date first, then BSD's.
iso_epoch() {
  local stamp="${1:0:19}"
  date -u -d "$stamp" +%s 2>/dev/null \
    || date -j -u -f '%Y-%m-%dT%H:%M:%S' "$stamp" +%s 2>/dev/null
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
      compgen -G "$(project_dir "$dir")/*.jsonl" >/dev/null
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

# Records. One directory per session under sessions/, one file per field, each
# written to a temporary sibling and renamed, so a reader never sees half a
# value and two writers of different fields never race. A record counts only
# when it holds dir, which a new record writes last.

state_dir() {
  if [[ -n "${AGENT_SESSION_STATE:-}" ]]; then
    printf '%s\n' "$AGENT_SESSION_STATE"
  elif [[ -d /srv/services/agents ]]; then
    printf '%s\n' /srv/services/agents/state
  else
    printf '%s\n' "${XDG_STATE_HOME:-$HOME/.local/state}/ags"
  fi
}

STATE_DIR="$(state_dir)"
SESSIONS_DIR="$STATE_DIR/sessions"
HISTORY_LOG="$STATE_DIR/history.log"

# For the verbs that must write. Fails naming the directory, before tmux.
ensure_state() {
  if (umask 077 && mkdir -p "$SESSIONS_DIR") 2>/dev/null && [[ -w "$SESSIONS_DIR" ]]; then
    return 0
  fi
  printf 'The state directory %s is not writable.\n' "$STATE_DIR" >&2
  return 1
}

# Every record's name, sorted. Lookups compare these exactly rather than
# testing a path, because the Mac's file system ignores case.
record_names() {
  local path
  [[ -d "$SESSIONS_DIR" ]] || return 0
  for path in "$SESSIONS_DIR"/*; do
    [[ -f "$path/dir" ]] && printf '%s\n' "${path##*/}"
  done | LC_ALL=C sort
}

record_has() {
  local each
  while IFS= read -r each; do
    [[ "$each" != "$1" ]] || return 0
  done < <(record_names)
  return 1
}

record_get() {
  local value=""
  [[ -f "$SESSIONS_DIR/$1/$2" ]] || return 0
  IFS= read -r value < "$SESSIONS_DIR/$1/$2" || true
  printf '%s\n' "$value"
}

record_closed() {
  [[ -f "$SESSIONS_DIR/$1/closed" ]]
}

record_open() {
  record_has "$1" && ! record_closed "$1"
}

write_field() {
  local tmp="$1.tmp.$$"
  if [[ -n "$2" ]]; then printf '%s\n' "$2"; fi > "$tmp" && mv -f "$tmp" "$1"
}

# Writes one field of an existing record, and nothing when the record has no
# dir, so a write racing close --forget never leaves a directory without one.
record_set() {
  [[ -f "$SESSIONS_DIR/$1/dir" ]] || return 0
  write_field "$SESSIONS_DIR/$1/$2" "$3"
}

record_seen() {
  [[ -f "$SESSIONS_DIR/$1/dir" ]] || return 0
  touch "$SESSIONS_DIR/$1/seen"
}

log_event() {
  local event="$1"
  shift
  printf '%s %s %s\n' "$(date +%Y-%m-%dT%H:%M:%S%z)" "$event" "$*" >> "$HISTORY_LOG" 2>/dev/null || true
}

# Writes a whole record for a session open is creating or adopt is recording.
# The pin goes in before dir, and a pin that differs from the one already there
# is logged with the source that chose it. An empty pin removes the file, so a
# Claude record reopened as another agent keeps no stale conversation.
record_create() {
  local name="$1" dir="$2" agent="$3" pane="$4" pin="$5" source="$6" path previous=""
  path="$SESSIONS_DIR/$name"
  record_has "$name" && previous="$(record_get "$name" conversation)"
  (umask 077 && mkdir -p "$path") || return 1
  write_field "$path/agent" "$agent"
  write_field "$path/pane" "$pane"
  if [[ -n "$pin" ]]; then
    write_field "$path/conversation" "$pin"
  else
    rm -f "$path/conversation"
  fi
  # A start by restore leaves seen alone, so a session nobody uses keeps aging.
  if [[ "${AGENT_SESSION_VIA:-}" != restore || ! -e "$path/seen" ]]; then
    touch "$path/seen"
  fi
  write_field "$path/dir" "$dir"
  rm -f "$path/closed"
  if [[ -n "$pin" && "$pin" != "$previous" ]]; then
    log_event pin "$name" "$pin" "$source" "$dir"
  fi
}

# Every later change of conversation goes through here. The same id writes and
# logs nothing, which is the usual case, since the hook fires at every start.
set_pin() {
  local name="$1" id="$2" source="$3" dir="$4"
  [[ -f "$SESSIONS_DIR/$name/dir" ]] || return 0
  [[ "$(record_get "$name" conversation)" != "$id" ]] || return 0
  record_set "$name" conversation "$id"
  log_event pin "$name" "$id" "$source" "$dir"
}

# Whether a record other than NAME, open or closed, pins this id.
pinned_elsewhere() {
  local id="$1" name="${2:-}" each
  while IFS= read -r each; do
    [[ "$each" != "$name" ]] || continue
    [[ "$(record_get "$each" conversation)" != "$id" ]] || return 0
  done < <(record_names)
  return 1
}

valid_name() {
  [[ "$1" =~ ^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$ ]]
}

# Prints the record whose name differs from NAME only in case.
name_clashes() {
  local name="$1" folded each
  folded="$(lower "$name")"
  while IFS= read -r each; do
    if [[ "$each" != "$name" && "$(lower "$each")" == "$folded" ]]; then
      printf '%s\n' "$each"
      return 0
    fi
  done < <(record_names)
  return 1
}

# The session a grouped client joined, or nothing for any other session.
group_of() {
  local name group
  while IFS=$'\t' read -r name group; do
    if [[ "$name" == "$1" ]]; then
      [[ -z "$group" || "$group" == "$name" ]] || printf '%s\n' "$group"
      return 0
    fi
  done < <(tmux list-sessions -F $'#{session_name}\t#{session_group}' 2>/dev/null || true)
}

home_locked() {
  case "${AGENT_SESSION_LOCKED:-}" in
    1) return 0 ;;
    0) return 1 ;;
  esac
  command -v ecryptfs-mount-private >/dev/null 2>&1 || return 1
  [[ -d "/home/.ecryptfs/${USER:-$(id -un)}" ]] || return 1
  # Read the mount table first. Under pipefail, grep -q closing the pipe early
  # fails mount with SIGPIPE, which would read an unlocked home as locked.
  local mounts
  mounts="$(mount)"
  ! grep -q "on $HOME type ecryptfs" <<<"$mounts"
}

project_dir() {
  printf '%s/projects/%s\n' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" "${1//[^A-Za-z0-9]/-}"
}

idle_days() {
  local days=$(( ($2 - $1) / 86400 ))
  ((days > 0)) || days=0
  printf '%s\n' "$days"
}

# The boot time as epoch seconds, or 0 where it is not known (the Mac, until
# 0007 defines it). A lock or a sessions file is then never judged by its age.
boot_epoch() {
  local boot=""
  if [[ -n "${AGENT_SESSION_BOOT_EPOCH:-}" ]]; then
    boot="$AGENT_SESSION_BOOT_EPOCH"
  elif [[ -r /proc/stat ]]; then
    boot="$(awk '/^btime /{ print $2 }' /proc/stat)"
  fi
  printf '%s\n' "${boot:-0}"
}

# The newer of seen's time and, for a Claude record, the time of the last
# message in its transcript. Not the transcript's own time, because a bare
# claude --resume writes to the file before any message.
last_activity() {
  local name="$1" best=0 pin dir transcript line stamp when
  [[ -e "$SESSIONS_DIR/$name/seen" ]] && best="$(file_mtime "$SESSIONS_DIR/$name/seen" || echo 0)"
  if [[ "$(record_get "$name" agent)" == claude ]] && ! home_locked; then
    pin="$(record_get "$name" conversation)"
    dir="$(record_get "$name" dir)"
    transcript="$(project_dir "$dir")/$pin.jsonl"
    if [[ -n "$pin" && -r "$transcript" ]]; then
      line="$(grep -E '"type"[[:space:]]*:[[:space:]]*"(user|assistant)"' "$transcript" 2>/dev/null | tail -n 1 || true)"
      stamp="$(printf '%s\n' "$line" | json_string timestamp)"
      if [[ -n "$stamp" ]] && when="$(iso_epoch "$stamp")" && ((when > best)); then
        best="$when"
      fi
    fi
  fi
  printf '%s\n' "$best"
}

# What idleness is measured against: the newest activity of any record from
# before the boot. Days the host spent off or locked count against no record,
# and an open between the boot and restore does not move it.
host_last_activity() {
  local boot best=0 each when
  boot="$(boot_epoch)"
  ((boot > 0)) || boot="$(date +%s)"
  while IFS= read -r each; do
    when="$(last_activity "$each")"
    if ((when < boot && when > best)); then best="$when"; fi
  done < <(record_names)
  if ((best > 0)); then printf '%s\n' "$best"; else date +%s; fi
}

launcher_path() {
  printf '%s/%s\n' "$(cd "$(dirname "$0")" && pwd -P)" "$(basename "$0")"
}

# A client coming or going counts as activity, so a session used at the desk
# never expires.
set_seen_hooks() {
  local name="$1" command
  command="run-shell -b '\"$(launcher_path)\" seen $name'"
  tmux set-hook -t "=$name:" client-attached "$command" 2>/dev/null || true
  tmux set-hook -t "=$name:" client-detached "$command" 2>/dev/null || true
}

# The conversation a running Claude in this pane holds, from Claude's own
# per-process file. Internal to Claude Code, so it only ever fills an empty pin.
claude_pid_conversation() {
  local pane="$1" shell_pid pid file id
  shell_pid="$(tmux display-message -p -t "$pane" '#{pane_pid}' 2>/dev/null)" || return 1
  [[ -n "$shell_pid" ]] || return 1
  for pid in $(pgrep -P "$shell_pid" 2>/dev/null || true); do
    file="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions/$pid.json"
    [[ -r "$file" ]] || continue
    id="$(json_string sessionId < "$file")"
    if is_uuid "$id"; then
      lower "$id"
      return 0
    fi
  done
  return 1
}

# The lookup's id, when it is usable as this record's pin.
lookup_pin() {
  local name="$1" dir="$2" pane="$3" id
  [[ -n "$pane" ]] || return 1
  id="$(claude_pid_conversation "$pane")" || return 1
  [[ -f "$(project_dir "$dir")/$id.jsonl" ]] || return 1
  pinned_elsewhere "$id" "$name" && return 1
  printf '%s\n' "$id"
}

fill_pin_by_lookup() {
  local name="$1" dir pane id
  record_has "$name" || return 0
  [[ "$(record_get "$name" agent)" == claude ]] || return 0
  [[ -z "$(record_get "$name" conversation)" ]] || return 0
  dir="$(record_get "$name" dir)"
  pane="$(record_get "$name" pane)"
  id="$(lookup_pin "$name" "$dir" "$pane")" || return 0
  set_pin "$name" "$id" lookup "$dir"
}

agent_window() {
  tmux list-windows -t "=$1" -F $'#{window_name}\t#{pane_id}\t#{pane_current_command}' 2>/dev/null \
    | awk -F '\t' '$1 == "agent" { print; exit }'
}

unrecorded_session() {
  [[ "$(tmux show-options -qv -t "=$1:" @ags-unrecorded 2>/dev/null)" == 1 ]]
}

# Records one running session. Prints an adopted or skipped line, and returns
# 1 when it skipped.
adopt_session() {
  local name="$1" agent="${2:-}" group clash row window pane command path dir pin="" note=""

  skip() { printf 'skipped %s: %s\n' "$name" "$1"; }

  tmux has-session -t "=$name" 2>/dev/null || { skip "not running"; return 1; }
  group="$(group_of "$name")"
  [[ -z "$group" ]] || { skip "grouped client of $group"; return 1; }
  valid_name "$name" || { skip "name not valid"; return 1; }
  if clash="$(name_clashes "$name")"; then skip "name clashes with $clash"; return 1; fi
  ! record_has "$name" || { skip "already recorded"; return 1; }
  ! unrecorded_session "$name" || { skip "started while locked"; return 1; }
  row="$(agent_window "$name")"
  [[ -n "$row" ]] || { skip "no agent window"; return 1; }
  IFS=$'\t' read -r window pane command <<< "$row"
  if [[ -z "$agent" ]]; then
    case "$command" in
      codex|opencode|claude) agent="$command" ;;
      bash|zsh|sh|fish|dash|ksh|-bash|-zsh|-sh) agent=shell ;;
      *)
        if [[ "$command" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
          agent=claude
        else
          skip "unknown command $command, use ags adopt $name AGENT"
          return 1
        fi
        ;;
    esac
  fi
  path="$(tmux display-message -p -t "=$name:" '#{session_path}' 2>/dev/null)" || path=""
  dir="$(physical "$path")" || { skip "its directory $path is gone"; return 1; }
  if [[ "$agent" == claude ]]; then
    pin="$(lookup_pin "$name" "$dir" "$pane")" || pin=""
    [[ -n "$pin" ]] || note=", no pin: reopens the directory's newest unpinned conversation"
  elif [[ "$agent" == shell ]]; then
    note=", if an agent exited here, close it with --forget and adopt it again with its agent"
  fi
  record_create "$name" "$dir" "$agent" "$pane" "$pin" adopt
  set_seen_hooks "$name"
  printf 'adopted %s (%s in %s)%s\n' "$name" "$agent" "$dir" "$note"
}

# Tell the Telegram bridge about a lifecycle change on this host, so a session
# started at a terminal gets its topic without a trip through /bind. It runs in
# the background and says nothing. No bridge, no configuration or no network
# all mean nothing happens, and the terminal never waits on Telegram. The
# bridge sets AGENT_BRIDGE_QUIET and paneport sets PANEPORT_QUIET when they
# call this launcher, since each announces its own sessions. Under nohup it
# outlives a close run inside the session it kills.
announce_session() {
  local bridge="${AGENT_BRIDGE_BIN:-/srv/services/agents/bin/agent-bridge.py}"
  local env_file="${AGENT_BRIDGE_ENV:-/srv/services/agents/bridge.env}"

  [[ -z "${AGENT_BRIDGE_QUIET:-}" && -z "${PANEPORT_QUIET:-}" ]] || return 0
  [[ -r "$bridge" && -r "$env_file" ]] || return 0
  command -v python3 >/dev/null 2>&1 || return 0

  ( AGENT_BRIDGE_ENV="$env_file" nohup python3 "$bridge" topic "$@" >/dev/null 2>&1 & ) || true
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

# What gets typed into the pane. Claude is always given the conversation id,
# chosen here for a new one, so the record never depends on the hook for a
# session this launcher starts. Codex and OpenCode resume by directory.
agent_command() {
  local agent="$1" resume="$2" id="${3:-}" name="${4:-}"

  case "$agent" in
    claude)
      if [[ "$resume" == true ]]; then
        printf 'claude --resume %s\n' "$id"
      else
        printf 'claude --session-id %s --name %s\n' "$id" "$name"
      fi
      ;;
    codex)    if [[ "$resume" == true ]]; then printf 'codex resume --last\n'; else printf 'codex\n'; fi ;;
    opencode) if [[ "$resume" == true ]]; then printf 'opencode --continue\n'; else printf 'opencode\n'; fi ;;
    shell)    printf '\n' ;;
    *)        agent_binary "$agent" ;;
  esac
}

new_conversation_id() {
  local id
  id="$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid 2>/dev/null || true)"
  if ! is_uuid "$id"; then
    printf 'Could not generate a conversation id (no uuidgen).\n' >&2
    exit 1
  fi
  lower "$id"
}

# Rule 2. A pin is usable when its transcript exists and holds a message.
# Otherwise prints why: missing or empty.
pin_alive() {
  local transcript
  transcript="$(project_dir "$1")/$2.jsonl"
  if [[ ! -f "$transcript" ]]; then
    printf 'missing\n'
    return 1
  fi
  if ! grep -q '"type"[[:space:]]*:[[:space:]]*"user"' "$transcript" 2>/dev/null; then
    printf 'empty\n'
    return 1
  fi
}

# Conversation ids a running Claude holds right now. A sessions file older
# than the boot was left by a crash, and its pid may belong to anything since.
live_conversations() {
  local boot file pid id
  boot="$(boot_epoch)"
  for file in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/sessions/*.json; do
    [[ -f "$file" ]] || continue
    (( $(file_mtime "$file" || echo 0) >= boot )) || continue
    pid="$(json_number pid < "$file")"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null || continue
    id="$(json_string sessionId < "$file")"
    is_uuid "$id" && lower "$id"
  done
  return 0
}

# Rule 3. The newest interactive transcript in the directory that holds a
# message, that no record pins and that no running Claude is writing.
newest_unpinned_transcript() {
  local folder file id entry live
  folder="$(project_dir "$1")"
  [[ -d "$folder" ]] || return 1
  live="$(live_conversations)"
  while IFS= read -r file; do
    [[ "$file" == *.jsonl && -f "$folder/$file" ]] || continue
    id="${file%.jsonl}"
    is_uuid "$id" || continue
    id="$(lower "$id")"
    entry="$(grep -m 1 -o '"entrypoint"[[:space:]]*:[[:space:]]*"[^"]*"' "$folder/$file" 2>/dev/null | head -n 1 | json_string entrypoint || true)"
    [[ "$entry" != sdk-* ]] || continue
    grep -q '"type"[[:space:]]*:[[:space:]]*"user"' "$folder/$file" 2>/dev/null || continue
    pinned_elsewhere "$id" && continue
    if printf '%s\n' "$live" | grep -qx "$id"; then continue; fi
    printf '%s\n' "$id"
    return 0
  done < <(ls -t "$folder" 2>/dev/null)
  return 1
}

# Inside tmux, attaching is an error. Switching is what was meant.
enter_session() {
  local name="$1"

  if [[ -n "${TMUX:-}" ]]; then
    tmux switch-client -t "=$name"
  else
    tmux attach-session -t "=$name"
  fi
}

# Where open --repo would start, for --resolve: --dir, the record, the
# repository, the default root. Fails quietly.
resolve_dir() {
  local name="$1" dir="$2" result
  valid_name "$name" || return 1
  if [[ -n "$dir" ]]; then
    result="$dir"
  elif record_has "$name"; then
    result="$(record_get "$name" dir)"
  else
    result="$(repo_for_name "$name" || resolve_root)"
  fi
  result="$(physical "$result")" || return 1
  [[ "$result" != / ]] || return 1
  printf '%s\n' "$result"
}

refuse() {
  printf '%s\n' "$*" >&2
  exit 1
}

cmd_open() {
  local name="" dir="" agent="" agent_explicit=false mode=auto detached=false
  local resolve=false repo=false conversation_arg="" resume=false
  local recordable=false recorded=false closed=false replaced=false locked=false
  local record_agent="" record_dir="" pin="" source="" label="" phrase="" note=""
  local notfound="" agent_cmd agent_bin pane top home reason folder

  while (($# > 0)); do
    case "$1" in
      --agent)  agent="${2:?--agent needs a value}"; agent_explicit=true; shift 2 ;;
      --dir)    dir="${2:?--dir needs a value}"; shift 2 ;;
      --resume) mode=resume; shift ;;
      --fresh)  mode=fresh; shift ;;
      --repo)   repo=true; shift ;;
      --conversation) conversation_arg="${2:?--conversation needs a value}"; shift 2 ;;
      --detached) detached=true; shift ;;
      --resolve) resolve=true; shift ;;
      -h|--help) usage; exit 0 ;;
      *)
        if [[ -z "$name" ]]; then
          name="$1"
        elif [[ "$1" =~ ^(claude|codex|opencode|shell)$ ]]; then
          agent="$1"
          agent_explicit=true
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

  # paneport asks where a session would start before it creates one itself, so
  # this answers from the filesystem and never touches a tmux server.
  if [[ "$resolve" == true ]]; then
    resolve_dir "$name" "$dir" || exit 1
    return 0
  fi

  if [[ -n "$conversation_arg" ]]; then
    [[ "$mode" == auto ]] || refuse "--conversation cannot be combined with --fresh or --resume."
  fi

  if tmux has-session -t "=$name" 2>/dev/null; then
    if [[ -n "$conversation_arg" ]]; then
      refuse "$name is running. To reopen it on another conversation, close it first (ags close $name, or /close $name in Telegram)."
    fi
    if record_open "$name"; then
      record_seen "$name"
      fill_pin_by_lookup "$name"
    fi
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

  valid_name "$name" \
    || refuse "Session name $name is not valid: up to 64 letters, digits, _ and -, not starting with -."
  if reason="$(name_clashes "$name")"; then
    refuse "A record named $reason differs from $name only in case. Use ags open $reason, or another name."
  fi
  valid_name "$name" && recordable=true
  record_has "$name" && recorded=true
  home_locked && locked=true

  if [[ -n "$dir" && "$dir" == *[[:cntrl:]]* ]]; then
    refuse "The directory holds a control character."
  fi

  if [[ "$locked" == true ]]; then
    [[ "$recorded" == false ]] || refuse "The home is locked, run ags unlock first."
    [[ "${agent:-claude}" == shell ]] \
      || refuse "The home is locked, so only a shell session can start. Run ags unlock first."
    if [[ -n "$dir" ]]; then
      [[ -d "$dir" ]] || refuse "Working directory does not exist: $dir"
    else
      dir="$(resolve_root)"
    fi
    dir="$(physical "$dir")" || refuse "Working directory does not exist: $dir"
    [[ "$dir" != / ]] || refuse "Refusing to start $name in /. Use --dir to name a directory."
    agent=shell label="(home locked)"
  else
    if [[ "$recordable" == true ]]; then
      ensure_state || exit 1
    fi
    if [[ "$recorded" == true ]]; then
      record_closed "$name" && closed=true
      record_agent="$(record_get "$name" agent)"
      record_dir="$(record_get "$name" dir)"
    fi
    [[ -n "$agent" ]] || agent="${record_agent:-claude}"
    agent_binary "$agent" >/dev/null

    if [[ -n "$dir" ]]; then
      [[ -d "$dir" ]] || refuse "Working directory does not exist: $dir"
      dir="$(physical "$dir")"
    fi

    # An explicit agent or --dir that disagrees with the record replaces that
    # field of a closed record, and is refused on an open one.
    if [[ "$recorded" == true ]]; then
      if { [[ "$agent_explicit" == true && "$agent" != "$record_agent" ]] \
           || [[ -n "$dir" && "$dir" != "$record_dir" ]]; }; then
        if [[ "$closed" == false ]]; then
          refuse "$name has an open record ($record_agent in $record_dir). Close it first (ags close $name, or /close $name in Telegram), or use another name."
        fi
        replaced=true
      fi
    fi

    if [[ -n "$dir" ]]; then
      label="(--dir)"
    elif [[ "$recorded" == true ]]; then
      dir="$record_dir"
      [[ -d "$dir" ]] \
        || refuse "The directory of $name's record is gone: $dir. Use --dir to give another (after ags close $name), or ags close $name --forget."
      label="(its record)"
    elif [[ "$repo" == true ]]; then
      if dir="$(repo_for_name "$name")"; then
        label="(repository)"
      else
        dir="$(resolve_root)"
        label="(default root, no repository named $name)"
      fi
      dir="$(physical "$dir")" || refuse "Working directory does not exist: $dir"
      [[ "$dir" != / ]] \
        || refuse "Refusing to start $name in / (default root, no repository named $name). Use --dir to name a directory, or --dir . to start here anyway."
    else
      dir="$(pwd -P)"
      if top="$(git -C . rev-parse --show-toplevel 2>/dev/null)" && [[ -n "$top" ]]; then
        dir="$(physical "$top")"
      fi
      home="$(physical "$HOME" || printf '%s' "$HOME")"
      if [[ "$dir" == / || "$dir" == "$home" ]]; then
        refuse "Refusing to start $name in $dir (current directory). Use --repo to look for a repository named $name, or --dir . to start here anyway."
      fi
      label="(current directory)"
    fi
  fi

  # Which conversation. The first rule that matches decides.
  case "$agent" in
    claude)
      folder="$(project_dir "$dir")"
      encoded="${folder##*/}"
      if ((${#encoded} > 200)); then
        printf 'Warning: %s encodes to more than 200 characters, so Claude shortens it and no conversation will be found.\n' "$dir" >&2
      fi
      if [[ -n "$conversation_arg" ]]; then
        pin="$(lower "$conversation_arg")"
        is_uuid "$pin" || refuse "Not a conversation id: $conversation_arg"
        [[ -f "$folder/$pin.jsonl" ]] || refuse "No conversation $pin in $dir."
        if pinned_elsewhere "$pin" "$name"; then refuse "Conversation $pin belongs to another record."; fi
        resume=true source=conversation phrase="resuming conversation $pin"
      elif [[ "$mode" == fresh || "$replaced" == true ]]; then
        pin="$(new_conversation_id)" source=new phrase="fresh conversation"
      else
        if [[ "$recorded" == true && -n "$(record_get "$name" conversation)" ]]; then
          pin="$(record_get "$name" conversation)"
          if reason="$(pin_alive "$dir" "$pin")"; then
            resume=true phrase="resuming its conversation"
          else
            if [[ "$reason" == missing ]]; then
              note=" (pinned ${pin:0:8} not found)"
            else
              note=" (pinned ${pin:0:8} was never used)"
            fi
            notfound="$pin $reason $(idle_days "$(file_mtime "$SESSIONS_DIR/$name/conversation" || date +%s)" "$(date +%s)")"
            pin=""
          fi
        fi
        if [[ "$resume" == false ]]; then
          if [[ "$mode" == resume || "$recordable" == false ]] \
             || { [[ "$recorded" == true && -z "$notfound" ]] && [[ ! -f "$SESSIONS_DIR/$name/conversation" ]]; }; then
            if pin="$(newest_unpinned_transcript "$dir")"; then
              resume=true source=search phrase="continuing its last conversation"
            else
              pin="$(new_conversation_id)" source=new phrase="fresh conversation (nothing to resume)"
            fi
          else
            pin="$(new_conversation_id)" source=new phrase="fresh conversation"
          fi
        fi
      fi
      ;;
    shell) ;;
    *)
      [[ -z "$conversation_arg" ]] || refuse "--conversation is for Claude sessions only."
      if [[ "$replaced" == true || "$mode" == fresh ]]; then
        resume=false
      elif [[ "$mode" == resume ]]; then
        resume=true
      elif { [[ "$recorded" == true || "$recordable" == false ]]; } \
           && has_conversation "$agent" "$dir"; then
        resume=true
      fi
      if [[ "$resume" == true ]]; then
        phrase="continuing its last conversation"
      else
        phrase="fresh conversation"
      fi
      ;;
  esac
  [[ "$agent" != shell ]] || [[ -z "$conversation_arg" ]] || refuse "--conversation is for Claude sessions only."

  agent_bin="$(agent_binary "$agent")"
  if [[ -n "$agent_bin" ]] && ! command -v "$agent_bin" >/dev/null 2>&1; then
    printf 'Agent %s is not on PATH. Is the home unlocked? See: agent-session.sh unlock\n' \
      "$agent_bin" >&2
    exit 1
  fi
  agent_cmd="$(agent_command "$agent" "$resume" "$pin" "$name")"

  ensure_config
  clean_server_environment

  if ! pane="$(tmux_with_conf new-session -d -s "$name" -c "$dir" -n agent -P -F '#{pane_id}' 2>&1)"; then
    # Another start won the race. Nothing was written, so attach to it.
    if tmux has-session -t "=$name" 2>/dev/null; then
      if [[ "$detached" == true ]]; then
        printf '%s is already running.\n' "$name"
        return 0
      fi
      enter_session "$name"
      return 0
    fi
    printf 'Could not start %s: %s\n' "$name" "$pane" >&2
    exit 1
  fi

  if [[ "$locked" == true ]]; then
    tmux set-option -t "=$name:" @ags-unrecorded 1 2>/dev/null || true
  elif [[ "$recordable" == true ]]; then
    if [[ -n "$notfound" ]]; then
      log_event notfound "$name" $notfound
    fi
    if [[ "$agent" == claude ]]; then
      record_create "$name" "$dir" "$agent" "$pane" "$pin" "$source"
    else
      record_create "$name" "$dir" "$agent" "$pane" "" ""
    fi
  fi
  set_seen_hooks "$name"

  tmux new-window -t "=$name:" -c "$dir" -n shell
  tmux new-window -t "=$name:" -c "$dir" -n git
  tmux select-window -t "=$name:agent"

  if [[ -n "$agent_cmd" ]]; then
    tmux send-keys -t "=$name:agent" "$agent_cmd" Enter
  fi

  local how="started at a terminal" tail=""
  [[ "${AGENT_SESSION_VIA:-}" != restore ]] || how="restored after a reboot"
  [[ -z "$phrase" ]] || tail=", $phrase$note"
  announce_session "$name" --text "🟢 $name $how, $agent in $dir$tail."

  # The first line is what Telegram shows, so it stands on its own.
  printf 'Started %s with %s in %s %s%s.\n' "$name" "$agent" "$dir" "$label" "$tail"
  if [[ "$detached" == true ]]; then
    printf 'Use ags open %s to attach.\n' "$name"
  else
    enter_session "$name"
  fi
}

# Read the agent window even when the user is viewing shell or git.
# The foreground program is observable. An agent's internal task state is not.
cmd_ls() {
  local all=false json=false sessions="" sid name windows attached group detail window running
  local directory program path attachment state agent recorded idle first=true now each row
  local r_name r_state r_running r_attachment r_windows r_directory r_agent r_recorded r_extra r_rdir rdir
  local US=$'\x1f'
  local -a rows=() running_names=()

  while (($# > 0)); do
    case "$1" in
      --all) all=true ;;
      --json) json=true ;;
      *) printf 'Unexpected argument: %s\n\n' "$1" >&2; usage >&2; return 1 ;;
    esac
    shift
  done

  if ! sessions="$(tmux list-sessions -F $'#{session_id}\t#{session_name}\t#{session_windows}\t#{session_attached}\t#{session_group}' 2>&1)"; then
    case "$sessions" in
      *"no server running"*|*"No such file or directory"*|*"error connecting"*) sessions="" ;;
      *) printf '%s\n' "$sessions" >&2; return 1 ;;
    esac
  fi
  if [[ -d "$SESSIONS_DIR" && ! -r "$SESSIONS_DIR" ]]; then
    printf 'Warning: cannot read the records in %s.\n' "$SESSIONS_DIR" >&2
  fi
  now="$(date +%s)"

  # Each row: name, state, running, attachment, windows, directory, agent,
  # recorded, and attached count or idle days. Unit-separator delimited,
  # because read collapses runs of tabs and an empty field would shift the rest.
  if [[ -n "$sessions" ]]; then
    while IFS=$'\t' read -r sid name windows attached group; do
      running_names+=("$name")
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
      # A grouped client shows in the plain listing only.
      if [[ "$json" == true ]]; then
        [[ -z "$group" || "$group" == "$name" ]] || continue
        [[ "$name" != *[[:cntrl:]]* ]] || continue
      fi
      agent="" rdir="" recorded=0
      if record_has "$name"; then
        agent="$(record_get "$name" agent)" rdir="$(record_get "$name" dir)" recorded=1
      fi
      rows+=("$name$US""running$US${running:-unknown}$US$attachment$US$windows$US$directory$US$agent$US$recorded$US$attached$US$rdir")
    done <<< "$sessions"
  fi

  while IFS= read -r name; do
    for each in ${running_names[@]+"${running_names[@]}"}; do
      [[ "$each" != "$name" ]] || continue 2
    done
    idle="$(idle_days "$(last_activity "$name")" "$now")"
    if record_closed "$name"; then
      [[ "$all" == true ]] || continue
      state="$(record_get "$name" closed)"
      [[ "$state" == expired ]] || state=closed
      running="$state"
    else
      state=stopped running="stopped ${idle}d"
    fi
    rdir="$(record_get "$name" dir)"
    rows+=("$name$US$state$US$running$US-$US-$US$rdir$US$(record_get "$name" agent)${US}1$US$idle$US$rdir")
  done < <(record_names)

  if [[ "$json" == true ]]; then
    printf '['
    for row in ${rows[@]+"${rows[@]}"}; do
      IFS="$US" read -r r_name r_state r_running r_attachment r_windows r_directory r_agent r_recorded r_extra r_rdir <<< "$row"
      [[ "$first" == true ]] || printf ','
      first=false
      printf '{"name":"%s","state":"%s",' "$(json_escape "$r_name")" "$r_state"
      if [[ "$r_recorded" == 1 ]]; then
        printf '"agent":"%s","dir":"%s","recorded":true,' "$(json_escape "$r_agent")" "$(json_escape "$r_rdir")"
      else
        printf '"agent":null,"dir":null,"recorded":false,'
      fi
      if [[ "$r_state" == running ]]; then
        if [[ "$r_extra" -gt 0 ]]; then printf '"attached":true,'; else printf '"attached":false,'; fi
        printf '"windows":%s,"idle_days":null}' "$r_windows"
      else
        printf '"attached":null,"windows":null,"idle_days":%s}' "$r_extra"
      fi
    done
    printf ']\n'
    return 0
  fi

  if ((${#rows[@]} == 0)); then
    printf 'No sessions.\n'
    return 0
  fi
  printf '%-16s  %-14s  %-12s  %-7s  %s\n' SESSION RUNNING ATTACHMENT WINDOWS DIRECTORY
  for row in "${rows[@]}"; do
    IFS="$US" read -r name state running attachment windows directory _ <<< "$row"
    printf '%-16s  %-14s  %-12s  %-7s  %s\n' "$name" "$running" "$attachment" "$windows" "$directory"
  done
}

# Shared lifecycle verbs are detached so a command has the same effect from
# a terminal or Telegram. open remains the explicit terminal attachment step.
# A phone has no current directory, so these search repositories by name.
cmd_start() {
  local verb="$1" name="${2:-}"
  local -a options=(--detached --repo)
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
  if [[ $# -gt 0 && "$1" != -* ]]; then
    agent_binary "$1" >/dev/null
    options+=(--agent "$1")
    shift
  fi
  while (($# > 0)); do
    case "$1" in
      --agent) agent_binary "${2:?--agent needs a value}" >/dev/null; options+=(--agent "$2"); shift 2 ;;
      --dir) options+=(--dir "${2:?--dir needs a value}"); shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) printf 'Unexpected argument: %s\n' "$1" >&2; return 1 ;;
    esac
  done
  if [[ "$verb" == resume ]]; then options+=(--resume); else options+=(--fresh); fi
  cmd_open "$name" "${options[@]}"
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
  local name="$1" client="${1}-m" seen

  # A grouped session shares the windows but keeps its own size and its own
  # current window, so a second client does not reshape the first one's view.
  #
  # Cleanup is a detach hook rather than `destroy-unattached on`, which reaps
  # the session the moment it is created, before there is a client to attach.
  # Both hooks count the client as activity on the session it joined.
  if ! tmux has-session -t "=$client" 2>/dev/null; then
    seen="run-shell -b '\"$(launcher_path)\" seen $name'"
    tmux new-session -d -t "$name" -s "$client"
    tmux set-hook -t "$client" client-attached "$seen"
    tmux set-hook -t "$client" client-detached "$seen ; kill-session -t '$client'"
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

# Closing marks the record closed, so the next open resumes exactly this
# conversation, and stops the agent and the tmux session. Everything that must
# happen comes before the last kill, because a close run inside its own
# session dies with it.
cmd_close() {
  local name="" forget=false running=false group client text

  while (($# > 0)); do
    case "$1" in
      --forget) forget=true ;;
      *)
        if [[ -z "$name" ]]; then name="$1"; else printf 'Unexpected argument: %s\n' "$1" >&2; exit 1; fi
        ;;
    esac
    shift
  done
  [[ -n "$name" ]] || { printf 'A session name is required.\n' >&2; exit 1; }
  name="${name//[.:]/-}"

  if tmux has-session -t "=$name" 2>/dev/null; then
    running=true
    group="$(group_of "$name")"
    if [[ -n "$group" ]]; then
      tmux kill-session -t "=$name"
      return 0
    fi
  fi

  if [[ "$running" == false ]]; then
    record_has "$name" || { printf 'No session or record named %s.\n' "$name" >&2; exit 1; }
    if record_closed "$name"; then
      if [[ "$forget" == true ]]; then
        rm -rf "${SESSIONS_DIR:?}/$name"
        printf 'Forgot the record of %s.\n' "$name"
      else
        printf '%s is already closed.\n' "$name"
      fi
      return 0
    fi
  fi

  if [[ "$running" == true && "$forget" == false ]] && ! record_has "$name" \
     && valid_name "$name" && ! unrecorded_session "$name" && ! home_locked; then
    if ensure_state 2>/dev/null; then
      adopt_session "$name" || true
    else
      printf 'Warning: the state directory %s is not writable, closing without a record.\n' "$STATE_DIR" >&2
    fi
  fi
  [[ "$running" == false ]] || fill_pin_by_lookup "$name"

  if record_has "$name"; then
    if [[ "$forget" == true ]]; then
      rm -rf "${SESSIONS_DIR:?}/$name" \
        || printf 'Warning: could not remove the record of %s.\n' "$name" >&2
    else
      record_set "$name" closed close 2>/dev/null \
        || printf 'Warning: could not mark %s closed in %s.\n' "$name" "$STATE_DIR" >&2
    fi
    log_event close "$name"
  fi

  if [[ "$forget" == true ]]; then
    text="closed $name at a terminal and forgot its record. /open $name starts it again by name."
  else
    text="closed $name at a terminal. /open $name picks the conversation up again."
  fi
  announce_session "$name" --no-create --no-buttons --text "$text"

  if [[ "$running" == true ]]; then
    # No copy of the agent may survive in a second client.
    while IFS=$'\t' read -r client group; do
      if [[ "$group" == "$name" && "$client" != "$name" ]]; then
        tmux kill-session -t "=$client" 2>/dev/null || true
      fi
    done < <(tmux list-sessions -F $'#{session_name}\t#{session_group}' 2>/dev/null || true)
    tmux kill-session -t "=$name"
  fi
}

cmd_adopt() {
  local name="${1:-}" agent="${2:-}" each status=0
  home_locked && { printf 'The home is locked, run ags unlock first.\n' >&2; exit 1; }
  ensure_state || exit 1
  [[ -z "$agent" ]] || agent_binary "$agent" >/dev/null
  if [[ -n "$name" ]]; then
    adopt_session "${name//[.:]/-}" "$agent" || status=1
    return "$status"
  fi
  while IFS= read -r each; do
    [[ -n "$each" ]] || continue
    adopt_session "$each" || true
  done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null || true)
}

# Starts every open record with no running session, one at a time, each in a
# process of its own so a refused record cannot stop the loop.
cmd_restore() {
  local dry=false lock pid since days reference each idle restored=0 expired=0 failed=0
  local gap="${AGENT_SESSION_RESTORE_GAP:-5}" output started=false server
  local -a starting=()

  while (($# > 0)); do
    case "$1" in
      --dry-run) dry=true ;;
      *) printf 'Usage: ags restore [--dry-run]\n' >&2; exit 1 ;;
    esac
    shift
  done
  home_locked && { printf 'The home is locked, run ags unlock first.\n' >&2; exit 1; }
  ensure_state || exit 1

  if [[ "$dry" == false ]]; then
    lock="$STATE_DIR/restore.lock"
    if ! mkdir "$lock" 2>/dev/null; then
      # A lock left by a power cut mid-restore names a pid that is not running,
      # or is older than the boot.
      pid="$(cat "$lock/pid" 2>/dev/null || true)"
      since="$(file_mtime "$lock" || echo 0)"
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && ((since >= $(boot_epoch))); then
        printf 'a restore is already running\n' >&2
        exit 1
      fi
      rm -rf "$lock"
      mkdir "$lock" 2>/dev/null || { printf 'a restore is already running\n' >&2; exit 1; }
    fi
    printf '%s\n' "$$" > "$lock/pid"
    # Expanded now, since lock is local and the trap runs after the return.
    trap "rm -rf $(printf '%q' "$lock")" EXIT
  fi

  days="${AGENT_SESSION_EXPIRE_DAYS:-14}"
  reference="$(host_last_activity)"
  while IFS= read -r each; do
    record_open "$each" || continue
    tmux has-session -t "=$each" 2>/dev/null && continue
    idle="$(idle_days "$(last_activity "$each")" "$reference")"
    if ((days > 0 && idle >= days)); then
      if [[ "$dry" == true ]]; then
        printf 'would expire %s (idle %s days)\n' "$each" "$idle"
      else
        record_set "$each" closed expired
        log_event expired "$each" "$idle"
        printf 'expired %s (idle %s days), /open %s resumes it\n' "$each" "$idle" "$each"
      fi
      expired=$((expired + 1))
      continue
    fi
    starting+=("$each")
  done < <(record_names)

  for each in ${starting[@]+"${starting[@]}"}; do
    if [[ "$dry" == true ]]; then
      printf 'would start %s (%s in %s)\n' "$each" "$(record_get "$each" agent)" "$(record_get "$each" dir)"
      continue
    fi
    if [[ "$started" == true ]] && ((gap > 0)); then sleep "$gap"; fi
    started=true
    if output="$(AGENT_SESSION_VIA=restore "$BASH" "$(launcher_path)" open "$each" --detached 2>&1)" \
       && tmux has-session -t "=$each" 2>/dev/null; then
      restored=$((restored + 1))
      log_event restore "$each"
      printf '%s\n' "$(printf '%s\n' "$output" | head -n 1)"
    else
      failed=$((failed + 1))
      printf 'failed %s: %s\n' "$each" "$(printf '%s\n' "$output" | head -n 1)"
    fi
  done

  [[ "$dry" == false ]] || return 0
  printf 'Restored %s, expired %s, failed %s.\n' "$restored" "$expired" "$failed"

  # A server the bridge's sandboxed unit started cannot sudo from its panes.
  if [[ -r /proc/self/status ]] && server="$(tmux display-message -p '#{pid}' 2>/dev/null)" \
     && grep -q '^NoNewPrivs:[[:space:]]*1' "/proc/$server/status" 2>/dev/null; then
    printf 'Warning: this tmux server was started by the bridge, so its panes cannot sudo. If it holds only sessions started while the home was locked, run tmux kill-server, then ags restore again.\n' >&2
  fi
  ((failed == 0))
}

cmd_history() {
  local name="${1:-}" each
  if [[ ! -e "$HISTORY_LOG" ]]; then
    printf 'No history.\n'
    return 0
  fi
  [[ -r "$HISTORY_LOG" ]] || { printf 'Cannot read the history in the state directory %s.\n' "$STATE_DIR" >&2; exit 1; }
  if [[ -n "$name" ]]; then
    history_of "${name//[.:]/-}"
    return 0
  fi
  while IFS= read -r each; do
    printf '%s\n' "$each"
    history_of "$each" | sed 's/^/  /'
  done < <(awk '{ print $3 }' "$HISTORY_LOG" | LC_ALL=C sort -u)
}

# One name's conversations, newest first by the date each was first pinned.
history_of() {
  local name="$1" current="" locked=false when id source dir state mark
  record_has "$name" && current="$(record_get "$name" conversation)"
  home_locked && locked=true
  while IFS=$'\t' read -r when id source dir; do
    if [[ "$locked" == true ]]; then
      state=unknown
    elif [[ -f "$(project_dir "$dir")/$id.jsonl" ]]; then
      state=kept
    else
      state=gone
    fi
    mark=""
    [[ "$id" != "$current" ]] || mark="  current"
    printf '%s  %s  %-12s  %s%s\n' "${when:0:10}" "$id" "$source" "$state" "$mark"
  done < <(awk -v name="$name" '
    $2 == "pin" && $3 == name && !seen[$4]++ {
      dir = $0
      for (i = 1; i <= 5; i++) sub(/^[^ ]+ /, "", dir)
      rows[++n] = $1 "\t" $4 "\t" $5 "\t" dir
    }
    END { for (i = n; i >= 1; i--) print rows[i] }' "$HISTORY_LOG")
}

# Records only ever hold a session's own pane, so this follows a conversation
# the launcher did not start: /clear, an in-app /resume, or claude typed by
# hand in the agent pane. Every check fails silently, cheapest first, and it
# prints nothing, because a SessionStart hook's stdout joins the conversation.
cmd_pin() {
  local payload id transcript session group pane dir shell_pid parent new_pane="" owner

  payload="$(cat 2>/dev/null || true)"
  [[ "${CLAUDE_CODE_ENTRYPOINT:-}" != sdk-* ]] || return 0
  [[ -n "${TMUX_PANE:-}" ]] || return 0
  command -v tmux >/dev/null 2>&1 || return 0

  id="$(printf '%s\n' "$payload" | json_string session_id)"
  transcript="$(printf '%s\n' "$payload" | json_string transcript_path)"
  is_uuid "$id" || return 0
  id="$(lower "$id")"

  session="$(tmux display-message -p -t "$TMUX_PANE" '#{session_name}' 2>/dev/null)" || return 0
  [[ -n "$session" ]] || return 0
  group="$(group_of "$session")"
  [[ -z "$group" ]] || session="$group"
  record_open "$session" || return 0
  [[ "$(record_get "$session" agent)" == claude ]] || return 0

  pane="$(record_get "$session" pane)"
  if [[ "$TMUX_PANE" != "$pane" ]]; then
    # Pane ids restart after a reboot, so a recorded pane may be empty, gone or
    # in another session. Then this session's agent window is the agent pane.
    if [[ -n "$pane" ]] && owner="$(tmux display-message -p -t "$pane" '#{session_name}' 2>/dev/null)" \
       && [[ -n "$owner" ]]; then
      group="$(group_of "$owner")"
      [[ -z "$group" ]] || owner="$group"
      [[ "$owner" != "$session" ]] || return 0
    fi
    [[ "$(tmux display-message -p -t "$TMUX_PANE" '#{window_name}' 2>/dev/null)" == agent ]] || return 0
    new_pane="$TMUX_PANE"
  fi

  [[ -n "${CLAUDE_PID:-}" ]] || return 0
  shell_pid="$(tmux display-message -p -t "$TMUX_PANE" '#{pane_pid}' 2>/dev/null)" || return 0
  parent="$(ps -o ppid= -p "$CLAUDE_PID" 2>/dev/null | tr -d ' ')" || return 0
  [[ -n "$parent" && "$parent" == "$shell_pid" ]] || return 0

  dir="$(record_get "$session" dir)"
  [[ "${transcript%/*}" == "$(project_dir "$dir")" ]] || return 0
  [[ "$(lower "${transcript##*/}")" == "$id.jsonl" ]] || return 0
  pinned_elsewhere "$id" "$session" && return 0

  [[ -z "$new_pane" ]] || record_set "$session" pane "$new_pane"
  set_pin "$session" "$id" hook "$dir"
}

cmd_seen() {
  local name="${1:-}"
  [[ -n "$name" ]] || return 0
  record_open "$name" || return 0
  record_seen "$name"
}

cmd_remote() {
  local host="${AGENT_SESSION_HOST:-mint}" remote_args quoted arg

  # A remote command lands in the hub's home, so name search is the only
  # meaning a remote open can have.
  if [[ "${1:-}" == open ]]; then
    set -- "$@" --repo
  fi

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

# Unlocking runs restore, and so does an unlock of a home already unlocked,
# since restore starts only open records that are not running.
cmd_unlock() {
  local status=0

  if ! command -v ecryptfs-mount-private >/dev/null 2>&1; then
    printf 'ecryptfs-mount-private not found. This command is for the Mint hub.\n' >&2
    exit 1
  fi

  if mount | grep -q "on $HOME type ecryptfs"; then
    printf 'Home is already unlocked.\n'
  else
    printf 'Enter the login passphrase to unlock %s:\n' "$HOME"
    ecryptfs-mount-private
  fi

  "$BASH" "$(launcher_path)" restore || status=$?
  return "$status"
}

# The hook and the tmux client hooks run on every Claude start and every
# attach, including where tmux is missing, so they never fail and never speak.
case "${1:-}" in
  pin)  shift; cmd_pin "$@" 2>/dev/null || true; exit 0 ;;
  seen) shift; cmd_seen "$@" 2>/dev/null || true; exit 0 ;;
esac

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
  ls|list)     cmd_ls "$@" ;;
  mobile)      cmd_mobile "$@" ;;
  close|kill)  cmd_close "$@" ;;
  adopt)       cmd_adopt "$@" ;;
  restore)     cmd_restore "$@" ;;
  history)     cmd_history "$@" ;;
  remote)      cmd_remote "$@" ;;
  unlock)      cmd_unlock ;;
  help|-h|--help) usage ;;
  *)
    printf 'Unknown command: %s\n\n' "$command" >&2
    usage >&2
    exit 1
    ;;
esac
