# Enable Powerlevel10k instant prompt. Should stay close to the top of ~/.zshrc.
# Initialization code that may require console input (password prompts, [y/n]
# confirmations, etc.) must go above this block; everything else may go below.
if [[ -r "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh" ]]; then
  source "${XDG_CACHE_HOME:-$HOME/.cache}/p10k-instant-prompt-${(%):-%n}.zsh"
fi

# If you come from bash you might have to change your $PATH.
# export PATH=$HOME/bin:/usr/local/bin:$PATH

# Path to your oh-my-zsh installation.
export ZSH="$HOME/.oh-my-zsh"

# Set name of the theme to load --- if set to "random", it will
# load a random theme each time oh-my-zsh is loaded, in which case,
# to know which specific one was loaded, run: echo $RANDOM_THEME
# See https://github.com/ohmyzsh/ohmyzsh/wiki/Themes
ZSH_THEME="powerlevel10k/powerlevel10k"

# Set list of themes to pick from when loading at random
# Setting this variable when ZSH_THEME=random will cause zsh to load
# a theme from this variable instead of looking in $ZSH/themes/
# If set to an empty array, this variable will have no effect.
# ZSH_THEME_RANDOM_CANDIDATES=( "robbyrussell" "agnoster" )

# Uncomment the following line to use case-sensitive completion.
# CASE_SENSITIVE="true"

# Uncomment the following line to use hyphen-insensitive completion.
# Case-sensitive completion must be off. _ and - will be interchangeable.
# HYPHEN_INSENSITIVE="true"

# Uncomment one of the following lines to change the auto-update behavior
# zstyle ':omz:update' mode disabled  # disable automatic updates
# zstyle ':omz:update' mode auto      # update automatically without asking
# zstyle ':omz:update' mode reminder  # just remind me to update when it's time

# Uncomment the following line to change how often to auto-update (in days).
# zstyle ':omz:update' frequency 13

# Uncomment the following line if pasting URLs and other text is messed up.
# DISABLE_MAGIC_FUNCTIONS="true"

# Uncomment the following line to disable colors in ls.
# DISABLE_LS_COLORS="true"

# Uncomment the following line to disable auto-setting terminal title.
# DISABLE_AUTO_TITLE="true"

# Uncomment the following line to enable command auto-correction.
# ENABLE_CORRECTION="true"

# Uncomment the following line to display red dots whilst waiting for completion.
# You can also set it to another string to have that shown instead of the default red dots.
# e.g. COMPLETION_WAITING_DOTS="%F{yellow}waiting...%f"
# Caution: this setting can cause issues with multiline prompts in zsh < 5.7.1 (see #5765)
# COMPLETION_WAITING_DOTS="true"

# Uncomment the following line if you want to disable marking untracked files
# under VCS as dirty. This makes repository status check for large repositories
# much, much faster.
# DISABLE_UNTRACKED_FILES_DIRTY="true"

# Uncomment the following line if you want to change the command execution time
# stamp shown in the history command output.
# You can set one of the optional three formats:
# "mm/dd/yyyy"|"dd.mm.yyyy"|"yyyy-mm-dd"
# or set a custom format using the strftime function format specifications,
# see 'man strftime' for details.
# HIST_STAMPS="mm/dd/yyyy"

# Would you like to use another custom folder than $ZSH/custom?
# ZSH_CUSTOM=/path/to/new-custom-folder

# Which plugins would you like to load?
# Standard plugins can be found in $ZSH/plugins/
# Custom plugins may be added to $ZSH_CUSTOM/plugins/
# Example format: plugins=(rails git textmate ruby lighthouse)
# Add wisely, as too many plugins slow down shell startup.
plugins=(git zsh-cargo-completion)

source $ZSH/oh-my-zsh.sh

# User configuration

# export MANPATH="/usr/local/man:$MANPATH"

# You may need to manually set your language environment
# export LANG=en_US.UTF-8

# Compilation flags
# export ARCHFLAGS="-arch x86_64"

# Set personal aliases, overriding those provided by oh-my-zsh libs,
# plugins, and themes. Aliases can be placed here, though oh-my-zsh
# users are encouraged to define aliases within the ZSH_CUSTOM folder.
# For a full list of active aliases, run `alias`.

# To customize prompt, run `p10k configure` or edit ~/.p10k.zsh.
[[ ! -f ~/.p10k.zsh ]] || source ~/.p10k.zsh

# ---------------------------
#      Development config
# ---------------------------

# Node Version Manager (NVM)
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"  # This loads nvm
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"  # This loads nvm bash_completion

# Rust Shell
source $HOME/.cargo/env

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
  OS_DRIVE="/mnt"
  OS_WORKSPACE="${OS_DRIVE}/d/Workspace" 

  # Apps
  alias o="explorer.exe"
  alias su="${OS_DRIVE}/c/Program\ Files/Sublime\ Text/sublime_text.exe"
  alias code="${OS_DRIVE}/c/Users/david/AppData/Local/Programs/Microsoft\ VS\ Code/Code.exe" 

# MacOS
elif [[ "$OSTYPE" == "darwin21.0"* ]]; then

  # Paths
  OS_DRIVE="/Volumes/Macintosh\ HD" 
  OS_WORKSPACE="~/Workspace" 

  # Apps
  alias o="open"
  alias su="open ${OS_DRIVE}/Applications/Sublime\ Text.app"
  alias code="open ${OS_DRIVE}/Applications/Visual\ Studio\ Code.app"  

# Cygwin
elif [[ "$OSTYPE" == "cygwin"* ]]; then

  # Paths
  OS_DRIVE="/cygdrive" 
  OS_WORKSPACE="${OS_DRIVE}/d/Workspace" 

  # Apps
  alias o="explorer"
  alias su="${OS_DRIVE}/c/Program\ Files/Sublime\ Text/sublime_text.exe"
  alias code="${OS_DRIVE}/c/Users/david/AppData/Local/Programs/Microsoft\ VS\ Code/Code.exe"  

fi

# --------------------------------------------------------
#
# ░█████╗░░█████╗░███╗░░░███╗███╗░░░███╗░█████╗░███╗░░██╗
# ██╔══██╗██╔══██╗████╗░████║████╗░████║██╔══██╗████╗░██║
# ██║░░╚═╝██║░░██║██╔████╔██║██╔████╔██║██║░░██║██╔██╗██║
# ██║░░██╗██║░░██║██║╚██╔╝██║██║╚██╔╝██║██║░░██║██║╚████║
# ╚█████╔╝╚█████╔╝██║░╚═╝░██║██║░╚═╝░██║╚█████╔╝██║░╚███║
# ░╚════╝░░╚════╝░╚═╝░░░░░╚═╝╚═╝░░░░░╚═╝░╚════╝░╚═╝░░╚══╝
#
# --------------------------------------------------------

# ---------------------------
#       Basic setup
# ---------------------------

# Preferred editor for local and remote sessions
if [[ -n $SSH_CONNECTION ]]; then
  export EDITOR='vim'
else
  export EDITOR='mvim'
fi

# ---------------------------
#           Paths
# ---------------------------

# Workspace
export WS_WORKSPACE="${OS_WORKSPACE}"

# Repos
<<<<<<< HEAD
export REPOS="${WORKSPACE}/repos"
export GITHUB="${REPOS}/github"
export TOOLS="${GITHUB}/tools"
export RUST="${GITHUB}/rust"
export DOTFILES="${TOOLS}/dotfiles"
=======
export WS_REPOS="${WS_WORKSPACE}/repos"
export WS_GITHUB="${WS_REPOS}/github"

# Tools Repos
export WS_TOOLS="${WS_GITHUB}/tools"
export WS_DOTFILES="${WS_TOOLS}/dotfiles"

# Rust Repos
export WS_RUST="${WS_GITHUB}/rust"

# Web Repos
export WS_WEB="${WS_GITHUB}/web"
>>>>>>> feat: update directory structure

# ---------------------------
#       App aliases
# ---------------------------

# Zsh
alias ohmyzsh="vim ~/.oh-my-zsh"
alias zshcfg="vim ~/.zshrc"

# Git
alias gitalias="vim ~/.gitalias"
alias gitconfig="vim ~/.gitconfig"

# System
alias v="vim"
alias g="git"
alias c="clear"

# Dotfiles repo
alias zsh_to_home="cp ${DOTFILES}/common/.zshrc ~/.zshrc"
alias zsh_to_dotfiles="cp ~/.zshrc ${DOTFILES}/common/.zshrc"

# ---------------------------
#      Directory aliases
# ---------------------------

# Workspace
alias ws="cd ${WS_WORKSPACE}"

# Repos
<<<<<<< HEAD
alias repos="cd ${REPOS}"
alias github="cd ${GITHUB}"
alias tools="cd ${TOOLS}"
alias rust="cd ${RUST}"
alias dotfiles="cd ${DOTFILES}"

=======
alias repos="cd ${WS_REPOS}"
alias github="cd ${WS_GITHUB}"

# Tools Repos
alias tools="cd ${WS_TOOLS}"
alias dotfiles="cd ${WS_DOTFILES}"

# Rust Repos
alias rust="cd ${WS_RUST}"

# Web Repos
alias web="cd ${WS_WEB}"
>>>>>>> feat: update directory structure
