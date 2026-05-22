#!/usr/bin/env python3
"""
Apple Music Playlist Sync — Full pipeline.

Uses browser automation to export playlists from Apple Music web (free, no API keys)
or the official Apple Music API if you have a developer account.

Usage:
    python3 sync_apple_music.py              # Dry run
    python3 sync_apple_music.py --apply      # Full sync with downloads
    python3 sync_apple_music.py --use-api    # Use official API instead of web scraper
"""

import sys
import subprocess
from pathlib import Path

WEB_EXPORT_SCRIPT = Path.home() / ".dotfiles/scripts/mp3-tagger/export_apple_music_web.py"
API_EXPORT_SCRIPT = Path.home() / ".dotfiles/scripts/mp3-tagger/export_apple_music_api.py"
SYNC_SCRIPT = Path.home() / ".dotfiles/scripts/mp3-tagger/sync_playlists_yt.py"
DEFAULT_PLAYLIST_DIR = Path.home() / "playlists"
DEFAULT_EXPORTS_DIR = Path("/mnt/c/Users/david/Music/iTunes/Playlist Exports")
DEFAULT_LIBRARY_DIR = Path("/mnt/c/Users/david/iCloudDrive/1-Storage/1-Libraries/3-Music")


def run_web_export() -> bool:
    print("\n" + "=" * 70)
    print("  Step 1: Exporting Apple Music playlists via web browser")
    print("=" * 70 + "\n")

    if not WEB_EXPORT_SCRIPT.exists():
        print(f"Export script not found: {WEB_EXPORT_SCRIPT}")
        return False

    result = subprocess.run(
        ["python3", str(WEB_EXPORT_SCRIPT), "--output", str(DEFAULT_PLAYLIST_DIR)],
        capture_output=False,
    )
    return result.returncode == 0


def run_api_export() -> bool:
    print("\n" + "=" * 70)
    print("  Step 1: Exporting Apple Music playlists via API")
    print("=" * 70 + "\n")

    if not API_EXPORT_SCRIPT.exists():
        print(f"API export script not found: {API_EXPORT_SCRIPT}")
        return False

    result = subprocess.run(["python3", str(API_EXPORT_SCRIPT)], capture_output=False)
    return result.returncode == 0


def run_sync(apply: bool, playlist_dir: Path) -> bool:
    print("\n" + "=" * 70)
    print("  Step 2: Syncing playlists to library")
    print("=" * 70 + "\n")

    if not SYNC_SCRIPT.exists():
        print(f"Sync script not found: {SYNC_SCRIPT}")
        return False

    cmd = ["python3", str(SYNC_SCRIPT), "--playlist-dir", str(playlist_dir)]
    if apply:
        cmd.append("--apply")

    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Apple Music -> Library sync pipeline")
    parser.add_argument("--apply", action="store_true", help="Actually download and copy files")
    parser.add_argument("--use-api", action="store_true", help="Use official Apple Music API (requires dev account)")
    parser.add_argument("--skip-export", action="store_true", help="Skip export (use existing playlist files)")
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"\n{'='*70}")
    print(f"  Apple Music Playlist Sync — {mode}")
    print(f"  Library: {DEFAULT_LIBRARY_DIR}")
    print(f"{'='*70}")

    playlist_dir = DEFAULT_PLAYLIST_DIR

    if not args.skip_export:
        if args.use_api:
            if not run_api_export():
                print("\n  API export failed. Aborting.")
                sys.exit(1)
            playlist_dir = DEFAULT_EXPORTS_DIR
        else:
            if not run_web_export():
                print("\n  Web export failed. Aborting.")
                sys.exit(1)
    else:
        print("\n  Skipping export (using existing playlist files)")

    if not run_sync(args.apply, playlist_dir):
        print("\n  Sync failed.")
        sys.exit(1)

    print(f"\n{'='*70}")
    print("  Done!")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
