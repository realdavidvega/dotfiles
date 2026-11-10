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
