#!/usr/bin/env bash

set -euo pipefail

echo "Setting up uv tools..."
echo

if ! command -v uv &> /dev/null; then
    echo "  uv not found — skipping. Install it (brew install uv, or the official"
    echo "  installer) and re-run: bash restoration_scripts/02-uv-tools.sh"
    echo
    return 0 2>/dev/null || exit 0
fi

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

# ---- uv tools (standalone CLIs, one isolated venv each) ----
# `dot package dump` does not know about uv tools, so this manifest is maintained by
# hand and this is the only thing that restores it.
UV_TOOLS="$DOTFILES_PATH/langs/python/uv_tools.txt"

if [ -f "$UV_TOOLS" ]; then
    echo "Installing uv tools from $UV_TOOLS..."
    while IFS= read -r tool || [ -n "$tool" ]; do
        tool="${tool%%#*}"                     # strip comments
        tool="$(echo "$tool" | xargs)"         # trim whitespace
        [ -n "$tool" ] || continue
        echo "  → $tool"
        uv tool install "$tool" || echo "    failed: $tool (continuing)"
    done < "$UV_TOOLS"
    echo "uv tools complete!"
else
    echo "No uv_tools.txt found, skipping uv tool installation"
fi
echo
