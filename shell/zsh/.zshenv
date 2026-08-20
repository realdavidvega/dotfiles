# Skip Ubuntu's global compinit: let zim handle it
skip_global_compinit=1

export DOTFILES_PATH="$HOME/.dotfiles"
export DOTLY_PATH="$DOTFILES_PATH/modules/dotly"
export DOTLY_THEME="codely"

if [ -d "$HOME/.nvm/versions/node" ]; then
  for node_bin in "$HOME"/.nvm/versions/node/*/bin(N); do
    export PATH="$PATH:$node_bin"
  done
fi
