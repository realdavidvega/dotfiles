#!/usr/bin/env python3
"""
Playlist Sync with YouTube Download — WSL workflow without iTunes.

Reads playlist definition files (.txt or .m3u), finds tracks in your local
library, downloads missing ones from YouTube via yt-dlp, and syncs to
categorized numbered folders.

Usage:
    python3 sync_playlists_yt.py --playlist "My Playlist.txt"
    python3 sync_playlists_yt.py --playlist-dir ~/playlists/ --apply
    python3 sync_playlists_yt.py --playlist "playlist.m3u" --library-dir /path/to/music --apply
"""

import os
import re
import sys
import shutil
import argparse
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple, Dict
from difflib import SequenceMatcher

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_LIBRARY_DIR = Path("/mnt/c/Users/david/iCloudDrive/1-Storage/1-Libraries/3-Music")
DEFAULT_DOWNLOAD_DIR = Path("/mnt/c/Users/david/iCloudDrive/1-Storage/1-Libraries/3-Music/0-To Check")
DEFAULT_PLAYLIST_DIR = Path.home() / "playlists"

MATCH_THRESHOLD = 0.60
YT_DLP_BIN = "yt-dlp"

# ── Playlist Categorization ──────────────────────────────────────────────────

PLAYLIST_CATEGORIES = [
    ("1-Other", [
        r"misc", r"other", r"various", r"mixed",
    ]),
    ("2-Soundtracks & Themes", [
        r"soundtrack", r"theme", r"ost", r"score", r"anime",
        r"game", r"gaming", r"movie", r"series", r"instrumental",
        r"march", r"anthem", r"chant", r"classic",
    ]),
    ("3-Rock, Soul, Blues & Folk", [
        r"rock", r"soul", r"blues", r"folk", r"country",
        r"jazz", r"funk", r"alternative", r"indie",
    ]),
    ("4-Hard Rock & Metal", [
        r"metal", r"hard.?rock", r"punk", r"industrial",
        r"death.?metal", r"black.?metal", r"thrash",
        r"nu.?metal", r"metalcore", r"djent",
    ]),
    ("5-Electronic", [
        r"electro", r"electronic", r"synth", r"house",
        r"techno", r"trance", r"dubstep", r"edm",
        r"cyberpunk", r"synthwave", r"ambient", r"dnb",
        r"drum and bass", r"garage", r"lo.?fi",
    ]),
    ("6-Rap & Hip Hop", [
        r"rap", r"hip.?hop", r"trap", r"phonk",
        r"freestyle", r"battle", r"grime",
    ]),
    ("7-Latino", [
        r"latino", r"latin", r"reggaeton", r"salsa",
        r"bachata", r"merengue", r"cumbia", r"dembow",
        r"perreo", r"spanish", r"español", r"mexican",
        r"colombia", r"puerto rico", r"dominicana",
    ]),
]

DEFAULT_CATEGORY = "1-Other"


def categorize_playlist(playlist_name: str) -> str:
    """Return the category folder for a given playlist name."""
    name_lower = playlist_name.lower()
    for category, patterns in PLAYLIST_CATEGORIES:
        for pattern in patterns:
            if re.search(pattern, name_lower):
                return category
    return DEFAULT_CATEGORY


# ── Library Indexing ─────────────────────────────────────────────────────────

def fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def build_library_index(library_dir: Path) -> List[Tuple[str, Path]]:
    """Index all MP3 files in the library for fast matching."""
    print(f"  Indexing library: {library_dir}")
    files = []
    for ext in ("*.mp3", "*.m4a", "*.flac", "*.ogg", "*.wma"):
        files.extend(library_dir.rglob(ext))
    
    index = []
    for f in files:
        stem = f.stem
        stem = re.sub(r'\s*\[[A-Za-z0-9_-]{11}\]$', '', stem)
        stem = re.sub(r'\s+', ' ', stem).strip()
        index.append((stem, f))
    
    print(f"  Indexed {len(index)} tracks")
    return index


def find_best_match(artist: str, title: str, index: List[Tuple[str, Path]]) -> Optional[Path]:
    """Find the best matching audio file by comparing against filenames."""
    search_term = f"{artist} {title}".strip().lower()
    if not search_term:
        return None

    best_path = None
    best_score = 0.0

    for stem, path in index:
        stem_lower = stem.lower()
        score = fuzzy_ratio(search_term, stem_lower)
        
        if title.lower() in stem_lower:
            score += 0.15
        if artist.lower() in stem_lower:
            score += 0.15
        
        if score > best_score and score >= MATCH_THRESHOLD:
            best_score = score
            best_path = path

    return best_path


# ── YouTube Download ─────────────────────────────────────────────────────────

def download_from_youtube(artist: str, title: str, download_dir: Path) -> Optional[Path]:
    """Search YouTube and download the best match as MP3."""
    query = f"{artist} {title}".strip()
    search_query = f"ytsearch1:{query}"
    
    safe_name = re.sub(r'[<>:\"/\\|?*]', '-', query).strip('. ')
    output_template = str(download_dir / f"{safe_name}.%(ext)s")
    
    print(f"    🔍 Searching YouTube: {query}")
    
    cmd = [
        YT_DLP_BIN,
        search_query,
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--embed-metadata",
        "--embed-thumbnail",
        "--convert-thumbnails", "jpg",
        "--output", output_template,
        "--no-playlist",
        "--quiet",
        "--no-warnings",
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f"    ⚠ yt-dlp failed: {result.stderr.strip()[:200]}")
            return None
    except subprocess.TimeoutExpired:
        print(f"    ⚠ Download timed out")
        return None
    except FileNotFoundError:
        print(f"    ⚠ yt-dlp not found. Install with: brew install yt-dlp")
        return None
    
    expected_path = download_dir / f"{safe_name}.mp3"
    if expected_path.exists():
        print(f"    ✓ Downloaded: {expected_path.name}")
        return expected_path
    
    for f in download_dir.glob(f"{safe_name}*.mp3"):
        print(f"    ✓ Downloaded: {f.name}")
        return f
    
    return None


# ── Playlist Parsing ──────────────────────────────────────────────────────────

def parse_txt_playlist(path: Path) -> List[Tuple[str, str]]:
    """Parse a simple text playlist: one 'Artist - Title' per line."""
    tracks = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            if " - " in line:
                artist, title = line.split(" - ", 1)
                artist = artist.strip()
                title = title.strip()
            else:
                artist = ""
                title = line.strip()
            
            tracks.append((artist, title))
    return tracks


def parse_m3u_playlist(path: Path) -> List[Tuple[str, str]]:
    """Parse an M3U playlist and return (artist, title) tuples."""
    tracks = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            meta = line.split(",", 1)
            if len(meta) < 2:
                continue
            track_info = meta[1].strip()
            if " - " in track_info:
                artist, title = track_info.split(" - ", 1)
                tracks.append((artist.strip(), title.strip()))
            else:
                tracks.append(("", track_info))
    return tracks


def parse_playlist(path: Path) -> List[Tuple[str, str]]:
    """Auto-detect format and parse playlist."""
    if path.suffix.lower() == ".m3u":
        return parse_m3u_playlist(path)
    else:
        return parse_txt_playlist(path)


# ── Sync Logic ───────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, "-")
    name = name.lstrip(".- ")
    return name.strip(". ")


def format_track_number(i: int) -> str:
    return str(i).zfill(2)


def sync_playlist(
    playlist_name: str,
    tracks: List[Tuple[str, str]],
    index: List[Tuple[str, Path]],
    library_dir: Path,
    download_dir: Path,
    dry_run: bool,
) -> dict:
    """Sync a single playlist: find or download tracks, copy to categorized folder."""
    category = categorize_playlist(playlist_name)
    playlist_dir = library_dir / category / sanitize_filename(playlist_name)

    report = {
        "playlist": playlist_name,
        "category": category,
        "dir": str(playlist_dir),
        "matched": 0,
        "downloaded": 0,
        "unmatched": [],
        "removed": 0,
        "created": 0,
    }

    current_files: Dict[str, Path] = {}
    if playlist_dir.exists():
        for f in playlist_dir.iterdir():
            if f.is_file() or f.is_symlink():
                current_files[f.name] = f

    new_files: set = set()

    for i, (artist, title) in enumerate(tracks, start=1):
        num = format_track_number(i)
        match_path = find_best_match(artist, title, index)
        
        if not match_path and not dry_run:
            match_path = download_from_youtube(artist, title, download_dir)
            if match_path:
                report["downloaded"] += 1
                index.append((match_path.stem, match_path))
        
        if match_path:
            ext = match_path.suffix
            safe_title = sanitize_filename(title) or "Unknown"
            new_name = f"{num}-{safe_title}{ext}"
            new_files.add(new_name)

            dest_path = playlist_dir / new_name

            if dry_run:
                if not dest_path.exists():
                    report["created"] += 1
            else:
                playlist_dir.mkdir(parents=True, exist_ok=True)

                if dest_path.exists() or dest_path.is_symlink():
                    real_dest = dest_path.resolve()
                    if real_dest != match_path.resolve():
                        if dest_path.is_symlink() or dest_path.is_file():
                            dest_path.unlink()

                if not dest_path.exists():
                    shutil.copyfile(str(match_path), str(dest_path))
                    report["created"] += 1

            report["matched"] += 1
        else:
            report["unmatched"].append(f"{num}- {artist} - {title}")

    for old_name, old_path in current_files.items():
        if old_name not in new_files:
            if dry_run:
                report["removed"] += 1
            else:
                old_path.unlink()
                report["removed"] += 1

    if not dry_run and playlist_dir.exists() and not any(playlist_dir.iterdir()):
        playlist_dir.rmdir()

    return report


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Sync playlists with YouTube download fallback (no iTunes needed)"
    )
    parser.add_argument("--playlist", type=Path, help="Path to a single playlist file (.txt or .m3u)")
    parser.add_argument("--playlist-dir", type=Path, default=DEFAULT_PLAYLIST_DIR, help="Directory with playlist files")
    parser.add_argument("--library-dir", type=Path, default=DEFAULT_LIBRARY_DIR, help="Root of your music library")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR, help="Where to save downloaded tracks")
    parser.add_argument("--apply", action="store_true", help="Actually create/update folders (default: dry-run)")
    args = parser.parse_args()

    dry_run = not args.apply
    mode = "DRY RUN" if dry_run else "APPLY"

    playlist_files = []
    if args.playlist:
        if not args.playlist.exists():
            print(f"Playlist not found: {args.playlist}")
            sys.exit(1)
        playlist_files = [args.playlist]
    else:
        if not args.playlist_dir.exists():
            print(f"Playlist directory not found: {args.playlist_dir}")
            print("Create it and add .txt or .m3u files, or use --playlist for a single file.")
            sys.exit(1)
        playlist_files = sorted(args.playlist_dir.glob("*.txt")) + sorted(args.playlist_dir.glob("*.m3u"))
        if not playlist_files:
            print(f"No .txt or .m3u files found in: {args.playlist_dir}")
            sys.exit(0)

    print(f"\n{'='*70}")
    print(f"  Playlist Sync with YouTube Download — {mode}")
    print(f"  Library:    {args.library_dir}")
    print(f"  Downloads:  {args.download_dir}")
    print(f"  Playlists:  {len(playlist_files)}")
    print(f"{'='*70}\n")

    index = build_library_index(args.library_dir)

    total_matched = 0
    total_downloaded = 0
    total_unmatched = 0
    total_created = 0
    total_removed = 0

    for playlist_path in playlist_files:
        playlist_name = playlist_path.stem
        tracks = parse_playlist(playlist_path)
        category = categorize_playlist(playlist_name)
        print(f"  [{category}] {playlist_name}  ({len(tracks)} tracks)")

        report = sync_playlist(
            playlist_name=playlist_name,
            tracks=tracks,
            index=index,
            library_dir=args.library_dir,
            download_dir=args.download_dir,
            dry_run=dry_run,
        )

        total_matched += report["matched"]
        total_downloaded += report["downloaded"]
        total_unmatched += len(report["unmatched"])
        total_created += report["created"]
        total_removed += report["removed"]

        if report["downloaded"]:
            print(f"    📥 Downloaded: {report['downloaded']} tracks")
        if report["unmatched"]:
            print(f"    ⚠ Unmatched ({len(report['unmatched'])}) -- could not find or download:")
            for u in report["unmatched"][:5]:
                print(f"       {u}")
            if len(report["unmatched"]) > 5:
                print(f"       ... and {len(report['unmatched']) - 5} more")

    print(f"\n{'='*70}")
    print(f"  Summary")
    print(f"{'='*70}")
    print(f"  Matched:     {total_matched}")
    print(f"  Downloaded:  {total_downloaded}")
    print(f"  Unmatched:   {total_unmatched}")
    print(f"  Created:     {total_created}")
    print(f"  Removed:     {total_removed}")

    if dry_run:
        print(f"\n  This was a DRY RUN. No files were modified.")
        print(f"  Run with --apply to download missing tracks and create/update folders.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
