# ---------------------------
#       Global functions
# ---------------------------

# CD if exists
function cdd() {
	cd "$(ls -d -- */ | fzf)" || echo "Invalid directory"
}

function j() {
	fname=$(declare -f -F _z)

	[ -n "$fname" ] || source "$DOTLY_PATH/modules/z/z.sh"

	_z "$1"
}

# Get recent dirs
function recent_dirs() {
	# This script depends on pushd. It works better with autopush enabled in ZSH
	escaped_home=$(echo $HOME | sed 's/\//\\\//g')
	selected=$(dirs -p | sort -u | fzf)

	cd "$(echo "$selected" | sed "s/\~/$escaped_home/")" || echo "Invalid directory"
}

# Function to change to the corresponding directory
ws() {
  case $1 in
  	# Main repos
    "repos") cd $REPOS ;;
    "github") cd $GITHUB ;;
    "external") cd $EXTERNAL ;;
    "work") cd $WORK ;;
	  # Work-specific repos
	  ## @xebia-functional
    "xebia") cd $XEBIA ;;
    "cortex") cd $CORTEX ;;
	  ## Projects
    "xef") cd "$XEBIA/ai/xef" ;;
	  # Tools repos
    "tools") cd $TOOLS ;;
	  # Language repos
	  "rust") cd $RUST ;;
    "web") cd $WEB ;;
    "php") cd $PHP ;;
    "java") cd $JAVA ;;
    "kotlin") cd $KOTLIN ;;
    "scala") cd $SCALA ;;
    "python") cd $PYTHON ;;
    # Add more cases for other directories
    *) cd $WORKSPACE ;;
  esac
}

# Tere
tere() {
  local result=$(command tere "$@")
  [ -n "$result" ] && cd -- "$result"
}
