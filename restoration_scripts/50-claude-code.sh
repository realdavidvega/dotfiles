#!/usr/bin/env bash

set -euo pipefail

echo "Setting up Claude Code..."
echo

if command -v claude &> /dev/null; then
  echo "  Claude Code already installed: $(claude --version 2>/dev/null || echo present)"
  exit 0
fi

if [[ "$OSTYPE" =~ ^darwin ]]; then
  if command -v brew &> /dev/null; then
    echo "  Installing Claude Code via Homebrew cask..."
    brew install --cask claude-code
  else
    echo "  Homebrew not found. Install Homebrew first, then run: brew install --cask claude-code"
    exit 1
  fi
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
  echo "  Installing Claude Code via official installer..."
  curl -fsSL https://claude.ai/install.sh | bash
else
  echo "  Unsupported OS for Claude Code setup: $OSTYPE"
  exit 1
fi

echo
echo "Claude Code setup complete!"
