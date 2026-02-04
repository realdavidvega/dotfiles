# ---------------------------
#       OS Exports
# ---------------------------

# Linux
if [[ "$OSTYPE" == "linux-gnu"* ]]; then

  # No longer maintained, use SDKMAN instead (see sdk list java)
  # export JAVA_HOME="/home/linuxbrew/.linuxbrew/opt/openjdk@20"

  OS_DRIVE="/mnt"
  C_DRIVE="$OS_DRIVE/c"
  OS_WORKSPACE="$C_DRIVE/Workspace"

  BREW_PATH="/home/linuxbrew/.linuxbrew/bin"

  # No longer maintained, use DOCKER/PODMAN instead
  # DOCKER_PATH="$OS_DRIVE/c/Program Files/Rancher Desktop/resources/resources/linux"

  # bun path
  export BUN_INSTALL="$HOME/.bun"

  # Opencode path
  export OPENCODE_PATH=$HOME/.opencode

  path=(
    "$HOME/bin"
    "$DOTLY_PATH/bin"
    "$DOTFILES_PATH/bin"
    "$DOCKER_PATH/bin"
    "$BUN_INSTALL/bin"
    "$OPENCODE_PATH/bin"
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

  # Exports
  export WIN_HOME="$C_DRIVE/Users/david"
  export DOWNLOADS="$C_DRIVE/Users/david/Downloads"
  
  # Apps
  alias open="$C_DRIVE/Windows/SysWOW64/explorer.exe"
  alias sublime="$C_DRIVE/Program\ Files/Sublime\ Text/sublime_text.exe"
  alias code="$C_DRIVE/Users/david/AppData/Local/Programs/Microsoft\ VS\ Code/Code.exe"

  # Powershell
  alias pshcfg="vim $DOTFILES_PATH/shell/posh/Microsoft.PowerShell_profile.ps1"

  # Smart git wrapper - use Linux git for native paths, Windows git for /mnt/c
  git() {
    local current_path=$(pwd)
    
    # Use Linux git for native Linux filesystem (needed for git-crypt)
    if [[ "$current_path" == /home/* ]] || [[ "$current_path" == /root/* ]] || [[ "$current_path" == $HOME* ]]; then
        command /usr/bin/git "$@"
    else
        # Use Windows git for /mnt/c paths
        /mnt/c/Program\ Files/Git/bin/git.exe "$@"
    fi
  }

  # Use git configuration from dotfiles with windows git
  cp $DOTFILES_PATH/git/.gitconfig $WIN_HOME/.gitconfig
  cp $DOTFILES_PATH/git/.gitalias $WIN_HOME/.gitalias
  cp $DOTFILES_PATH/git/.gitignore $WIN_HOME/.gitignore
  cp $DOTFILES_PATH/git/.gitkeep $WIN_HOME/.gitkeep

  # Nvm env
  export NVM_DIR="$HOME/.nvm"
  [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
  [ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

  # Workaround for invalid java location (No longer maintained, use SDKMAN instead)
  # alias java="$JAVA_HOME/bin/java"

  # Linux brew
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"

  # Emacs
  alias emacs="emacs"

# MacOS
elif [[ "$OSTYPE" =~ ^darwin ]]; then

  # No longer maintained, use SDKMAN instead (see sdk list java)
  # export JAVA_HOME="$HOME/Library/Java/JavaVirtualMachines/corretto-17.0.6/Contents/Home"

  OS_DRIVE="/Volumes/Macintosh\ HD"
  OS_WORKSPACE="$HOME/Workspace"
  BREW_PATH="/opt/homebrew/bin"

  # No longer maintained, use DOCKER/PODMAN instead
  # DOCKER_PATH="~/.rd/bin"

  # bun path
  export BUN_INSTALL="$HOME/.bun"

  # Opencode path
  export OPENCODE_PATH=$HOME/.opencode

  path=(
    "$HOME/bin"
    "$DOTLY_PATH/bin"
    "$DOTFILES_PATH/bin"
    "$BUN_INSTALL/bin"
    "$OPENCODE_PATH/bin"
    "$DOCKER_PATH/bin"
    "$JAVA_HOME/bin"
    "$GEM_HOME/bin"
    "$GOPATH/bin"
    "$HOME/.cargo/bin"
    "$HOME/Library/Application Support/Coursier/bin"
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

  # Exports
  export DOWNLOADS="$HOME/downloads"

  # NVM env
  export NVM_DIR="$HOME/.nvm"
  sourceif $(brew --prefix nvm)/nvm.sh

  # Node path
  export NODE_PATH=$NODE_PATH:`npm root -g`

  # Ruby env
  eval "$(rbenv init - zsh)"

  # Docker aliases
  export DOCKER_ALIAS="docker"

  # TheFuck
  eval $(thefuck --alias)

  # Rancher Symlink Fix (if no docker-compose plugin is used)
  # lnsif ~/.rd/bin/docker-compose ~/.docker/cli-plugins/docker-compose

  # Apps (also as workaround for loading env variables from zsh / dotly)
  alias sublime="open $OS_DRIVE/Applications/Sublime\ Text.app/Contents/SharedSupport/bin/subl"
  alias code="open $OS_DRIVE/Applications/Visual\ Studio\ Code.app"
  alias intellij="open -a $HOME/Applications/IntelliJ\ IDEA\ Ultimate.app"
  alias webstorm="open -a $HOME/Applications/WebStorm.app"
  alias rustrover="open -a $HOME/Applications/RustRover.app"

  # Shorter Apps
  alias ij=intellij
  alias wsm=webstorm

  # Terminal
  # alias ls="exa"
  # alias l="exa -l"
  # alias ll="exa -la"
  alias psa="ps aux"

  # Daily wallpaper (execute once, node needed)
  # npx --yes bing-wallpaper-daily-mac-multimonitor@latest enable-auto-update
fi

# ---------------------------
#        Dotly Exports
# ---------------------------

# Paths
export DOTFILES_PATH="$HOME/.dotfiles"
export DOTLY_PATH="$DOTFILES_PATH/modules/dotly"

# Theme config
export CODELY_THEME_MINIMAL=false
export CODELY_THEME_MODE="dark"
export CODELY_THEME_PROMPT_IN_NEW_LINE=false
export CODELY_THEME_PWD_MODE="short" # full, short, home_relative

# Languages
export GEM_HOME="$HOME/.gem"
export GOPATH="$HOME/.go"

# Apps
if [ "$CODELY_THEME_MODE" = "dark" ]; then
	fzf_colors="pointer:#ebdbb2,bg+:#3c3836,fg:#ebdbb2,fg+:#fbf1c7,hl:#8ec07c,info:#928374,header:#fb4934"
else
	fzf_colors="pointer:#db0f35,bg+:#d6d6d6,fg:#808080,fg+:#363636,hl:#8ec07c,info:#928374,header:#fffee3"
fi

export FZF_DEFAULT_OPTS="--color=$fzf_colors --reverse"

# ---------------------------
#        Custom Exports
# ---------------------------

# Workspace
export WORKSPACE="$OS_WORKSPACE"

# Dotfiles
export DOTFILES_AI="$DOTFILES_PATH/ai"
export DOTFILES_SECRETS="$DOTFILES_PATH/secrets"
export DOTFILES_CONFIG="$DOTFILES_PATH/config"

# Zsh
export ZSHRC="$HOME/.zshrc"

# Git
export GITALIAS="$HOME/.gitalias"
export GITCONFIG="$HOME/.gitconfig"

# Repos
export REPOS="$WORKSPACE/repos"
export GITHUB_REPOS="$REPOS/github"
export EXTERNAL="$REPOS/external"
export WORK="$REPOS/work"

# Work-specific
## main
export XEBIA="$WORK/xebia"
export CORTEX="$WORK/cortex"

## tools
export TOOLS="$GITHUB_REPOS/tools"
export DOTFILES="$DOTFILES_PATH"

## tech
export RUST="$GITHUB_REPOS/rust"
export WEB="$GITHUB_REPOS/web"
export PHP="$GITHUB_REPOS/php"
export JAVA="$GITHUB_REPOS/java"
export KOTLIN="$GITHUB_REPOS/kotlin"
export SCALA="$GITHUB_REPOS/scala"
export PYTHON="$GITHUB_REPOS/python"

# SDKMAN
sourceif "$HOME/.sdkman/bin/sdkman-init.sh"
