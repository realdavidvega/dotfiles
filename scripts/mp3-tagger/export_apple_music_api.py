#!/usr/bin/env python3
"""
Export Apple Music playlists via the official Apple Music API.

Outputs M3U files that sync_playlists_yt.py can consume.

Prerequisites:
1. Apple Developer account (free)
2. A MusicKit private key + key ID + team ID
3. A Music User Token (one-time auth via browser)

Usage:
    # Export all playlists
    python3 export_apple_music_api.py --dev-token "eyJ..." --user-token "Ag..."

    # Or save tokens to a config file first
    python3 export_apple_music_api.py --save-config
    python3 export_apple_music_api.py
"""

import os
import sys
import json
import base64
import argparse
import subprocess
from pathlib import Path
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("requests not installed. Run: pip install requests")
    sys.exit(1)

API_BASE = "https://api.music.apple.com/v1"
DEFAULT_OUTPUT = Path("/mnt/c/Users/david/Music/iTunes/Playlist Exports")
CONFIG_PATH = Path.home() / ".config" / "apple-music-api.json"


def api_get(endpoint: str, dev_token: str, user_token: str, params: dict = None) -> dict:
    headers = {
        "Authorization": f"Bearer {dev_token}",
        "Music-User-Token": user_token,
    }
    url = f"{API_BASE}{endpoint}"
    if params:
        url += "?" + urlencode(params)

    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 401:
        print("  Authentication failed. Check your dev token and user token.")
        sys.exit(1)
    elif response.status_code == 403:
        print("  Forbidden. Your developer token may not have MusicKit enabled.")
        sys.exit(1)
    response.raise_for_status()
    return response.json()


def get_all_playlists(dev_token: str, user_token: str) -> list:
    playlists = []
    offset = 0
    limit = 100

    while True:
        data = api_get("/me/library/playlists", dev_token, user_token, {"limit": limit, "offset": offset})
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            playlists.append({
                "id": item.get("id"),
                "name": item.get("attributes", {}).get("name", "Untitled"),
                "track_count": item.get("attributes", {}).get("trackCount", 0),
            })

        if len(items) < limit:
            break
        offset += limit

    return playlists


def get_playlist_tracks(playlist_id: str, dev_token: str, user_token: str) -> list:
    tracks = []
    offset = 0
    limit = 100

    while True:
        data = api_get(f"/me/library/playlists/{playlist_id}/tracks", dev_token, user_token, {"limit": limit, "offset": offset})
        items = data.get("data", [])
        if not items:
            break

        for item in items:
            attrs = item.get("attributes", {})
            tracks.append({
                "name": attrs.get("name", ""),
                "artist": attrs.get("artistName", ""),
                "album": attrs.get("albumName", ""),
                "duration": attrs.get("durationInMillis", 0),
            })

        if len(items) < limit:
            break
        offset += limit

    return tracks


def sanitize_filename(name: str) -> str:
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, "-")
    return name.strip(". ")


def export_playlist_m3u(playlist_name: str, tracks: list, output_dir: Path) -> Path:
    m3u_lines = ["#EXTM3U"]
    for track in tracks:
        artist = track.get("artist", "")
        title = track.get("name", "")
        duration_sec = int(track.get("duration", 0) / 1000)
        m3u_lines.append(f"#EXTINF:{duration_sec},{artist} - {title}")
        m3u_lines.append("")

    safe_name = sanitize_filename(playlist_name)
    m3u_path = output_dir / f"{safe_name}.m3u"
    m3u_path.parent.mkdir(parents=True, exist_ok=True)

    with open(m3u_path, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_lines) + "\n")

    return m3u_path


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(dev_token: str, user_token: str):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"dev_token": dev_token, "user_token": user_token}, f)
    print(f"  Saved tokens to {CONFIG_PATH}")


def generate_dev_token(private_key_path: str, key_id: str, team_id: str) -> str:
    try:
        import jwt
    except ImportError:
        print("  PyJWT not installed. Run: pip install pyjwt")
        sys.exit(1)

    with open(private_key_path, "r") as f:
        private_key = f.read()

    headers = {
        "alg": "ES256",
        "kid": key_id,
    }
    payload = {
        "iss": team_id,
        "iat": int(__import__("time").time()),
        "exp": int(__import__("time").time()) + 15777000,
    }

    token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
    return token


def print_setup_help():
    print("""
  Setup Guide: Apple Music API Tokens
  ====================================

  1. Developer Token (JWT)
     ----------------------
     a) Sign in to https://developer.apple.com/account/
     b) Go to Certificates, Identifiers & Profiles → Keys
     c) Click + to create a new key
     d) Enable "MusicKit" and give it a name
     e) Download the .p8 file (you only get ONE chance)
     f) Note the Key ID and your Team ID (from Membership details)

     Then run this script with:
       --private-key /path/to/AuthKey_XXX.p8 --key-id XXX --team-id YYY

  2. Music User Token
     -----------------
     Open this helper in your browser:
       file:///home/david/.dotfiles/scripts/mp3-tagger/get_music_user_token.html

     Or manually:
       a) Open https://music.apple.com in your browser
       b) Sign in with your Apple ID
       c) Open DevTools (F12) → Network tab
       d) Refresh the page or click Library
       e) Find a request to api.music.apple.com
       f) Copy the "Music-User-Token" header value

  3. Save tokens for reuse:
     python3 export_apple_music_api.py --save-config --dev-token TOKEN --user-token TOKEN
""")


def main():
    parser = argparse.ArgumentParser(description="Export Apple Music playlists via API")
    parser.add_argument("--dev-token", help="Apple Music Developer JWT token")
    parser.add_argument("--user-token", help="Apple Music User Token")
    parser.add_argument("--private-key", type=Path, help="Path to .p8 private key file")
    parser.add_argument("--key-id", help="MusicKit Key ID")
    parser.add_argument("--team-id", help="Apple Team ID")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory for M3U files")
    parser.add_argument("--save-config", action="store_true", help="Save tokens to config for reuse")
    parser.add_argument("--setup", action="store_true", help="Show setup instructions")
    args = parser.parse_args()

    if args.setup:
        print_setup_help()
        sys.exit(0)

    config = load_config()
    dev_token = args.dev_token or config.get("dev_token")
    user_token = args.user_token or config.get("user_token")

    if args.private_key and args.key_id and args.team_id:
        dev_token = generate_dev_token(args.private_key, args.key_id, args.team_id)
        print("  Generated developer token from private key")

    if not dev_token or not user_token:
        print("  Missing tokens. Run with --setup for instructions.")
        sys.exit(1)

    if args.save_config:
        save_config(dev_token, user_token)

    print(f"\n  {'='*70}")
    print("  Apple Music API Playlist Exporter")
    print(f"  {'='*70}\n")

    print("  Fetching playlists...")
    playlists = get_all_playlists(dev_token, user_token)
    print(f"  Found {len(playlists)} playlists")

    exported = 0
    total_tracks = 0

    for playlist in playlists:
        name = playlist["name"]
        pid = playlist["id"]
        track_count = playlist["track_count"]

        if track_count == 0:
            continue

        tracks = get_playlist_tracks(pid, dev_token, user_token)
        if not tracks:
            continue

        m3u_path = export_playlist_m3u(name, tracks, args.output)
        print(f"  ✓ {name}: {len(tracks)} tracks → {m3u_path}")
        exported += 1
        total_tracks += len(tracks)

    print(f"\n  {'='*70}")
    print(f"  Exported: {exported} playlists ({total_tracks} tracks)")
    print(f"  Output: {args.output}")
    print(f"  {'='*70}\n")


if __name__ == "__main__":
    main()
