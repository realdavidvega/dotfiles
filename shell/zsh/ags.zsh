# Agent sessions for a zsh whose own rc is not this repository's, such as a Mac
# that runs ags without the full install. The rc sources this file through the
# line 51-agent-sessions.sh appends to it. See doc/remote-agent-sessions.md.
#
# Sourcing prints nothing, since an IDE reads the environment through
# `zsh -i -l -c` and p10k's instant prompt captures early output.

# Work roots belong in the rc, above the line that sources this file. Exported
# either way, so a plain assignment there still reaches the launcher.
: "${AGENT_SESSION_REPOS:=$HOME/Workspace/github}"
export AGENT_SESSION_REPOS

[[ -r "${DOTFILES_PATH:-$HOME/.dotfiles}/aliases/.tmux-aliases" ]] \
  && source "${DOTFILES_PATH:-$HOME/.dotfiles}/aliases/.tmux-aliases"

# After a reboot, the first prompt of a terminal tab says when recorded
# sessions are not running. A precmd hook never runs in a `-c` shell, and the
# bundle id and JetBrains' variable keep out an IDE that inherited
# TERM_PROGRAM, so a terminal it restores at login never takes the hint.
_ags_hint() {
  add-zsh-hook -d precmd _ags_hint
  _ags_hint_due 2>/dev/null || return 0
  _ags_hint_text="$("${DOTFILES_PATH:-$HOME/.dotfiles}/scripts/agent-session.sh" restore --hint 2>/dev/null)"
  [[ -n "$_ags_hint_text" ]] || { unset _ags_hint_text; return 0; }
  # p10k's first precmd queues its own sched +0 to hand the terminal back, and
  # events due together run in the order they were queued, so this prints after.
  if zmodload zsh/sched 2>/dev/null; then
    sched +0 _ags_hint_print
  else
    _ags_hint_print
  fi
}

# Whether restore --hint has anything to do: a recorded session stopped with no
# entry from this boot, an entry made in the last 10 s, so every tab a reboot
# reopens together prints, or an entry the launcher would remove. Each process
# costs a loaded Mac several ms at every tab's first prompt, so this reads the
# state with zsh alone and starts two: sysctl for the boot and tmux for the
# running names. The launcher owns the rules, including expiry, whose sessions
# it marks with a file entry this treats as already judged until the record is
# used again. The modules it loads stay loaded in the shell.
_ags_hint_due() {
  emulate -L zsh
  local state hint id name entry pid line fd deadline ended=0
  integer left
  local -a running fresh reply
  if [[ -n "${AGENT_SESSION_STATE:-}" ]]; then
    state=$AGENT_SESSION_STATE
  elif [[ -d /srv/services/agents ]]; then
    state=/srv/services/agents/state
  else
    state=${XDG_STATE_HOME:-$HOME/.local/state}/ags
  fi
  [[ -d $state ]] || return 1
  if [[ -n "${AGENT_SESSION_BOOT_ID:-}" ]]; then
    id=$AGENT_SESSION_BOOT_ID
  elif [[ $OSTYPE == darwin* ]]; then
    id=$(sysctl -n kern.bootsessionuuid 2>/dev/null)
  elif [[ -r /proc/sys/kernel/random/boot_id ]]; then
    IFS= read -r id < /proc/sys/kernel/random/boot_id
  fi
  [[ -n $id ]] || return 1
  hint=$state/hint/$id
  fresh=($hint/*(N/ms-10))
  (( $#fresh )) && return 0

  # A wedged server gets one second in all, as the launcher's listing does.
  # Process substitution sets no $!, so the child names itself first.
  if (( $+commands[tmux] )); then
    zmodload zsh/zselect zsh/system zsh/datetime 2>/dev/null || return 0
    deadline=$(( EPOCHREALTIME + 1 ))
    exec {fd}< <(print -r -- $sysparams[pid]; exec tmux list-sessions -F '#{session_name}' 2>/dev/null)
    {
      while (( left = (deadline - EPOCHREALTIME) * 100, left > 0 )) && zselect -t $left -r $fd; do
        IFS= read -r -u $fd line || { ended=1; break; }
        if [[ -z $pid ]]; then pid=$line; else running+=($line); fi
      done
    } always {
      [[ -n $pid ]] || ! zselect -t 0 -r $fd || IFS= read -r -u $fd pid
      exec {fd}<&-
      (( ended )) || { [[ $pid == <1-> ]] && kill $pid 2>/dev/null }
    }
    (( ended )) || return 1
  fi
  # Another tab's launcher may have made an entry during the listing.
  fresh=($hint/*(N/ms-10))
  (( $#fresh )) && return 0

  # A file entry is a record this boot left out. Use since then means the
  # launcher would count it now.
  for entry in $hint/*(N); do
    name=${entry:t}
    (( ${running[(Ie)$name]} )) && return 0
    [[ -f $state/sessions/$name/dir && ! -e $state/sessions/$name/closed ]] || return 0
    [[ -d $entry || ! $state/sessions/$name/seen -nt $entry ]] || return 0
  done
  for entry in $state/sessions/*(N/); do
    name=${entry:t}
    [[ -f $entry/dir && ! -e $entry/closed ]] || continue
    (( ${running[(Ie)$name]} )) && continue
    [[ -e $hint/$name ]] || return 0
  done
  return 1
}

_ags_hint_print() {
  print -r -- "$_ags_hint_text"
  unset _ags_hint_text
}

if [[ -o interactive && -z "${TMUX:-}" ]] \
  && [[ "${TERM_PROGRAM:-}" == (iTerm.app|Apple_Terminal) ]] \
  && [[ "${__CFBundleIdentifier:-}" == (com.googlecode.iterm2|com.apple.Terminal) ]] \
  && [[ "${TERMINAL_EMULATOR:-}" != JetBrains* ]]; then
  autoload -Uz add-zsh-hook
  add-zsh-hook precmd _ags_hint
fi
