#!/usr/bin/env bash

set -e

# dotly sources these scripts without changing directory, so cwd is wherever
# `dot self install` was invoked from. Every git/git-crypt call below is
# repo-relative — without this cd they would target the wrong repository, or no
# repository at all. Sourcing happens inside a pipeline subshell, so this cd
# cannot leak back to the caller.
_REPO_ROOT="${DOTFILES_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$_REPO_ROOT" || { echo "Cannot enter dotfiles repo: $_REPO_ROOT"; exit 1; }

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

# Presence of the symmetric key is the canonical "this checkout is unlocked" signal.
# Do NOT count `git-crypt status | grep encrypted` — that lists paths encrypted in the
# repo regardless of unlock state, so it is never 0 and would send an already-unlocked
# machine into `git-crypt unlock`, which errors out (and refuses on a dirty tree).
if [ -f "$_REPO_ROOT/.git/git-crypt/keys/default" ]; then
    echo "Repository is already unlocked!"
    echo
    exit 0
fi

echo "Encrypted sources detected:"
echo " - config/opencode/** (OpenCode agent configs)"
echo " - config/opencode/global/AGENTS.md (Global OpenCode rules)"
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

if [ -f "config/opencode/global/AGENTS.md" ]; then
    if head -n 1 config/opencode/global/AGENTS.md >/dev/null 2>&1; then
        echo "config/opencode/global/AGENTS.md"
    else
        echo "config/opencode/global/AGENTS.md - may still be encrypted"
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
