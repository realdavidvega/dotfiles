# This is a useful file to have the same aliases/functions in bash and zsh

# ---------------------------
#       Pre-init Functions
# ---------------------------

# Source if exists
function sourceif() {
	file_path="$1"
	if test -f "$file_path"; then
		source "$file_path"
	else
		echo "Warning: $file_path does not exist."
	fi
}

# Symlink if not exists
function lnsif() {
    target_path="$1"
    symlink_path="$2"

    if [ ! -L "$symlink_path" ]; then
        ln -s "$target_path" "$symlink_path"
    fi
}

# ---------------------------
#           Init
# ---------------------------

sourceif "$DOTFILES_PATH/secrets/secrets.sh"
sourceif "$DOTFILES_PATH/shell/exports.sh"
sourceif "$DOTFILES_PATH/shell/functions.sh"
sourceif "$DOTFILES_PATH/shell/aliases.sh"
