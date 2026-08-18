#!/usr/bin/env bash

set -euo pipefail

echo "Setting up playlist sync dependencies..."
echo

PLAYLIST_SYNC_DIR="$DOTFILES_PATH/scripts/mp3-tagger"

if [ -d "$PLAYLIST_SYNC_DIR" ]; then
    echo "  Playlist sync scripts found at: $PLAYLIST_SYNC_DIR"
else
    echo "  Playlist sync scripts not found at expected location"
fi

if command -v playwright &> /dev/null; then
    echo "  Checking Playwright browsers..."
    if playwright install chromium >/dev/null 2>&1; then
        echo "  Playwright Chromium browser installed"
    else
        echo "  Playwright Chromium browser already installed or install pending"
    fi
else
    echo "  Playwright not found. It should be installed by 'dot package import'"
    echo "  from langs/python/requirements.txt. Run:"
    echo "    dot package import"
    echo "  Then re-run this script."
fi

echo

if command -v yt-dlp &> /dev/null; then
    echo "  yt-dlp is already installed"
else
    echo "  yt-dlp not found. It should be installed by 'dot package import'"
    echo "  from os/linux/brew/Brewfile. Run:"
    echo "    dot package import"
fi

echo
echo "Playlist sync setup complete!"
echo
echo "Usage:"
echo "  python3 $PLAYLIST_SYNC_DIR/sync_apple_music.py --apply"
