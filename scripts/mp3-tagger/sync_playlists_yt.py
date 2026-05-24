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

import re
import sys
import shutil
import argparse
import subprocess
import json
import csv
from pathlib import Path
from typing import Optional, List, Tuple, Dict
from difflib import SequenceMatcher
from collections import defaultdict

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_LIBRARY_DIRS = [
    Path("/mnt/c/Users/david/iCloudDrive/1-Storage/1-Libraries/3-Music"),
    Path("/mnt/c/Users/david/Music/iTunes/iTunes Media/Music"),
]
DEFAULT_DOWNLOAD_DIR = Path("/mnt/c/Users/david/iCloudDrive/1-Storage/1-Libraries/3-Music/0-To Check")
DEFAULT_PLAYLIST_DIR = Path.home() / "workspace" / "resources" / "playlists"

MATCH_THRESHOLD = 0.60
TITLE_STRICT_MIN = 0.72
ARTIST_STRICT_MIN = 0.45
YT_DLP_BIN = "yt-dlp"


def make_track_record(
    artist: str = "",
    title: str = "",
    album: str = "",
    playlist_name: str = "",
    track_type: str = "Playlist",
    isrc: str = "",
    apple_id: str = "",
) -> dict[str, str]:
    return {
        "artist": artist.strip(),
        "title": title.strip(),
        "album": album.strip(),
        "playlist_name": playlist_name.strip(),
        "type": track_type.strip() or "Playlist",
        "isrc": isrc.strip(),
        "apple_id": apple_id.strip(),
    }

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


def normalize_for_match(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\b(feat\.?|ft\.?)\b", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_modifiers(text: str) -> set[str]:
    lowered = (text or "").lower()
    known = {
        "remix", "slowed", "instrumental", "nightcore", "extended", "trap",
        "piano", "guitar", "acoustic", "cover", "version", "reverb",
    }
    found = set()
    for k in known:
        if re.search(rf"\b{re.escape(k)}\b", lowered):
            found.add(k)
    return found


def split_stem_artist_title(stem: str) -> Tuple[str, str]:
    parts = re.split(r"\s+-\s+", stem, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", stem.strip()


def build_library_index(library_dirs: List[Path]) -> List[Tuple[str, Path]]:
    """Index all audio files across one or more library roots."""
    files = []
    seen: set[str] = set()
    for library_dir in library_dirs:
        if not library_dir.exists():
            print(f"  Skipping missing library dir: {library_dir}")
            continue
        print(f"  Indexing library: {library_dir}")
        for ext in ("*.mp3", "*.m4a", "*.flac", "*.ogg", "*.wma"):
            for f in library_dir.rglob(ext):
                f_str = str(f)
                if f_str in seen:
                    continue
                seen.add(f_str)
                files.append(f)
    
    index = []
    for f in files:
        stem = f.stem
        stem = re.sub(r'\s*\[[A-Za-z0-9_-]{11}\]$', '', stem)
        stem = re.sub(r'\s+', ' ', stem).strip()
        index.append((stem, f))
    
    print(f"  Indexed {len(index)} tracks")
    return index


def extract_isrc_tag(file_path: Path) -> str:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format_tags=isrc,ISRC",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        if result.returncode != 0:
            return ""
        value = (result.stdout or "").strip().splitlines()
        if not value:
            return ""
        return value[0].strip().upper()
    except Exception:
        return ""


def build_isrc_index(index: List[Tuple[str, Path]], wanted_isrcs: set[str]) -> Dict[str, Path]:
    """Build exact ISRC -> path map for required ISRCs only."""
    if not wanted_isrcs:
        return {}

    print(f"  Building ISRC index for {len(wanted_isrcs)} requested codes")
    remaining = set(x.strip().upper() for x in wanted_isrcs if x.strip())
    by_isrc: Dict[str, Path] = {}

    for _, path in index:
        if not remaining:
            break
        code = extract_isrc_tag(path)
        if not code:
            continue
        if code in remaining and code not in by_isrc:
            by_isrc[code] = path
            remaining.remove(code)

    print(f"  ISRC exact matches in library: {len(by_isrc)}")
    return by_isrc


def find_best_match(artist: str, title: str, index: List[Tuple[str, Path]]) -> Optional[Path]:
    """Find the best matching audio file by comparing against filenames."""
    if not title.strip():
        return None

    title_norm = normalize_for_match(title)
    artist_norm = normalize_for_match(artist)
    wanted_mods = extract_modifiers(title)

    best_path = None
    best_score = 0.0

    for stem, path in index:
        cand_artist, cand_title = split_stem_artist_title(stem)
        cand_title_norm = normalize_for_match(cand_title)
        cand_artist_norm = normalize_for_match(cand_artist)
        stem_norm = normalize_for_match(stem)
        stem_mods = extract_modifiers(stem)

        if wanted_mods and not wanted_mods.issubset(stem_mods):
            continue

        if title_norm:
            if artist_norm:
                title_score = fuzzy_ratio(title_norm, cand_title_norm or stem_norm)
            else:
                title_score = max(
                    fuzzy_ratio(title_norm, cand_title_norm or stem_norm),
                    fuzzy_ratio(title_norm, stem_norm),
                )
        else:
            title_score = 0.0
        if title_score < TITLE_STRICT_MIN:
            continue

        artist_score = 1.0
        if artist_norm:
            artist_score = fuzzy_ratio(artist_norm, cand_artist_norm or stem_norm)
            if artist_score < ARTIST_STRICT_MIN and artist_norm not in stem_norm:
                continue

        score = (title_score * 0.85) + (artist_score * 0.15)
        if title_norm and title_norm in (cand_title_norm or stem_norm):
            score += 0.05
        if artist_norm and artist_norm in (cand_artist_norm or stem_norm):
            score += 0.03

        if score > best_score and score >= MATCH_THRESHOLD:
            best_score = score
            best_path = path

    return best_path


# ── YouTube Download ─────────────────────────────────────────────────────────

def download_from_youtube(
    artist: str,
    title: str,
    download_dir: Path,
    album: str = "",
    isrc: str = "",
) -> Optional[Path]:
    """Search YouTube and download the best audio-first match as MP3."""
    query_parts = [artist.strip(), title.strip()]
    if album.strip():
        query_parts.append(album.strip())
    query = " ".join([p for p in query_parts if p]).strip()
    search_query = f"ytsearch12:{query} audio"

    safe_name = sanitize_filename(query)
    output_template = str(download_dir / f"{safe_name}.%(ext)s")

    print(f"    🔍 Searching YouTube: {query}")
    selected_id = None
    selected_title = ""

    probe_cmd = [
        YT_DLP_BIN,
        search_query,
        "--dump-single-json",
        "--no-warnings",
        "--quiet",
        "--skip-download",
    ]

    try:
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=90)
        if probe.returncode == 0 and probe.stdout.strip():
            payload = json.loads(probe.stdout)
            entries = payload.get("entries", []) if isinstance(payload, dict) else []

            title_norm = normalize_for_match(title)
            artist_norm = normalize_for_match(artist)
            target_norm = normalize_for_match(query)
            album_norm = normalize_for_match(album)

            def score_entry(entry: dict) -> float:
                e_title = str(entry.get("title") or "")
                e_uploader = str(entry.get("uploader") or "")
                e_channel = str(entry.get("channel") or "")
                duration = entry.get("duration") or 0

                t_norm = normalize_for_match(e_title)
                u_norm = normalize_for_match(f"{e_uploader} {e_channel}")

                score = 0.0
                score += fuzzy_ratio(target_norm, t_norm) * 0.55
                score += fuzzy_ratio(title_norm, t_norm) * 0.30
                if artist_norm:
                    score += fuzzy_ratio(artist_norm, u_norm) * 0.10
                    if artist_norm in t_norm:
                        score += 0.08
                if album_norm and album_norm in t_norm:
                    score += 0.10

                lowered = f"{e_title} {e_uploader} {e_channel}".lower()

                if "topic" in lowered or "provided to youtube" in lowered:
                    score += 0.30
                if "official audio" in lowered or "audio" in lowered:
                    score += 0.10

                if "official video" in lowered or "music video" in lowered or "mv" in lowered:
                    score -= 0.35
                if "lyric video" in lowered or "lyrics" in lowered:
                    score -= 0.12
                if "live" in lowered:
                    score -= 0.20

                if isinstance(duration, (int, float)) and duration > 0:
                    if duration < 70:
                        score -= 0.35
                    elif duration < 110:
                        score -= 0.18
                    elif duration > 520:
                        score -= 0.18

                return score

            ranked = sorted(
                (
                    (score_entry(e), e)
                    for e in entries
                    if isinstance(e, dict) and e.get("id")
                ),
                key=lambda x: x[0],
                reverse=True,
            )

            if ranked:
                selected = ranked[0][1]
                selected_id = selected.get("id")
                selected_title = str(selected.get("title") or "")
    except Exception:
        selected_id = None

    if selected_id:
        print(f"      ↳ Selected: {selected_title}")
        source = f"https://www.youtube.com/watch?v={selected_id}"
    else:
        source = search_query

    cmd = [
        YT_DLP_BIN,
        source,
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

def parse_txt_playlist(path: Path) -> List[dict[str, str]]:
    """Parse text playlist lines as: Artist - Title || Album: Name (album optional)."""
    tracks = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            if re.match(r"^\d{1,2}:\d{2}\s+-\s+", line):
                line = re.sub(r"^\d{1,2}:\d{2}\s+-\s+", "", line, count=1)

            album = ""
            if " || Album: " in line:
                line, album = line.split(" || Album: ", 1)
                album = album.strip()

            if " - " in line:
                artist, title = line.split(" - ", 1)
                artist = artist.strip()
                title = title.strip()
            else:
                artist = ""
                title = line.strip()
            
            tracks.append(make_track_record(artist=artist, title=title, album=album))
    return tracks


def parse_m3u_playlist(path: Path) -> List[dict[str, str]]:
    """Parse an M3U playlist and return (artist, title, album) tuples."""
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
                tracks.append(make_track_record(artist=artist, title=title))
            else:
                tracks.append(make_track_record(title=track_info))
    return tracks


def parse_csv_playlist(path: Path) -> List[dict[str, str]]:
    """Parse Apple-style CSV and return track records with all fields."""
    tracks: List[dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            title = (row.get("Track name") or "").strip()
            artist = (row.get("Artist name") or "").strip()
            album = (row.get("Album") or "").strip()
            playlist_name = (row.get("Playlist name") or "").strip()
            track_type = (row.get("Type") or "Playlist").strip()
            isrc = (row.get("ISRC") or "").strip()
            apple_id = (row.get("Apple - id") or "").strip()
            if title:
                tracks.append(
                    make_track_record(
                        artist=artist,
                        title=title,
                        album=album,
                        playlist_name=playlist_name,
                        track_type=track_type,
                        isrc=isrc,
                        apple_id=apple_id,
                    )
                )
    return tracks


def parse_playlist(path: Path) -> List[dict[str, str]]:
    """Auto-detect format and parse playlist."""
    suffix = path.suffix.lower()
    if suffix == ".m3u":
        return parse_m3u_playlist(path)
    if suffix == ".csv":
        return parse_csv_playlist(path)
    else:
        return parse_txt_playlist(path)


# ── Sync Logic ───────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    illegal = '<>:"/\\|?*'
    for ch in illegal:
        name = name.replace(ch, "-")
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s*-+\s*$", "", name)
    name = re.sub(r"-+", "-", name)
    name = name.lstrip(".- ")
    return name.strip(". ")


def clean_title_for_filename(title: str) -> str:
    """Ensure filename is only track title (never artist/album metadata)."""
    t = title.strip()
    if " || Album: " in t:
        t = t.split(" || Album: ", 1)[0].strip()
    return sanitize_filename(t)


def enforce_mp3_metadata(
    file_path: Path,
    artist: str,
    title: str,
    album: str = "",
    playlist_name: str = "",
    track_type: str = "",
    isrc: str = "",
    apple_id: str = "",
) -> None:
    if not file_path.exists() or file_path.suffix.lower() != ".mp3":
        return
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        str(file_path),
        "-codec",
        "copy",
        "-metadata",
        f"title={title}",
        "-metadata",
        f"artist={artist}",
        "-metadata",
        f"album_artist={artist}",
        "-metadata",
        f"album={album}",
        "-metadata",
        f"isrc={isrc}",
        "-metadata",
        f"grouping={playlist_name}",
        "-metadata",
        f"comment=Playlist={playlist_name}; Type={track_type}; AppleID={apple_id}; ISRC={isrc}",
        str(file_path.with_suffix(".tagging.tmp.mp3")),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=True)
        tagged = file_path.with_suffix(".tagging.tmp.mp3")
        tagged.replace(file_path)
    except Exception:
        tmp = file_path.with_suffix(".tagging.tmp.mp3")
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def format_track_number(i: int) -> str:
    return str(i).zfill(2)


def sync_playlist(
    playlist_name: str,
    tracks: List[dict[str, str]],
    index: List[Tuple[str, Path]],
    isrc_index: Dict[str, Path],
    library_dirs: List[Path],
    target_dir: Path,
    download_dir: Path,
    dry_run: bool,
    number_prefix: bool,
) -> dict:
    """Sync a single playlist: find or download tracks, copy to categorized folder."""
    category = categorize_playlist(playlist_name)
    playlist_dir = target_dir / category / sanitize_filename(playlist_name)

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

    for i, track in enumerate(tracks, start=1):
        artist = track.get("artist", "")
        title = track.get("title", "")
        album = track.get("album", "")
        track_playlist_name = track.get("playlist_name", playlist_name) or playlist_name
        track_type = track.get("type", "Playlist")
        isrc = track.get("isrc", "")
        apple_id = track.get("apple_id", "")
        num = format_track_number(i)
        downloaded_now = False
        match_path = None
        if isrc:
            match_path = isrc_index.get(isrc.strip().upper())

        if not match_path:
            match_path = find_best_match(artist, title, index)
        
        if not match_path and not dry_run:
            match_path = download_from_youtube(artist, title, download_dir, album=album, isrc=isrc)
            if match_path:
                report["downloaded"] += 1
                downloaded_now = True
                index.append((match_path.stem, match_path))
        
        if match_path:
            ext = match_path.suffix
            safe_title = clean_title_for_filename(title) or "Unknown"
            if number_prefix:
                new_name = f"{num}-{safe_title}{ext}"
            else:
                new_name = f"{safe_title}{ext}"
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
                    if downloaded_now and artist and title:
                        enforce_mp3_metadata(
                            dest_path,
                            artist=artist,
                            title=title,
                            album=album,
                            playlist_name=track_playlist_name,
                            track_type=track_type,
                            isrc=isrc,
                            apple_id=apple_id,
                        )
                    report["created"] += 1

            report["matched"] += 1
        else:
            if album:
                report["unmatched"].append(f"{num}- {artist} - {title} || Album: {album}")
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
    parser.add_argument("--playlist", type=Path, help="Path to a single playlist file (.txt, .m3u, .csv)")
    parser.add_argument("--playlist-dir", type=Path, default=DEFAULT_PLAYLIST_DIR, help="Directory with playlist files")
    parser.add_argument(
        "--library-dir",
        type=Path,
        action="append",
        default=None,
        help="Music library root. Repeat to include multiple roots. Defaults to iCloud + iTunes paths.",
    )
    parser.add_argument("--target-dir", type=Path, default=None, help="Destination root for generated playlist folders (defaults to --library-dir)")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR, help="Where to save downloaded tracks")
    parser.add_argument("--apply", action="store_true", help="Actually create/update folders (default: dry-run)")
    parser.add_argument("--no-number-prefix", action="store_true", help="Do not prefix output filenames with track numbers")
    args = parser.parse_args()

    library_dirs = args.library_dir if args.library_dir else list(DEFAULT_LIBRARY_DIRS)
    target_dir = args.target_dir or library_dirs[0]

    dry_run = not args.apply
    mode = "DRY RUN" if dry_run else "APPLY"
    number_prefix = not args.no_number_prefix

    playlist_files = []
    if args.playlist:
        if not args.playlist.exists():
            print(f"Playlist not found: {args.playlist}")
            sys.exit(1)
        playlist_files = [args.playlist]
    else:
        if not args.playlist_dir.exists():
            print(f"Playlist directory not found: {args.playlist_dir}")
            print("Create it and add .txt, .m3u, or .csv files, or use --playlist for a single file.")
            sys.exit(1)
        raw_files = (
            sorted(args.playlist_dir.rglob("*.txt"))
            + sorted(args.playlist_dir.rglob("*.m3u"))
            + sorted(args.playlist_dir.rglob("*.csv"))
        )
        dedup: Dict[str, Path] = {}
        for p in raw_files:
            key = p.stem.strip().lower()
            prev = dedup.get(key)
            if prev is None:
                dedup[key] = p
                continue
            prev_depth = len(prev.relative_to(args.playlist_dir).parts)
            new_depth = len(p.relative_to(args.playlist_dir).parts)
            if new_depth > prev_depth:
                dedup[key] = p
        playlist_files = sorted(dedup.values())
        if not playlist_files:
            print(f"No .txt, .m3u, or .csv files found in: {args.playlist_dir}")
            sys.exit(0)

    print(f"\n{'='*70}")
    print(f"  Playlist Sync with YouTube Download — {mode}")
    print(f"  Library:    {', '.join(str(x) for x in library_dirs)}")
    print(f"  Target:     {target_dir}")
    print(f"  Downloads:  {args.download_dir}")
    print(f"  Playlists:  {len(playlist_files)}")
    print(f"{'='*70}\n")

    index = build_library_index(library_dirs)

    total_matched = 0
    total_downloaded = 0
    total_unmatched = 0
    total_created = 0
    total_removed = 0

    parsed_playlists: List[Tuple[Path, str, List[dict[str, str]]]] = []
    wanted_isrcs: set[str] = set()
    for playlist_path in playlist_files:
        playlist_name = playlist_path.stem
        tracks = parse_playlist(playlist_path)
        if playlist_path.suffix.lower() == ".csv":
            grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
            for t in tracks:
                grouped_name = (t.get("playlist_name", "") or "").strip() or playlist_name
                grouped[grouped_name].append(t)
            if grouped:
                for g_name, g_tracks in grouped.items():
                    for t in g_tracks:
                        code = (t.get("isrc", "") or "").strip().upper()
                        if code:
                            wanted_isrcs.add(code)
                    parsed_playlists.append((playlist_path, g_name, g_tracks))
                continue

        if tracks:
            csv_playlist_name = tracks[0].get("playlist_name", "")
            if csv_playlist_name:
                playlist_name = csv_playlist_name
            for t in tracks:
                code = (t.get("isrc", "") or "").strip().upper()
                if code:
                    wanted_isrcs.add(code)
        parsed_playlists.append((playlist_path, playlist_name, tracks))

    isrc_index = build_isrc_index(index, wanted_isrcs)

    for playlist_path, playlist_name, tracks in parsed_playlists:
        category = categorize_playlist(playlist_name)
        print(f"  [{category}] {playlist_name}  ({len(tracks)} tracks)")

        report = sync_playlist(
            playlist_name=playlist_name,
            tracks=tracks,
            index=index,
            isrc_index=isrc_index,
            library_dirs=library_dirs,
            target_dir=target_dir,
            download_dir=args.download_dir,
            dry_run=dry_run,
            number_prefix=number_prefix,
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
