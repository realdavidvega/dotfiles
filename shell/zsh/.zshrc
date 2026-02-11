# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# Uncomment for debuf with `zprof`
# zmodload zsh/zprof

# ZSH Ops
setopt HIST_IGNORE_ALL_DUPS
setopt HIST_FCNTL_LOCK
setopt +o nomatch
# setopt autopushd

ZSH_HIGHLIGHT_HIGHLIGHTERS=(main brackets)

# # Start Zim
###########################################
export ZIM_HOME="$DOTFILES_PATH/shell/zsh/.zim"
source "$ZIM_HOME/init.zsh"
###########################################

# Start oh-my-zsh
###########################################
# export OH_MY_ZSH="$DOTFILES_PATH/shell/zsh/.oh-my-zsh"
# export ZSH=$OH_MY_ZSH
# export ZSH_CUSTOM="$OH_MY_ZSH/custom"

# ZSH_THEME="powerlevel10k/powerlevel10k"

# plugins=(
#   git
#   docker
#   zsh-autosuggestions
#   zsh-syntax-highlighting
#   fast-syntax-highlighting
#   zsh-autocomplete
# )

# source $ZSH/oh-my-zsh.sh
###########################################

# Async mode for autocompletion
ZSH_AUTOSUGGEST_USE_ASYNC=true
ZSH_HIGHLIGHT_MAXLENGTH=300
POWERLEVEL9K_INSTANT_PROMPT=verbose

source "$DOTFILES_PATH/shell/init.sh"

fpath=(
    "$DOTFILES_PATH/shell/zsh/themes"
    "$DOTFILES_PATH/shell/zsh/completions"
    "$DOTLY_PATH/shell/zsh/themes"
    "$DOTLY_PATH/shell/zsh/completions"
    "$HOME/.local/wd"
    $fpath
)

autoload -Uz promptinit && promptinit
# prompt ${DOTLY_THEME:-codely}

source "$DOTLY_PATH/shell/zsh/bindings/dot.zsh"
source "$DOTLY_PATH/shell/zsh/bindings/reverse_search.zsh"
source "$DOTFILES_PATH/shell/zsh/key-bindings.zsh"

# Rust Shell
source $HOME/.cargo/env

# Python certs
export REQUESTS_CA_BUNDLE=$DOTFILES_SECRETS/certs/requests-ca-bundle.pem

# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f $HOME/.p10k.zsh ]] || source $HOME/.p10k.zsh

export SDKMAN_DIR="$HOME/.sdkman"
[[ -s "$HOME/.sdkman/bin/sdkman-init.sh" ]] && source "$HOME/.sdkman/bin/sdkman-init.sh"

# The next line updates PATH for the Google Cloud SDK.
if [ -f "$HOME/.local/google-cloud-sdk/path.zsh.inc" ]; then . "$HOME/.local/google-cloud-sdk/path.zsh.inc"; fi

# The next line enables shell command completion for gcloud.
if [ -f "$HOME/.local/google-cloud-sdk/completion.zsh.inc" ]; then . "$HOME/.local/google-cloud-sdk/completion.zsh.inc"; fi

[[ -f "$HOME/.local/bin/env" ]] && . "$HOME/.local/bin/env"

# >>> conda initialize >>>
# Only initialize if conda exists in WSL
if [ -d "$HOME/miniconda3" ]; then
    if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
        . "$HOME/miniconda3/etc/profile.d/conda.sh"
    fi
fi
# <<< conda initialize <<<

[ -f ~/.fzf.zsh ] && source ~/.fzf.zsh

# bun completions (compinit already loaded by zim)
if [ -s "/home/david/.bun/_bun" ]; then
    # Mock compinit to prevent bun from calling it
    compinit() { : }
    source "/home/david/.bun/_bun"
    unfunction compinit
fi

# Auto-activate Python virtual environments
# Priority: .venv-wsl (WSL-specific) > .venv > venv
python_venv() {
  local dir="$PWD"
  local venv_path=""

  # Search for venv in current and parent directories
  while [[ "$dir" != "/" ]]; do
    if [[ -d "$dir/.venv-wsl" && -f "$dir/.venv-wsl/bin/activate" ]]; then
      venv_path="$dir/.venv-wsl"
      break
    elif [[ -d "$dir/.venv" && -f "$dir/.venv/bin/activate" ]]; then
      venv_path="$dir/.venv"
      break
    elif [[ -d "$dir/venv" && -f "$dir/venv/bin/activate" ]]; then
      venv_path="$dir/venv"
      break
    fi
    dir="$(dirname "$dir")"
  done

  # Activate if found and not already active
  if [[ -n "$venv_path" ]]; then
    if [[ "${VIRTUAL_ENV:-}" != "$venv_path" ]]; then
      [[ -n "${VIRTUAL_ENV:-}" ]] && deactivate 2>/dev/null
      source "$venv_path/bin/activate"
    fi
  else
    # Deactivate if no venv found and one is active
    [[ -n "${VIRTUAL_ENV:-}" ]] && deactivate 2>/dev/null
  fi
}

# Hook to run on directory change and on shell initialization
autoload -U add-zsh-hook
add-zsh-hook chpwd python_venv
python_venv
