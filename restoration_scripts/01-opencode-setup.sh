#!/usr/bin/env bash

echo "Setting up OpenCode configuration..."
echo

# Create necessary directories
mkdir -p "$HOME/.config/opencode"

echo "OpenCode directories created"
echo "Symlinks will be created by dotly/dotbot automatically"
echo
echo "OpenCode configuration files location:"
echo "- Config: $DOTFILES_PATH/config/opencode/"
echo "- Secrets: $DOTFILES_PATH/secrets/opencode/"
echo

# Check if OpenCode is installed
if command -v opencode &> /dev/null; then
    echo "OpenCode CLI detected"
    
    # Install OpenCode plugins/dependencies if package.json exists in config
    if [ -f "$HOME/.config/opencode/package.json" ]; then
        echo "Installing OpenCode plugins..."
        cd "$HOME/.config/opencode"
        
        # Try bun first, fallback to npm
        if command -v bun &> /dev/null; then
            bun install
        elif command -v npm &> /dev/null; then
            npm install
        else
            echo "Neither bun nor npm found. Please install dependencies manually."
        fi
    fi
else
    echo "OpenCode not installed. Install it from: https://opencode.ai"
    echo "After installation, run: dot self install"
fi

echo
echo "OpenCode setup complete!"
