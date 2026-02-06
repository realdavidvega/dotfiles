#!/usr/bin/env bash

set -e

# =============================================================================
# CONFIGURATION: Edit this before restoring on a new machine
# =============================================================================
# Default key location - change this to where your key will be
GIT_CRYPT_KEY_PATH="$HOME/dotfiles-key.bin"
# Alternative common locations (uncomment and use if needed):
# GIT_CRYPT_KEY_PATH="$HOME/Downloads/dotfiles-key.bin"
# GIT_CRYPT_KEY_PATH="/mnt/c/Users/YourName/dotfiles-key.bin"  # WSL
# =============================================================================

echo "=========================================="
echo "Unlocking Encrypted Sources"
echo "=========================================="
echo

if ! command -v git-crypt &> /dev/null; then
    echo "git-crypt is not installed!"
    echo
    echo "Install it first:"
    echo "  • Ubuntu/Debian (WSL): sudo apt update && sudo apt install git-crypt"
    echo "  • macOS: brew install git-crypt"
    echo
    exit 1
fi

if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "Not in a git repository!"
    exit 1
fi

if git-crypt status &> /dev/null; then
    ENCRYPTED_COUNT=$(git-crypt status | grep -c "encrypted" || true)
    
    if [ "$ENCRYPTED_COUNT" -eq 0 ]; then
        echo "Repository is already unlocked!"
        echo
        exit 0
    fi
fi

echo "Encrypted sources detected:"
echo " - config/opencode/** (OpenCode agent configs)"
echo " - doc/opencode/** (Agent architecture docs)"
echo " - secrets/opencode/** (OpenCode secrets)"
echo " - git/work/.gitconfig (Work git config)"
echo

GIT_CRYPT_KEY_PATH="${GIT_CRYPT_KEY_PATH/#\~/$HOME}"

if [ ! -f "$GIT_CRYPT_KEY_PATH" ]; then
    echo "Key file not found: $GIT_CRYPT_KEY_PATH"
    echo
    echo "Edit this script and set GIT_CRYPT_KEY_PATH to your key location:"
    echo "  $DOTFILES_PATH/restoration_scripts/00-unlock-encrypted-sources.sh"
    echo
    echo "Then run: dot self install"
    echo
    exit 1
fi

echo "Using key: $GIT_CRYPT_KEY_PATH"
echo

git config core.autocrlf false
git config core.eol lf

echo "Unlocking repository..."
if git-crypt unlock "$GIT_CRYPT_KEY_PATH"; then
    echo "Repository unlocked successfully!"
else
    echo "Failed to unlock repository"
    exit 1
fi

echo
echo "Verifying unlock..."

VERIFICATION_FAILED=0

if [ -f "config/opencode/opencode.json" ]; then
    if jq empty config/opencode/opencode.json 2>/dev/null; then
        echo "config/opencode/opencode.json"
    else
        echo "config/opencode/opencode.json - may still be encrypted"
        VERIFICATION_FAILED=1
    fi
fi

if [ -f "doc/opencode/agent-architecture.md" ]; then
    if head -n 1 doc/opencode/agent-architecture.md | grep -q "OpenCode Agent Architecture" 2>/dev/null; then
        echo "doc/opencode/agent-architecture.md"
    else
        echo "doc/opencode/agent-architecture.md - may still be encrypted"
        VERIFICATION_FAILED=1
    fi
fi

if [ -f "secrets/opencode/antigravity-accounts.json" ]; then
    if jq empty secrets/opencode/antigravity-accounts.json 2>/dev/null; then
        echo "secrets/opencode/antigravity-accounts.json"
    else
        echo "secrets/opencode/antigravity-accounts.json - may still be encrypted"
        VERIFICATION_FAILED=1
    fi
fi

if [ -f "secrets/opencode/perplexity-auth.json" ]; then
    if jq empty secrets/opencode/perplexity-auth.json 2>/dev/null; then
        echo "secrets/opencode/perplexity-auth.json"
    else
        echo "secrets/opencode/perplexity-auth.json - may still be encrypted"
        VERIFICATION_FAILED=1
    fi
fi

if [ -f "git/work/.gitconfig" ]; then
    if head -n 1 git/work/.gitconfig &>/dev/null; then
        echo "git/work/.gitconfig"
    else
        echo "git/work/.gitconfig - may still be encrypted"
        VERIFICATION_FAILED=1
    fi
fi

echo

if [ $VERIFICATION_FAILED -eq 0 ]; then
    echo "All encrypted sources unlocked successfully!"
else
    echo "Unlock completed with warnings"
    echo "Some files may still be encrypted. Check above for details."
fi

echo
