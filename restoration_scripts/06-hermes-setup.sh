#!/usr/bin/env bash

set -e

echo "Setting up Hermes Agent..."
echo

# Hermes Agent installs into ~/.hermes and symlinks ~/.local/bin/hermes
# It handles uv, Python 3.11, Node.js, and dependencies automatically

if command -v hermes &> /dev/null; then
    echo "Hermes already installed at: $(which hermes)"
    echo "Updating..."
    hermes update || true
else
    echo "Installing Hermes Agent..."
    curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
    
    # Reload shell so hermes is available immediately
    if [ -n "$ZSH_VERSION" ]; then
        source ~/.zshrc 2>/dev/null || true
    elif [ -n "$BASH_VERSION" ]; then
        source ~/.bashrc 2>/dev/null || true
    fi
    
    echo "Hermes installed successfully"
fi

echo
echo "Hermes setup complete!"
echo "Run 'hermes' to start, or 'hermes setup' for first-time configuration"
