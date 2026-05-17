#!/usr/bin/env python3
"""
iTunes Library Playlist Exporter — WSL-compatible alternative to TuneLift.

Reads the iTunes Music Library XML file and exports all playlists as M3U files.
Works entirely in WSL/Linux without needing Windows console interop.

Usage:
    python3 export_itunes_playlists.py
    python3 export_itunes_playlists.py --library "/path/to/iTunes Music Library.xml"
    python3 export_itunes_playlists.py --output "/path/to/output/dir"
"""

import os
import re
import sys
import argparse
import plistlib
from pathlib import Path
from urllib.parse import unquote

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_LIBRARY = Path("/mnt/c/Users/david/Music/iTunes/iTunes Music Library.xml")
DEFAULT_OUTPUT = Path("/mnt/c/Users/david/Music/iTunes/Playlist Exports")


def parse_itunes_xml(library_path: Path) -> tuple:
    """Parse iTunes XML and return (tracks_dict, playlists_list)."""
    print(f"  Reading iTunes library: {library_path}")
    with open(library_path, "rb") as f:
        data = plistlib.load(f)
    
    tracks = data.get("Tracks", {})
    playlists = data.get("Playlists", [])
    print(f"  Found {len(tracks)} tracks, {len(playlists)} playlists")
    return tracks, playlists


def decode_location(location: str) -> str:
    """Convert iTunes file:// URL to local filesystem path."""
    if location.startswith("file://localhost/"):
        path = location[len("file://localhost/"):]
    elif location.startswith("file://"):
        path = location[len("file://"):]
    else:
        path = location
    
    # URL-decode percent-encoded characters
    path = unquote(path)
    
    # Convert Windows path to WSL path
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        rest = path[2:].replace("\\", "/")
        path = f"/mnt/{drive}{rest}"
    
    return path


def build_track_index(tracks: dict) -> dict:
    """Build a lookup dict keyed by Track ID."""
    return {
        str(tid): {
            "name": track.get("Name", ""),
            "artist": track.get("Artist", ""),
            "album": track.get("Album", ""),
            "location": track.get("Location", ""),
            "duration": track.get("Total Time", 0),
        }
        for tid, track in tracks.items()
    }


def sanitize_filename(name: str) -> str:
    """Remove characters illegal in filenames."""
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, "-")
    return name.strip(". ")


def export_playlist(playlist: dict, track_index: dict, output_dir: Path) -> dict:
    """Export a single playlist as M3U. Returns a report dict."""
    name = playlist.get("Name", "Untitled")
    playlist_items = playlist.get("Playlist Items", [])
    
    report = {
        "name": name,
        "tracks": 0,
        "matched": 0,
        "unmatched": [],
    }
    
    if not playlist_items:
        return report
    
    # Skip internal playlists
    if playlist.get("Master", False) or playlist.get("Distinguished Kind"):
        return report
    
    m3u_lines = ["#EXTM3U"]
    
    for item in playlist_items:
        track_id = str(item.get("Track ID", ""))
        if track_id not in track_index:
            report["unmatched"].append(f"Track ID {track_id} not found")
            continue
        
        track = track_index[track_id]
        report["tracks"] += 1
        
        artist = track.get("artist", "")
        title = track.get("name", "")
        location = track.get("location", "")
        duration_ms = track.get("duration", 0)
        duration_sec = int(duration_ms / 1000) if duration_ms else 0
        
        if not location:
            report["unmatched"].append(f"{artist} - {title} (no location)")
            continue
        
        # Convert to local path
        local_path = decode_location(location)
        
        # Write EXTINF line
        m3u_lines.append(f"#EXTINF:{duration_sec},{artist} - {title}")
        m3u_lines.append(local_path)
        report["matched"] += 1
    
    # Only write if we have tracks
    if report["matched"] > 0:
        safe_name = sanitize_filename(name)
        m3u_path = output_dir / f"{safe_name}.m3u"
        m3u_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("\n".join(m3u_lines) + "\n")
        
        report["path"] = str(m3u_path)
    
    return report


def main():
    parser = argparse.ArgumentParser(description="Export iTunes playlists to M3U from XML library")
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY, help="Path to iTunes Music Library.xml")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory for M3U files")
    args = parser.parse_args()
    
    if not args.library.exists():
        print(f"Library not found: {args.library}")
        print("Make sure classic iTunes is installed and the XML library exists.")
        sys.exit(1)
    
    print(f"\n{'='*70}")
    print(f"  iTunes Playlist Exporter")
    print(f"  Library:  {args.library}")
    print(f"  Output:   {args.output}")
    print(f"{'='*70}\n")
    
    # Parse XML
    tracks, playlists = parse_itunes_xml(args.library)
    track_index = build_track_index(tracks)
    
    # Export each playlist
    exported = 0
    skipped = 0
    total_tracks = 0
    
    for playlist in playlists:
        name = playlist.get("Name", "")
        
        # Skip internal/system playlists
        if playlist.get("Master", False):
            skipped += 1
            continue
        if playlist.get("Distinguished Kind"):
            skipped += 1
            continue
        if not playlist.get("Playlist Items"):
            skipped += 1
            continue
        
        report = export_playlist(playlist, track_index, args.output)
        
        if report["matched"] > 0:
            print(f"  ✓ {name}: {report['matched']} tracks → {report.get('path', '')}")
            exported += 1
            total_tracks += report["matched"]
        else:
            print(f"  ○ {name}: empty or no valid tracks")
            skipped += 1
    
    print(f"\n{'='*70}")
    print(f"  Summary")
    print(f"  Exported:  {exported} playlists ({total_tracks} tracks)")
    print(f"  Skipped:   {skipped} playlists (empty/internal)")
    print(f"  Output:    {args.output}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
