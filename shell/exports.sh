# ---------------------------
#       OS Exports
# ---------------------------

# Linux
if [[ "$OSTYPE" == "linux-gnu"* ]]; then

  # Paths
  export HOME="/home/david"

  # No longer maintained, use SDKMAN instead (see sdk list java)
  # export JAVA_HOME="/home/linuxbrew/.linuxbrew/opt/openjdk@20"

  OS_DRIVE="/mnt"
  OS_WORKSPACE="$OS_DRIVE/d/Workspace"
  C_DRIVE="$OS_DRIVE/c"
  
  BREW_PATH="/home/linuxbrew/.linuxbrew/bin"

  # No longer maintained, use DOCKER/PODMAN instead
  # DOCKER_PATH="$OS_DRIVE/c/Program Files/Rancher Desktop/resources/resources/linux"

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

  # Exports
  export WIN_HOME="$C_DRIVE/Users/david"
  export DOWNLOADS="$C_DRIVE/Users/david/Downloads"

  # Apps
  alias open="$C_DRIVE/Windows/SysWOW64/explorer.exe"
  alias sublime="$C_DRIVE/Program\ Files/Sublime\ Text/sublime_text.exe"
  alias vscode="$C_DRIVE/Users/david/AppData/Local/Programs/Microsoft\ VS\ Code/Code.exe"

  # Workaround for slow git on WSL2
  alias git="$C_DRIVE/Program\ Files/Git/bin/git.exe"

  # Use git configuration from dotfiles with windows git
  cp ~/.dotfiles/git/.gitconfig $WIN_HOME/.gitconfig
  cp ~/.dotfiles/git/.gitalias $WIN_HOME/.gitalias
  cp ~/.dotfiles/git/.gitignore $WIN_HOME/.gitignore
  cp ~/.dotfiles/git/.gitkeep $WIN_HOME/.gitkeep
  
  # Workaround for invalid java location (No longer maintained, use SDKMAN instead)
  # alias java="$JAVA_HOME/bin/java"

  # Linux brew
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"

  # Emacs
  alias emacs="emacs"

# MacOS
elif [[ "$OSTYPE" =~ ^darwin ]]; then

  # Paths
  export HOME="/Users/david"

  # No longer maintained, use SDKMAN instead (see sdk list java)
  # export JAVA_HOME="$HOME/Library/Java/JavaVirtualMachines/corretto-17.0.6/Contents/Home"

  OS_DRIVE="/Volumes/Macintosh\ HD"
  OS_WORKSPACE="$HOME/Workspace"
  BREW_PATH="/opt/homebrew/bin"

  # No longer maintained, use DOCKER/PODMAN instead
  # DOCKER_PATH="~/.rd/bin"
  
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
  alias vscode="open $OS_DRIVE/Applications/Visual\ Studio\ Code.app"
  alias intellij="open -a $HOME/Applications/IntelliJ\ IDEA\ Ultimate.app"
  alias webstorm="open -a $HOME/Applications/WebStorm.app"
  alias rustrover="open -a $HOME/Applications/RustRover.app"

  # Shorter Apps
  alias ij=intellij
  alias wsm=webstorm

  # Terminal
  alias ls="exa"
  alias l="exa -l"
  alias ll="exa -la"
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

# Zsh
export OH_MY_ZSH="/.oh-my-zsh"
export ZSHRC="~/.zshrc"

# Git
export GITALIAS="~/.gitalias"
export GITCONFIG="~/.gitconfig"

# Repos
export REPOS="$WORKSPACE/repos"
export GITHUB="$REPOS/github"
export EXTERNAL="$REPOS/external"
export WORK="$REPOS/work"

# Work-specific
## main
export XEBIA="$WORK/xebia"
export CORTEX="$WORK/cortex"

## tools
export TOOLS="$GITHUB/tools"
export DOTFILES="$DOTFILES_PATH"

## tech
export RUST="$GITHUB/rust"
export WEB="$GITHUB/web"
export PHP="$GITHUB/php"
export JAVA="$GITHUB/java"
export KOTLIN="$GITHUB/kotlin"
export SCALA="$GITHUB/scala"
export PYTHON="$GITHUB/python"

# SDKMAN
sourceif "$HOME/.sdkman/bin/sdkman-init.sh"
