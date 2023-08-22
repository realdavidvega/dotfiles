# -----------------------------------------------------------------
#
# ░█████╗░░██████╗░░░░░░░██████╗███████╗████████╗██╗░░░██╗██████╗░
# ██╔══██╗██╔════╝░░░░░░██╔════╝██╔════╝╚══██╔══╝██║░░░██║██╔══██╗
# ██║░░██║╚█████╗░█████╗╚█████╗░█████╗░░░░░██║░░░██║░░░██║██████╔╝
# ██║░░██║░╚═══██╗╚════╝░╚═══██╗██╔══╝░░░░░██║░░░██║░░░██║██╔═══╝░
# ╚█████╔╝██████╔╝░░░░░░██████╔╝███████╗░░░██║░░░╚██████╔╝██║░░░░░
# ░╚════╝░╚═════╝░░░░░░░╚═════╝░╚══════╝░░░╚═╝░░░░╚═════╝░╚═╝░░░░░
#
# -----------------------------------------------------------------

# Linux
if [[ "$OSTYPE" == "linux-gnu"* ]]; then

  # Paths
  export HOME="/home/david"
  export JAVA_HOME="/home/linuxbrew/.linuxbrew/opt/openjdk@20"

  OS_DRIVE="/mnt"
  OS_WORKSPACE="${OS_DRIVE}/d/Workspace" 
  BREW_PATH="/home/linuxbrew/.linuxbrew/bin"
  DOCKER_PATH="${OS_DRIVE}/c/Program Files/Rancher Desktop/resources/resources/linux"

  path=(
    "$HOME/bin"
    "$DOTLY_PATH/bin"
    "$DOTFILES_PATH/bin"
    "$DOCKER_PATH/bin"
    "$JAVA_HOME/bin"
    "$GEM_HOME/bin"
    "$GOPATH/bin"
    "$HOME/.cargo/bin"
    "/usr/local/opt/ruby/bin"
    "/usr/local/opt/python/libexec/bin"
    "$BREW_PATH"
    "/usr/local/bin"
    "/usr/local/sbin"
    "/bin"
    "/usr/bin"
    "/usr/sbin"
    "/sbin"
    "$path"
  )

  export path

  # Apps
  alias open="${OS_DRIVE}/c/Windows/SysWOW64/explorer.exe"
  alias sublime="${OS_DRIVE}/c/Program\ Files/Sublime\ Text/sublime_text.exe"
  alias vscode="${OS_DRIVE}/c/Users/david/AppData/Local/Programs/Microsoft\ VS\ Code/Code.exe" 

  # Workaround for slow git on WSL2
  alias git="/mnt/c/Program\ Files/Git/bin/git.exe"
  
  # Workaround for invalid java location
  alias java="$JAVA_HOME/bin/java"

  # Linux brew
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"

  # Custom OS Paths
  alias downloads="cd ${OS_DRIVE}/c/Users/david/Downloads"
  alias dw="downloads"

  # Emacs
  alias emacs="emacs"

  # Symlinks
  # ln -s ~/.dotfiles/git/.gitconfig ~/.gitconfig
  # ln -s ~/.dotfiles/aliases/.docker-aliases ~/.docker-aliases
  # ln -s ~/.dotfiles/aliases/.youtube-dl-aliases ~/.youtube-dl-aliases

# MacOS
elif [[ "$OSTYPE" =~ ^darwin ]]; then

  # Paths
  export HOME="/Users/david"
  export JAVA_HOME="$HOME/Library/Java/JavaVirtualMachines/corretto-17.0.6/Contents/Home"

  OS_DRIVE="/Volumes/Macintosh\ HD" 
  OS_WORKSPACE="~/Workspace" 
  BREW_PATH="/opt/homebrew/bin"
  DOCKER_PATH="~/.rd/bin"
  
  path=(
    "$HOME/bin"
    "$DOTLY_PATH/bin"
    "$DOTFILES_PATH/bin"
    "$DOCKER_PATH/bin"
    "$JAVA_HOME/bin"
    "$GEM_HOME/bin"
    "$GOPATH/bin"
    "$HOME/.cargo/bin"
    "/usr/local/opt/ruby/bin"
    "/usr/local/opt/python/libexec/bin"
    "$BREW_PATH"
    "/usr/local/bin"
    "/usr/local/sbin"
    "/bin"
    "/usr/bin"
    "/usr/sbin"
    "/sbin"
    "$path"
  )

  export path

  # NVM env
  export NVM_DIR="$HOME/.nvm"
  source $(brew --prefix nvm)/nvm.sh

  # Node path
  export NODE_PATH=$NODE_PATH:`npm root -g`

  # Ruby env
  eval "$(rbenv init - zsh)"

  # TheFuck
  eval $(thefuck --alias)

  # Apps
  alias sublime="open ${OS_DRIVE}/Applications/Sublime\ Text.app/Contents/SharedSupport/bin/subl"
  alias vscode="open ${OS_DRIVE}/Applications/Visual\ Studio\ Code.app"  

  # Terminal
  alias ls="exa"
  alias l="exa -l"
  alias ll="exa -la"

  # Daily wallpaper (execute once, node needed)
  # npx --yes bing-wallpaper-daily-mac-multimonitor@latest enable-auto-update

fi

# ------------------------------------------------------------------------------
# Dotly Paths
# ---------------------------------------------------------------
export DOTFILES_PATH="$HOME/.dotfiles"
export DOTLY_PATH="$DOTFILES_PATH/modules/dotly" 

# ------------------------------------------------------------------------------
# Codely theme config
# ------------------------------------------------------------------------------
export CODELY_THEME_MINIMAL=false
export CODELY_THEME_MODE="dark"
export CODELY_THEME_PROMPT_IN_NEW_LINE=false
export CODELY_THEME_PWD_MODE="short" # full, short, home_relative

# ------------------------------------------------------------------------------
# Languages
# ------------------------------------------------------------------------------
export GEM_HOME="$HOME/.gem"
export GOPATH="$HOME/.go"

# ------------------------------------------------------------------------------
# Apps
# ------------------------------------------------------------------------------
if [ "$CODELY_THEME_MODE" = "dark" ]; then
	fzf_colors="pointer:#ebdbb2,bg+:#3c3836,fg:#ebdbb2,fg+:#fbf1c7,hl:#8ec07c,info:#928374,header:#fb4934"
else
	fzf_colors="pointer:#db0f35,bg+:#d6d6d6,fg:#808080,fg+:#363636,hl:#8ec07c,info:#928374,header:#fffee3"
fi

export FZF_DEFAULT_OPTS="--color=$fzf_colors --reverse"

# ---------------------------
#        Custom Paths
# ---------------------------
# Workspace
export WORKSPACE="${OS_WORKSPACE}"

# Zsh
export OH_MY_ZSH="/.oh-my-zsh"
export ZSHRC="~/.zshrc"

# Git
export GITALIAS="~/.gitalias"
export GITCONFIG="~/.gitconfig"

# Repos
export REPOS="${WORKSPACE}/repos"
export GITHUB="${REPOS}/github"
export WORK="${REPOS}/work"

# Work-specific
## main
export XEBIA="${WORK}/xebia"
export CORTEX="${WORK}/cortex"

## tools
export TOOLS="${GITHUB}/tools"
export DOTFILES="${DOTFILES_PATH}"

## rust
export RUST="${GITHUB}/rust"

## web
export WEB="${GITHUB}/web"

# SDKMAN
source "$HOME/.sdkman/bin/sdkman-init.sh"
