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

# Dotfiles
alias .f="cd $DOTFILES_PATH"
alias dotfiles=".f"

alias ai="cd $DOTFILES_AI"
alias secrets="cd $DOTFILES_SECRETS"

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

# System
alias clr="clear"
alias c="clr"
alias op="open"
alias o="op"
alias v="vim"
alias vi="vim"
alias g="git"
alias copy="pbcopy"

# Git
alias gitalias="vim $GITALIAS"
alias gitconfig="vim $GITCONFIG"

# Python
alias pip="pip3"
alias python="python3"

# Zsh / cfg
alias zshcfg="vim $ZSHRC"
alias exportscfg="vim $DOTFILES/shell/exports.sh"
alias oscfg="vim $DOTFILES/shell/os.sh"
alias aliascfg="vim $DOTFILES/shell/aliases.sh"
alias ytcfg="vim $DOTFILES/aliases/.youtube-dl-aliases"
alias dockercfg="vim $DOTFILES/aliases/.docker-aliases"

# docker aliases
source ~/.docker-aliases

# youtube-dl aliases
source ~/.youtube-dl-aliases

# Apps
alias subl="sublime"
alias vsc="vscode"
alias mcpjam="npx @mcpjam/inspector@latest"

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
alias npmls="npm ls -g --depth=0 --json"

# NPM save/restore aliases
alias npm-save='rm -f $DOTFILES_CONFIG/node/npm-global.txt && \
  npm ls -g --depth=0 --json | jq -r ".dependencies | keys[]" > $DOTFILES_CONFIG/node/npm-global.txt'
alias npm-restore='xargs npm install -g < $DOTFILES_CONFIG/node/npm-global.txt'

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
alias wsr="ws repos"
alias wsgh="ws github"
alias wsex="ws external"
alias wsw="ws work"

# Work-specific
## @xebia-functional
alias wsxb="ws xebia"

# projects
alias wspj="ws projects"

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
