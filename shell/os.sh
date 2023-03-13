
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
  alias open="explorer.exe"
  alias sublime="${OS_DRIVE}/c/Program\ Files/Sublime\ Text/sublime_text.exe"
  alias vscode="${OS_DRIVE}/c/Users/david/AppData/Local/Programs/Microsoft\ VS\ Code/Code.exe" 

# MacOS
elif [[ "$OSTYPE" =~ ^darwin ]]; then

  # NVM env
  export NVM_DIR="$HOME/.nvm"
  source $(brew --prefix nvm)/nvm.sh

  # Node path
  export NODE_PATH=$NODE_PATH:`npm root -g`

  # Ruby env
  eval "$(rbenv init - zsh)"

  # TheFuck
  eval $(thefuck --alias)

  # Paths
  OS_DRIVE="/Volumes/Macintosh\ HD" 
  OS_WORKSPACE="~/Workspace" 

  # Apps
  alias sublime="open ${OS_DRIVE}/Applications/Sublime\ Text.app/Contents/SharedSupport/bin/subl"
  alias vscode="open ${OS_DRIVE}/Applications/Visual\ Studio\ Code.app"  

  # Terminal
  alias ls="exa"
  alias l="exa -l"
  alias ll="exa -la"

  # Daily wallpaper (execute once, node needed)
  # npx --yes bing-wallpaper-daily-mac-multimonitor@latest enable-auto-update

# Cygwin
elif [[ "$OSTYPE" == "cygwin"* ]]; then

  # Paths
  OS_DRIVE="/cygdrive" 
  OS_WORKSPACE="${OS_DRIVE}/d/Workspace" 

  # Apps
  alias su="${OS_DRIVE}/c/Program\ Files/Sublime\ Text/sublime_text.exe"
  alias vsc="${OS_DRIVE}/c/Users/david/AppData/Local/Programs/Microsoft\ VS\ Code/Code.exe"  

fi
