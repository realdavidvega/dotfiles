#!/usr/bin/env bash

set -e

echo "Setting up OpenCode configuration..."
echo

# Create necessary directories
mkdir -p "$HOME/.config/opencode"
mkdir -p "$HOME/.agents"

echo "OpenCode directories created"
echo "Symlinks will be created by dotly/dotbot automatically"
echo
echo "OpenCode configuration files location:"
echo "- Config: $DOTFILES_PATH/config/opencode/"
echo "- Secrets: $DOTFILES_PATH/secrets/opencode/"
echo "- Global OpenCode rules: $DOTFILES_PATH/config/opencode/global/AGENTS.md"
echo

# Configure Ollama provider in opencode.json if Ollama is installed
if command -v ollama &> /dev/null; then
    echo "Ollama detected, configuring provider in OpenCode..."
    
    OPENCODE_CONFIG="$HOME/.config/opencode/opencode.json"
    
    # Create config if it doesn't exist
    if [ ! -f "$OPENCODE_CONFIG" ]; then
        echo '{"$schema": "https://opencode.ai/config.json"}' > "$OPENCODE_CONFIG"
    fi
    
    # Check if Ollama provider already exists
    if ! grep -q '"ollama"' "$OPENCODE_CONFIG" 2>/dev/null; then
        echo "Adding Ollama provider to OpenCode config..."
        
        # Use python to safely modify JSON
        python3 << 'PYEOF'
import json
import sys

config_path = "$HOME/.config/opencode/opencode.json"

try:
    with open(config_path, 'r') as f:
        config = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    config = {"$schema": "https://opencode.ai/config.json"}

# Ensure provider section exists
if "provider" not in config:
    config["provider"] = {}

# Add Ollama provider
config["provider"]["ollama"] = {
    "npm": "@ai-sdk/openai-compatible",
    "name": "Ollama",
    "options": {
        "baseURL": "http://localhost:11434/v1"
    },
    "models": {}
}

with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print("Ollama provider added to OpenCode config")
PYEOF
    else
        echo "Ollama provider already configured"
    fi
else
    echo "Ollama not found. Install it first if you want local models."
fi

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
