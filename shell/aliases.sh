# ---------------------------
#       Dotly Aliases
# ---------------------------

# Enable aliases to be sudo’ed
alias sudo="sudo "

# Navigating
alias ..="cd .."
alias ...="cd ../.."
alias ....="cd ../../.."
alias .....="cd ../../../.."
alias ......="cd ../../../../.."
alias .......="cd ../../../../../.."
alias ........="cd ../../../../../../.."
alias ll="ls -l"
alias la="ls -la"
alias ~="cd ~"

# Directories
alias dotfiles="cd $DOTFILES_PATH"
alias secrets="cd $DOTFILES_PATH/secrets"

# Git
alias gaa="git add -A"
alias gc="$DOTLY_PATH/bin/dot git commit"
alias gca="git add --all && git commit --amend --no-edit"
alias gco="git checkout"
alias gd="$DOTLY_PATH/bin/dot git pretty-diff"
alias gs="git status -sb"
alias gf="git fetch --all -p"
alias gps="git push"
alias gpsf="git push --force"
alias gpl="git pull --rebase --autostash"
alias gb="git branch"
alias gl="$DOTLY_PATH/bin/dot git pretty-log"

# Utils
alias k="kill -9"
alias i.="(idea $PWD &>/dev/null &)"
alias c.="(code $PWD &>/dev/null &)"
alias o.="open ."
alias up="dot package update_all"

# ---------------------------
#       App Aliases
# ---------------------------

# Zsh / cfg
alias zshcfg="vim $ZSHRC"
alias exportscfg="vim $DOTFILES/shell/exports.sh"
alias oscfg="vim $DOTFILES/shell/os.sh"
alias aliascfg="vim $DOTFILES/shell/aliases.sh"
alias ytcfg="vim $DOTFILES/aliases/.youtube-dl-aliases"
alias dockercfg="vim $DOTFILES/aliases/.docker-aliases"

# Git
alias gitalias="vim $GITALIAS"
alias gitconfig="vim $GITCONFIG"

# System
alias clr="clear"
alias c="clr"
alias op="open"
alias o="op"
alias vi="vim"
alias v="vi"
alias g="git"
alias copy="pbcopy"

# Python
alias pip="pip3"
alias python="python3"

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

# Gradle aliases
alias gradlew="./gradlew"
alias gr="gradlew"
alias grb="gr build"
alias grt="gr test --parallel"
alias grc="gr spotlessCheck --parallel"
alias grs="gr spotlessApply --parallel"
alias grpl="gr publishToMavenLocal"

# Node aliases
alias nd="node"
alias ndv="node -v"

# NPM aliases
alias np="npm"
alias npv="npm -v"
alias npi="npm install"
alias nps="npm run start"

# Yarn aliases
alias y="yarn"
alias yv="yarn -v"
alias yi="yarn install"
alias ys="yarn start"

# NVM aliases
alias nv="nvm"
alias nvl="nvm list"
alias nvu="nvm use"

# Build aliases
alias m="make"

# ---------------------------
#      Directory Aliases
# ---------------------------

# Home
alias root="cd /"
alias home="cd $HOME"
alias downloads="cd $DOWNLOADS"
alias dw="downloads"

# Workspace
alias workspace="ws"

# Repos
alias wsre="ws repos"
alias wsgi="ws github"
alias wswo="ws work"

# Work-specific
## @xebia-functional
alias wsxeb="ws xebia"
alias wsco="ws cortex"

## projects
alias wsxef="ws xef"

# Tools repos
alias wsto="ws tools"

# Tech repos
alias wsru="ws rust"
alias wswe="ws web"
alias wsph="ws php"
alias wsja="ws java"
alias wsko="ws kotlin"
alias wssc="ws scala"
alias wspy="ws python"
