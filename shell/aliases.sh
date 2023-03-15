# ---------------------------
#       Dotly aliases
# ---------------------------

# Enable aliases to be sudo’ed
alias sudo='sudo '

alias ..="cd .."
alias ...="cd ../.."
alias ll="ls -l"
alias la="ls -la"
alias ~="cd ~"
alias dotfiles='cd $DOTFILES_PATH'

# Git
alias gaa="git add -A"
alias gc='$DOTLY_PATH/bin/dot git commit'
alias gca="git add --all && git commit --amend --no-edit"
alias gco="git checkout"
alias gd='$DOTLY_PATH/bin/dot git pretty-diff'
alias gs="git status -sb"
alias gf="git fetch --all -p"
alias gps="git push"
alias gpsf="git push --force"
alias gpl="git pull --rebase --autostash"
alias gb="git branch"
alias gl='$DOTLY_PATH/bin/dot git pretty-log'

# Utils
alias k='kill -9'
alias i.='(idea $PWD &>/dev/null &)'
alias c.='(code $PWD &>/dev/null &)'
alias o.='open .'
alias up='dot package update_all'

# ---------------------------
#       App aliases
# ---------------------------

# Zsh
alias zshcfg="vim ${ZSHRC}"
alias exportscfg="vim ${DOTFILES}/shell/exports.sh"
alias oscfg="vim ${DOTFILES}/shell/os.sh"
alias aliasescfg="vim ${DOTFILES}/shell/aliases.sh"

# Git
alias gitalias="vim ${GITALIAS}"
alias gitconfig="vim ${GITCONFIG}"

# System
alias clr="clear"
alias c="clear"
alias op="open"
alias o="open"
alias vi="vim"
alias v="vim"
alias g="git"
alias copy='pbcopy'

# docker aliases
source ~/.docker-aliases

# youtube-dl aliases
source ~/.youtube-dl-aliases

# Apps
alias subl="sublime"
alias vsc="vscode"

# Fuck aliases
alias fu="fuck"
alias FUCK="fuck"

# ---------------------------
#      Directory aliases
# ---------------------------

# Workspace
alias ws="cd ${WORKSPACE}"

# Repos
alias repos="cd ${REPOS}"
alias github="cd ${GITHUB}"
alias work="cd ${WORK}"

# Work-specific
## @47deg
alias 47deg="cd ${FOURTYSEVEN}"

# Tools repos
alias tools="cd ${TOOLS}"

# Rust repos
alias rust="cd ${RUST}"

# Web repos
alias web="cd ${WEB}"