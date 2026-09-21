#!/usr/bin/env bash

set -e

echo "Setting up Ollama..."
echo

# Detect OS
OS="unknown"
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
elif [[ "$OSTYPE" == "linux-gnu"* ]] || [[ "$OSTYPE" == "linux" ]]; then
    OS="linux"
fi

install_ollama_macos() {
    echo "Detected macOS ($OS)..."
    
    if command -v ollama &> /dev/null; then
        echo "Ollama already installed at: $(which ollama)"
        
        # Check if it's the broken Homebrew formula (0.30.x without llama-server)
        OLLAMA_VERSION=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        if [[ "$OLLAMA_VERSION" =~ ^0\.30\. ]]; then
            # Check if it's the formula (in Cellar) vs cask (in Applications)
            if ollama --version 2>&1 | grep -q "Cellar" || [[ "$(which ollama)" == *"Cellar"* ]]; then
                echo "WARNING: Detected broken Homebrew formula $OLLAMA_VERSION"
                echo "The formula is missing llama-server binary. Migrating to cask..."
                brew services stop ollama 2>/dev/null || true
                brew uninstall ollama 2>/dev/null || true
                brew install --cask ollama
                echo "Ollama desktop app installed successfully"
                return
            fi
        fi
        
        echo "Ollama is up to date ($OLLAMA_VERSION)"
        return
    fi
    
    # Fresh install: prefer cask on macOS
    echo "Installing Ollama via Homebrew Cask..."
    brew install --cask ollama
    echo "Ollama desktop app installed successfully"
}

install_ollama_linux() {
    echo "Detected Linux/WSL ($OS)..."
    
    if command -v ollama &> /dev/null; then
        echo "Ollama already installed at: $(which ollama)"
        return
    fi
    
    echo "Installing Ollama via official installer..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "Ollama installed successfully"
}

case "$OS" in
    macos)
        install_ollama_macos
        ;;
    linux)
        install_ollama_linux
        ;;
    *)
        echo "Unknown OS: $OSTYPE"
        echo "Please install Ollama manually from https://ollama.com"
        exit 1
        ;;
esac

echo
echo "Ollama setup complete!"
echo "Run 'ollama serve' to start the server (or launch the desktop app on macOS)"
