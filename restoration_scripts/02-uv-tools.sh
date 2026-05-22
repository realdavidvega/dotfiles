#!/usr/bin/env bash

set -euo pipefail

echo "Setting up uv tools..."
echo

# ---- Global Python venv for dotfiles packages ----
DOTFILES_PYTHON_VENV="${HOME}/.local/share/dotfiles/python-venv"
PYTHON_REQUIREMENTS="$DOTFILES_PATH/langs/python/requirements.txt"

echo "Setting up global Python venv for dotfiles packages..."

if [ ! -d "$DOTFILES_PYTHON_VENV" ]; then
    echo "  → Creating venv at $DOTFILES_PYTHON_VENV"
    uv venv "$DOTFILES_PYTHON_VENV"
else
    echo "  ✓ venv already exists"
fi

if [ -f "$PYTHON_REQUIREMENTS" ]; then
    echo "  → Installing packages from $PYTHON_REQUIREMENTS"
    uv pip install -r "$PYTHON_REQUIREMENTS" --python "$DOTFILES_PYTHON_VENV/bin/python"
else
    echo "  No requirements.txt found, skipping package installation"
fi

echo "Python venv setup complete!"
echo
