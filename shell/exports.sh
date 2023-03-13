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
export JAVA_HOME='/Library/Java/JavaVirtualMachines/amazon-corretto-15.jdk/Contents/Home'
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

# ------------------------------------------------------------------------------
# Path - The higher it is, the more priority it has
# ------------------------------------------------------------------------------
path=(
	"$HOME/bin"
	"$DOTLY_PATH/bin"
	"$DOTFILES_PATH/bin"
	"$JAVA_HOME/bin"
	"$GEM_HOME/bin"
	"$GOPATH/bin"
	"$HOME/.cargo/bin"
	"/usr/local/opt/ruby/bin"
	"/usr/local/opt/python/libexec/bin"
	"/opt/homebrew/bin"
	"/usr/local/bin"
	"/usr/local/sbin"
	"/bin"
	"/usr/bin"
	"/usr/sbin"
	"/sbin"
	"$path"
)

export path

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
## @47deg
export FOURTYSEVEN="${WORK}/47deg"

# Tools repos
export TOOLS="${GITHUB}/tools"
export DOTFILES="${DOTFILES_PATH}"

# Rust repos
export RUST="${GITHUB}/rust"

# Web repos
export WEB="${GITHUB}/web"

### MANAGED BY RANCHER DESKTOP START (DO NOT EDIT)
export PATH="/Users/david/.rd/bin:$PATH"
### MANAGED BY RANCHER DESKTOP END (DO NOT EDIT)
