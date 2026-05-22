#!/usr/bin/env bash

set -euo pipefail

echo "Setting up uv tools..."
echo

if ! command -v uv &> /dev/null; then
    echo "uv is not installed. Skipping uv tools setup."
    echo "Install uv from: https://docs.astral.sh/uv/getting-started/installation/"
    exit 0
fi

UV_TOOLS_FILE="$DOTFILES_PATH/langs/python/uv_tools.txt"

if [ -f "$UV_TOOLS_FILE" ]; then
    echo "Installing uv tools from: $UV_TOOLS_FILE"
    while IFS= read -r tool; do
        [[ -z "$tool" || "$tool" =~ ^# ]] && continue

        tool_name=$(echo "$tool" | awk '{print $1}')

        if uv tool list | grep -q "^${tool_name} "; then
            echo "  ✓ $tool_name already installed"
        else
            echo "  → Installing $tool_name..."
            uv tool install $tool
        fi
    done < "$UV_TOOLS_FILE"
else
    echo "No uv_tools.txt manifest found at $UV_TOOLS_FILE"
    echo "Falling back to installing known tools..."

    if ! uv tool list | grep -q "^basedpyright "; then
        echo "  → Installing basedpyright..."
        uv tool install basedpyright
    else
        echo "  ✓ basedpyright already installed"
    fi
fi

echo
echo "uv tools setup complete!"
