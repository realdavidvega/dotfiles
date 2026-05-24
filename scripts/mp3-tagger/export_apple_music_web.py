#!/usr/bin/env python3
"""
Apple Music Web Scraper — Export playlists without API keys or developer account.

Uses Playwright to automate the Apple Music web app (music.apple.com),
navigate to your library playlists, and extract track data.

Prerequisites:
    pip install playwright
    playwright install chromium

Usage:
    python3 export_apple_music_web.py --output ~/playlists
    python3 export_apple_music_web.py --headless --output ~/playlists
"""

import re
import sys
import json
import csv
import argparse
from pathlib import Path
from typing import Any

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("Playwright not installed.")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)

DEFAULT_OUTPUT = Path.home() / "workspace" / "resources" / "playlists"
APPLE_MUSIC_URL = "https://music.apple.com"
STATE_FILE = Path.home() / ".config" / "playlist-sync" / "apple-music-state.json"
PERSISTENT_PROFILE_DIR = Path.home() / ".config" / "playlist-sync" / "chromium-profile"


def sanitize_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in name)
    safe = re.sub(r"\s+", " ", safe).strip()
    safe = re.sub(r"\s*-+\s*$", "", safe)
    safe = re.sub(r"-+", "-", safe)
    safe = safe.lstrip(".- ")
    return safe.strip(". ")


def is_duration_token(value: str) -> bool:
    return bool(re.match(r"^\d{1,2}:\d{2}(?::\d{2})?(?:\s*[-|]\s*)?$", value.strip()))


def clean_artist_value(value: str) -> str:
    v = (value or "").strip()
    if is_duration_token(v):
        return ""
    return v


def strip_duration_prefix(value: str) -> str:
    v = (value or "").strip()
    return re.sub(r"^\d{1,2}:\d{2}(?::\d{2})?\s*-\s*", "", v, count=1)


def deep_get_first_str(obj: Any, paths: list[tuple[str, ...]]) -> str:
    for path in paths:
        cur = obj
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                ok = False
                break
        if ok and cur is not None:
            text = str(cur).strip()
            if text:
                return text
    return ""


def wait_for_login(page, timeout_ms: int = 300000) -> bool:
    print("  Waiting for you to log in to Apple Music...")
    print("  Please sign in manually in the browser window.")

    try:
        page.wait_for_selector(
            '[data-testid="library-sidebar"] , [data-testid="your-library"] , .sidebar-library , [aria-label*="Library"]',
            timeout=timeout_ms,
        )
        print("  Logged in detected.")
        return True
    except PlaywrightTimeout:
        print("  Timeout: Login not detected. Please try again.")
        return False


def navigate_to_playlists(page) -> bool:
    print("  Navigating to Library -> Playlists...")

    try:
        lib = page.locator('text=Library').first
        if lib.count() > 0:
            lib.click()
            page.wait_for_timeout(2000)
    except Exception:
        pass

    try:
        pl = page.locator('text=Playlists').first
        if pl.count() > 0:
            pl.click()
            page.wait_for_timeout(2000)
    except Exception:
        pass

    targets = [
        f"{APPLE_MUSIC_URL}/us/library/playlists",
        f"{APPLE_MUSIC_URL}/library/playlists",
    ]

    for target in targets:
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
        except Exception:
            continue

        if playlists_visible(page):
            return True

    return False


def playlists_visible(page) -> bool:
    """Return True only when playlist/folder nodes are actually visible."""
    selectors = [
        'li[data-testid="navigation-item__folder"]',
        'a[href*="/library/playlist/"]',
        'li.navigation-item__folder--has-children',
    ]
    for sel in selectors:
        try:
            if page.query_selector(sel) is not None:
                return True
        except Exception:
            pass
    return False


def ensure_playlists_ready(page) -> bool:
    """Open playlists directly; if redirected/logged out, perform login and retry."""
    for attempt in range(2):
        if navigate_to_playlists(page):
            return True
        try:
            page.reload(wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        print(f"  Playlist view not visible (attempt {attempt + 1}/2).")

    print("  Existing session not ready, login required.")
    if not wait_for_login(page):
        return False

    print("  Login confirmed, retrying playlist navigation...")
    return navigate_to_playlists(page)


def extract_serialized_data(page) -> dict[str, Any]:
    try:
        script = page.query_selector('script#serialized-server-data')
        if script:
            raw = script.inner_text()
            return json.loads(raw) if raw else {}
    except Exception:
        pass
    return {}


def expand_all_folders(page) -> None:
    print("  Expanding all folders...")
    selectors = [
        'li[data-testid="navigation-item__folder"][aria-expanded="false"]',
        '.navigation-item__folder--has-children[aria-expanded="false"]',
    ]
    for sel in selectors:
        folders = page.query_selector_all(sel)
        if folders:
            print(f"    Clicking {len(folders)} collapsed folders...")
            for folder in folders:
                try:
                    label = folder.query_selector('.navigation-item__folder-label')
                    if label:
                        label.click()
                        page.wait_for_timeout(500)
                except Exception:
                    pass
            page.wait_for_timeout(2000)
            break


def extract_playlists(page) -> list[dict[str, str]]:
    print("  Extracting playlists from sidebar...")
    playlists = []

    page.wait_for_timeout(2000)

    folders = page.query_selector_all('[data-testid="navigation-item__folder"]')
    if not folders:
        folders = page.query_selector_all('li.navigation-item__folder--has-children')

    if folders:
        print(f"    Found {len(folders)} folders")
        for folder in folders:
            try:
                label_el = folder.query_selector('[class*="navigation-item__label"]')
                folder_name = label_el.inner_text().strip() if label_el else "Unknown"

                links = folder.query_selector_all('a[href*="/library/playlist/"]')
                if links:
                    print(f"    Folder '{folder_name}': {len(links)} playlists")
                    for link in links:
                        href = link.get_attribute("href")
                        name_el = link.query_selector('[class*="navigation-item__label"]')
                        name = name_el.inner_text().strip() if name_el else link.inner_text().strip()
                        if name and href:
                            playlists.append({"name": name, "href": href, "folder": folder_name})
            except Exception:
                pass

    if not playlists:
        print("    Scanning page for playlists...")
        items = page.query_selector_all('a[href*="/library/playlist/"]')
        if items:
            print(f"    Found {len(items)} playlist links")
            for item in items:
                try:
                    href = item.get_attribute("href")
                    name_el = item.query_selector('[class*="navigation-item__label"]')
                    name = name_el.inner_text().strip() if name_el else item.inner_text().strip()
                    if name and href:
                        playlists.append({"name": name, "href": href, "folder": ""})
                except Exception:
                    pass

    print(f"  Found {len(playlists)} playlists")
    return playlists


def extract_playlist_tracks(page, playlist_url: str, timeout_ms: int = 30000) -> list[dict[str, str]]:
    print(f"  Opening playlist...")
    url = f"{APPLE_MUSIC_URL}{playlist_url}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception:
        return []

    page.wait_for_timeout(2500)
    tracks = []
    data = extract_serialized_data(page)

    if isinstance(data, dict):
        sections = data.get("sections", [])
        for section in sections:
            if section.get("itemKind") == "trackLockup":
                for item in section.get("items", []):
                    try:
                        title = strip_duration_prefix(item.get("title", ""))
                        artist = clean_artist_value(item.get("artistName", ""))
                        album = item.get("albumName", "")
                        play_params = item.get("playParams", {}) if isinstance(item.get("playParams", {}), dict) else {}
                        isrc = deep_get_first_str(item, [
                            ("isrc",),
                            ("isrcCode",),
                            ("attributes", "isrc"),
                            ("meta", "isrc"),
                            ("playParams", "isrc"),
                        ])
                        apple_id = deep_get_first_str(item, [
                            ("id",),
                            ("contentId",),
                            ("playParams", "id"),
                            ("attributes", "playParams", "id"),
                        ])
                        if not album:
                            album = deep_get_first_str(item, [
                                ("albumName",),
                                ("attributes", "albumName"),
                                ("meta", "album"),
                            ])
                        if title:
                            tracks.append({
                                "title": title,
                                "artist": artist,
                                "album": album,
                                "isrc": isrc,
                                "apple_id": apple_id,
                            })
                    except Exception:
                        pass

        if not tracks:
            og = data.get("seoData", {}).get("ogSongs", [])
            for song in og:
                try:
                    title = strip_duration_prefix(song.get("title", ""))
                    artist = clean_artist_value(song.get("artist", ""))
                    album = song.get("album", "")
                    isrc = str(song.get("isrc", "") or "")
                    apple_id = str(song.get("id", "") or song.get("appleId", "") or "")
                    if title:
                        tracks.append({
                            "title": title,
                            "artist": artist,
                            "album": album,
                            "isrc": isrc,
                            "apple_id": apple_id,
                        })
                except Exception:
                    pass

        if not tracks and data:
            print(f"    Data keys: {list(data.keys())[:5]}")

    if not tracks:
        tracks = fallback_extract_tracks_dom(page)

    print(f"  Found {len(tracks)} tracks")
    return tracks


def fallback_extract_tracks_dom(page) -> list[dict[str, str]]:
    selectors = [
        '[data-testid="track-list-row"]',
        '[data-testid="songs-list-row"]',
        '.songs-list-row',
        '[role="row"]',
    ]

    for sel in selectors:
        rows = page.query_selector_all(sel)
        if not rows:
            continue

        tracks = []
        for row in rows:
            try:
                title_el = row.query_selector('[class*="title"], [data-testid*="title"], [id*="title"]')
                artist_el = row.query_selector('[class*="artist"], [data-testid*="artist"]')
                album_el = row.query_selector('[class*="album"], [data-testid*="album"]')

                title = strip_duration_prefix(title_el.inner_text().strip() if title_el else "")
                artist = clean_artist_value(artist_el.inner_text().strip() if artist_el else "")
                album = album_el.inner_text().strip() if album_el else ""
                isrc = ""
                apple_id = ""

                song_link = row.query_selector('a[href*="/song/"]')
                if song_link:
                    href = song_link.get_attribute("href") or ""
                    m = re.search(r"/song/(\d+)", href)
                    if m:
                        apple_id = m.group(1)

                row_lines = [x.strip() for x in row.inner_text().splitlines() if x.strip()]
                filtered_lines = [x for x in row_lines if not is_duration_token(x)]

                if not title and filtered_lines:
                    title = strip_duration_prefix(filtered_lines[0])

                if not artist and title:
                    for line in filtered_lines:
                        if line != title:
                            artist = clean_artist_value(line)
                            if artist:
                                break

                if not album and title:
                    for line in filtered_lines:
                        if line != title and line != artist:
                            album = line
                            break

                if title:
                    tracks.append({"title": title, "artist": artist, "album": album, "isrc": isrc, "apple_id": apple_id})
            except Exception:
                pass

        if tracks:
            return tracks

    return []


def save_playlist_txt(playlist_name: str, tracks: list[dict[str, str]], output_dir: Path, folder: str = "") -> Path:
    safe_name = sanitize_name(playlist_name)

    if folder:
        safe_folder = sanitize_name(folder)
        target_dir = output_dir / safe_folder
    else:
        target_dir = output_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{safe_name}.txt"

    with open(path, "w", encoding="utf-8") as f:
        for track in tracks:
            artist = track.get("artist", "")
            title = track.get("title", "")
            album = track.get("album", "")
            if artist and title:
                if album:
                    f.write(f"{artist} - {title} || Album: {album}\n")
                else:
                    f.write(f"{artist} - {title}\n")
            elif title:
                f.write(f"{title}\n")

    return path


def save_all_playlists_csv(rows: list[dict[str, str]], output_dir: Path, csv_filename: str) -> Path:
    target_dir = output_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / csv_filename

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Track name", "Artist name", "Album", "Playlist name", "Type", "ISRC", "Apple - id"])
        for track in rows:
            writer.writerow([
                track.get("title", ""),
                track.get("artist", ""),
                track.get("album", ""),
                track.get("playlist_name", ""),
                track.get("type", "Playlist") or "Playlist",
                track.get("isrc", ""),
                track.get("apple_id", ""),
            ])

    return path


def main():
    parser = argparse.ArgumentParser(description="Export Apple Music playlists via web scraping")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Directory for exported playlist files")
    parser.add_argument("--format", choices=["csv", "txt"], default="csv", help="Export format (default: csv)")
    parser.add_argument("--csv-filename", type=str, default="My Apple Music Library.csv", help="Filename for consolidated CSV export")
    parser.add_argument("--headless", action="store_true", help="Run browser headless (not recommended for first login)")
    parser.add_argument("--name-filter", type=str, default="^\\d+-", help="Regex to filter playlists by name (default: '^\\d+-' for digit+dash prefix)")
    parser.add_argument("--clear-state", action="store_true", help="Clear saved session state and force re-login")
    parser.add_argument("--timeout", type=int, default=60, help="Page load timeout in seconds (default: 60)")
    parser.add_argument("--resume", action="store_true", help="Skip playlists that already have exported files for selected --format")
    parser.add_argument("--cdp", type=str, default="", help="Connect to existing browser via CDP (e.g., http://localhost:9222)")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print("  Apple Music Web Scraper")
    print(f"  Output: {args.output}")
    print(f"{'='*70}\n")

    if args.clear_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print(f"  Cleared saved session state: {STATE_FILE}")
        if PERSISTENT_PROFILE_DIR.exists():
            import shutil
            shutil.rmtree(PERSISTENT_PROFILE_DIR, ignore_errors=True)
            print(f"  Cleared persistent profile: {PERSISTENT_PROFILE_DIR}")

    with sync_playwright() as p:
        browser = None
        context = None
        page = None

        if args.cdp:
            print(f"  Connecting to existing browser via CDP: {args.cdp}")
            try:
                browser = p.chromium.connect_over_cdp(args.cdp)
                if browser.contexts:
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else context.new_page()
                    print(f"  Connected! Pages: {len(context.pages)}")
                    state_loaded = True
                else:
                    print("  No contexts found in connected browser")
                    state_loaded = False
            except Exception as e:
                print(f"  Could not connect via CDP: {e}")
                state_loaded = False
        else:
            PERSISTENT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(PERSISTENT_PROFILE_DIR),
                headless=args.headless,
                viewport={"width": 1400, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()
            print(f"  Using persistent profile: {PERSISTENT_PROFILE_DIR}")
            print(f"  Opening {APPLE_MUSIC_URL}...")
            page.goto(APPLE_MUSIC_URL, wait_until="domcontentloaded", timeout=15000)

            if page.query_selector('[data-testid="library-sidebar"] , [data-testid="your-library"] , [aria-label*="Library"]') is None:
                if not wait_for_login(page):
                    context.close()
                    sys.exit(1)
                print("  Login established in persistent profile.")

        if not ensure_playlists_ready(page):
            print("  Could not navigate to playlists.")
            if browser:
                browser.close()
            sys.exit(1)

        expand_all_folders(page)
        playlists = extract_playlists(page)
        if not playlists:
            print("  No playlists found.")
            if browser:
                browser.close()
            sys.exit(0)

        if args.name_filter:
            pattern = re.compile(args.name_filter)
            filtered = [p for p in playlists if pattern.search(p.get("name", ""))]
            print(f"  Filtered: {len(filtered)}/{len(playlists)} playlists match '{args.name_filter}'")
            playlists = filtered

        exported = 0
        total_tracks = 0
        skipped = 0
        csv_rows: list[dict[str, str]] = []

        for i, playlist in enumerate(playlists):
            name = playlist["name"]
            href = playlist["href"]
            folder = playlist.get("folder", "")

            if args.resume:
                safe_name = sanitize_name(name)
                if folder:
                    safe_folder = sanitize_name(folder)
                    existing = args.output / safe_folder / f"{safe_name}.{args.format}"
                else:
                    existing = args.output / f"{safe_name}.{args.format}"
                if existing.exists():
                    skipped += 1
                    continue

            print(f"\n  [{i+1}/{len(playlists)}] {name}")
            if folder:
                print(f"    Folder: {folder}")

            tracks = extract_playlist_tracks(page, href, timeout_ms=args.timeout * 1000)
            if tracks:
                if args.format == "csv":
                    for track in tracks:
                        row = dict(track)
                        row["playlist_name"] = name
                        row["type"] = "Playlist"
                        csv_rows.append(row)
                    path = args.output / args.csv_filename
                else:
                    path = save_playlist_txt(name, tracks, args.output, folder)
                print(f"  Saved: {path}")
                exported += 1
                total_tracks += len(tracks)
            else:
                print(f"  ⚠ No tracks found, skipping")

        if args.format == "csv":
            final_csv = save_all_playlists_csv(csv_rows, args.output, args.csv_filename)
            print(f"\n  Wrote consolidated CSV: {final_csv} ({len(csv_rows)} rows)")

        if not args.cdp and context:
            context.close()

    print(f"\n{'='*70}")
    print(f"  Exported: {exported} playlists ({total_tracks} tracks)")
    if skipped:
        print(f"  Skipped:  {skipped} (already existed, use --resume)")
    print(f"  Output: {args.output}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
