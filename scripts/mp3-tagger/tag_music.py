#!/usr/bin/env python3
"""
MP3 Metadata Tagger for Music Library

Scans a folder tree of MP3s and writes consistent ID3 tags inferred from
file paths and filenames. Designed for libraries organized by source
(Anime, Series, Gaming, Genre) where folder names encode franchise and
style information.

Tag Schema (5 tags):
  Title    – Clean song name extracted from the filename.
  Artist   – Franchise for soundtracks; performer for genre folders.
  Album    – Franchise / sub-collection (e.g. "Death Note - OST I").
  Genre    – One of 8 musical styles (see Genre Taxonomy below).
  Grouping – One of 7 version treatments (see Grouping Taxonomy below).

Genre Taxonomy:
  Epic / Orchestral  – Cinematic, dramatic, orchestral themes.
  Metal              – Heavy, aggressive guitar (DOOM, Rammstein, etc.).
  Rock               – Guitar-driven but non-metal.
  Hip Hop            – Rap, trap, phonk.
  Electronic         – Synth, cyberpunk, nightcore, EDM.
  Ambient / Chill    – Slowed + Reverb, atmospheric, lo-fi.
  Pop                – Mainstream, dance, Latin pop.
  Soundtrack         – Unmodified original OST score.

Grouping Taxonomy:
  Original     – Unmodified release.
  Cover        – Guitar, piano, or instrumental cover.
  Remix        – Mashup, AI cover, reimagined, VS mix.
  Slowed       – Slowed, Slowed + Reverb, slowed to perfection.
  Epic Version – Epic orchestra / remix (not slowed).
  Instrumental – Instrumental version of a vocal track.
  Other        – Speeches, special edits.

Classification Rules:
  • "Slowed + Reverb" ALWAYS overrides genre → Ambient / Chill.
  • "Epic Orchestra" maps to Epic / Orchestral unless slowed.
  • Filename "Artist - Title (Version).mp3" is parsed for genre folders.
  • Filename "Title - Subtitle (Version).mp3" is parsed for soundtrack folders.
  • YouTube IDs [xxxxx] and stylized unicode are stripped automatically.

Dependencies:
  pip install mutagen

Usage:
  # Preview what would change (dry-run, default)
  python3 tag_music.py

  # Write tags to all MP3s under BASE_DIR
  python3 tag_music.py --apply

  # Process a different library path
  python3 tag_music.py --apply --base-dir /path/to/music

The script is idempotent — safe to re-run after adding new files.
"""

import os
import re
import sys
import argparse
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT1, TIT2, TPE1, TALB, TCON

BASE_DIR = Path("/mnt/c/Users/david/iCloudDrive/1-Storage/1-Libraries/3-Music")

GENRE_RULES = [
    (r'(?i)(slowed|reverb|slowed\s+to\s+perfection)', 'Ambient / Chill'),
    (r'(?i)(metal\s*cover|heavy\s*metal|doom|berserk|rammstein|disturbed)', 'Metal'),
    (r'(?i)(\bepic\b|orchestral|cinematic|dramatic)', 'Epic / Orchestral'),
    (r'(?i)(\brap\b|hip\s*hop|phonk|trap\s*beat|trap\b|gangsta)', 'Hip Hop'),
    (r'(?i)(electronic|synth|cyberpunk|nightcore|edm\b)', 'Electronic'),
    (r'(?i)(\brock\b|alternative|grunge|indie\s*rock)', 'Rock'),
    (r'(?i)(\bpop\b|dance|latino|reggaeton)', 'Pop'),
    (r'(?i)(\bost\b|original\s+soundtrack|score)', 'Soundtrack'),
]

FOLDER_GENRE_FALLBACK = {
    '4-Hard Rock & Metal': 'Metal',
    '6-Rap & Hip Hop': 'Hip Hop',
    '5-Electronic': 'Electronic',
    '3-Rock, Soul, Blues & Folk': 'Rock',
    '7-Latino': 'Pop',
    '2-Soundtracks & Themes': 'Soundtrack',
}

GROUPING_RULES = [
    (r'(?i)(slowed|reverb|slowed\s+to\s+perfection)', 'Slowed'),
    (r'(?i)(guitar\s*cover|instrumental\s*cover|piano\s*cover|\bcover\b)', 'Cover'),
    (r'(?i)(remix|mashup|ai\s*cover|reimagined|vs\s+|remake|\bmix\b)', 'Remix'),
    (r'(?i)(\binstrumental\b)', 'Instrumental'),
    (r'(?i)(epic\s+version|epic\s+orchestra)', 'Epic Version'),
]


def clean_folder_name(name: str) -> str:
    return re.sub(r'^\d+-', '', name).strip()


def normalize_for_matching(filename: str) -> str:
    name = filename[:-4] if filename.lower().endswith('.mp3') else filename
    name = re.sub(r'\s*\[[A-Za-z0-9_-]{11}\]\s*', ' ', name)
    name = unicodedata.normalize('NFKC', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def parse_filename(filename: str, soundtrack_mode: bool = False) -> Tuple[Optional[str], str, Optional[str]]:
    name = normalize_for_matching(filename)

    if not soundtrack_mode:
        match = re.match(r'^(.+?)\s+-\s+(.+?)(?:\s+\(([^)]+)\))?$', name)
        if match:
            artist, title, version = match.groups()
            return artist.strip(), title.strip(), version.strip() if version else None
    else:
        match = re.match(r'^(.+?)\s+-\s+(.+?)(?:\s+\(([^)]+)\))?$', name)
        if match:
            title, subtitle, version = match.groups()
            return None, title.strip(), version.strip() if version else None

    match = re.match(r'^\d+\.?\s+(.+?)(?:\s+\(([^)]+)\))?$', name)
    if match:
        title, version = match.groups()
        return None, title.strip(), version.strip() if version else None

    match = re.match(r'^(.+?)(?:\s+\(([^)]+)\))?$', name)
    if match:
        title, version = match.groups()
        return None, title.strip(), version.strip() if version else None

    return None, name, None


def infer_from_path(rel_path: Path) -> Tuple[Optional[str], Optional[str], str]:
    parts = list(rel_path.parts)
    if not parts:
        return None, None, ''

    top_folder = parts[0]
    folder_genre = FOLDER_GENRE_FALLBACK.get(top_folder, '')

    if 'Soundtracks' in top_folder:
        if len(parts) >= 4:
            franchise = clean_folder_name(parts[2])
            album_folder = clean_folder_name(parts[3])
            if album_folder.lower() in ('other',):
                album = franchise
            else:
                album = f"{franchise} - {album_folder}" if album_folder != franchise else franchise
            return franchise, album, folder_genre
        elif len(parts) == 3:
            franchise = clean_folder_name(parts[2])
            return franchise, franchise, folder_genre

    if len(parts) >= 3:
        subfolder = clean_folder_name(parts[2])
        return None, subfolder, folder_genre

    return None, None, folder_genre


def classify_genre(filename: str, folder_hint: str) -> str:
    for pattern, genre in GENRE_RULES:
        if re.search(pattern, filename):
            return genre
    return folder_hint if folder_hint else 'Other'


def classify_grouping(filename: str) -> str:
    for pattern, grouping in GROUPING_RULES:
        if re.search(pattern, filename):
            return grouping
    return 'Original'


def ensure_id3(mp3_path: Path):
    audio = MP3(str(mp3_path))
    if audio.tags is None:
        audio.add_tags()
    else:
        try:
            audio.tags.update_to_v23()
        except Exception:
            pass
    return audio


def apply_tags(mp3_path: Path, title: str, artist: str, album: str, genre: str, grouping: str, dry_run: bool) -> dict:
    audio = MP3(str(mp3_path))
    current = {}
    if audio.tags:
        current = {
            'title': str(audio.tags.get('TIT2', '')),
            'artist': str(audio.tags.get('TPE1', '')),
            'album': str(audio.tags.get('TALB', '')),
            'genre': str(audio.tags.get('TCON', '')),
            'grouping': str(audio.tags.get('TIT1', '')),
        }

    proposed = {
        'title': title,
        'artist': artist,
        'album': album,
        'genre': genre,
        'grouping': grouping,
    }

    if not dry_run:
        audio = ensure_id3(mp3_path)
        audio.tags['TIT2'] = TIT2(encoding=3, text=title)
        audio.tags['TPE1'] = TPE1(encoding=3, text=artist)
        audio.tags['TALB'] = TALB(encoding=3, text=album)
        audio.tags['TCON'] = TCON(encoding=3, text=genre)
        audio.tags['TIT1'] = TIT1(encoding=3, text=grouping)
        audio.save(v2_version=3)

    return {'path': str(mp3_path), 'current': current, 'proposed': proposed}


def process_library(base_dir: Path, dry_run: bool = True) -> list:
    results = []
    mp3_files = sorted(base_dir.rglob('*.mp3'))

    for mp3_path in mp3_files:
        rel_path = mp3_path.relative_to(base_dir)
        filename = mp3_path.name

        path_artist, path_album, folder_genre = infer_from_path(rel_path)

        is_soundtrack = 'Soundtracks' in str(rel_path)
        parsed_artist, parsed_title, parsed_version = parse_filename(filename, soundtrack_mode=is_soundtrack)

        if is_soundtrack:
            artist = path_artist or 'Various'
            album = path_album or 'Various'
            title = parsed_title or 'Unknown'
        else:
            artist = parsed_artist or path_artist or 'Various'
            album = path_album or parsed_artist or 'Various'
            title = parsed_title or 'Unknown'

        normalized = normalize_for_matching(filename)
        genre = classify_genre(normalized, folder_genre)
        grouping = classify_grouping(normalized)

        result = apply_tags(mp3_path, title, artist, album, genre, grouping, dry_run)
        results.append(result)

    return results


def print_report(results: list, dry_run: bool):
    mode = "DRY RUN (no files modified)" if dry_run else "APPLIED"
    print(f"\n{'='*80}")
    print(f"  MP3 Metadata Tagger — {mode}")
    print(f"  Total files processed: {len(results)}")
    print(f"{'='*80}\n")

    genre_counts = {}
    grouping_counts = {}
    changed = 0
    unchanged = 0

    for r in results:
        p = r['proposed']
        c = r['current']
        genre_counts[p['genre']] = genre_counts.get(p['genre'], 0) + 1
        grouping_counts[p['grouping']] = grouping_counts.get(p['grouping'], 0) + 1

        if p != c:
            changed += 1
        else:
            unchanged += 1

    print("─ Genre Distribution ─")
    for g, count in sorted(genre_counts.items(), key=lambda x: -x[1]):
        print(f"  {g:20s} : {count:3d} files")

    print("\n─ Grouping Distribution ─")
    for g, count in sorted(grouping_counts.items(), key=lambda x: -x[1]):
        print(f"  {g:20s} : {count:3d} files")

    print(f"\n─ Changes ─")
    print(f"  Files with new/modified tags: {changed}")
    print(f"  Files already matching:       {unchanged}")

    print(f"\n─ Sample Changes (first 20) ─")
    shown = 0
    for r in results:
        if shown >= 20:
            break
        p = r['proposed']
        c = r['current']
        if p == c:
            continue
        shown += 1
        print(f"\n  File: .../{Path(r['path']).name}")
        for key in ['title', 'artist', 'album', 'genre', 'grouping']:
            old = c.get(key, '') or '(empty)'
            new = p[key]
            if old != new:
                print(f"    {key:10s}: {old:30s} → {new}")

    if dry_run:
        print(f"\n{'='*80}")
        print("  This was a DRY RUN. No files were modified.")
        print("  To apply these changes, run with --apply")
        print(f"{'='*80}")


def main():
    parser = argparse.ArgumentParser(description='Tag MP3 library with consistent metadata')
    parser.add_argument('--apply', action='store_true', help='Actually write tags to files (default: dry-run)')
    parser.add_argument('--base-dir', type=Path, default=BASE_DIR, help='Base music directory')
    args = parser.parse_args()

    dry_run = not args.apply
    results = process_library(args.base_dir, dry_run=dry_run)
    print_report(results, dry_run)


if __name__ == '__main__':
    main()
