#!/usr/bin/env bash

set -e

echo "Setting up OpenClaw..."
echo

# OpenClaw installs via npm and puts completions in ~/.openclaw/completions/

if command -v openclaw &> /dev/null; then
    echo "OpenClaw already installed at: $(which openclaw)"
else
    echo "Installing OpenClaw..."
    npm install -g openclaw
    
    # Generate shell completions
    openclaw completion zsh > "$HOME/.openclaw/completions/openclaw.zsh" 2>/dev/null || true
    
    echo "OpenClaw installed successfully"
fi

echo
echo "OpenClaw setup complete!"
