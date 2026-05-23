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
import argparse
from pathlib import Path

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

    page.goto(f"{APPLE_MUSIC_URL}/us/library/playlists", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(3000)
    return True


def extract_serialized_data(page) -> dict:
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


def extract_playlists(page) -> list:
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


def extract_playlist_tracks(page, playlist_url: str, timeout_ms: int = 30000) -> list:
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
                        title = item.get("title", "")
                        artist = item.get("artistName", "")
                        if title:
                            tracks.append({"title": title, "artist": artist})
                    except Exception:
                        pass

        if not tracks:
            og = data.get("seoData", {}).get("ogSongs", [])
            for song in og:
                try:
                    title = song.get("title", "")
                    artist = song.get("artist", "")
                    if title:
                        tracks.append({"title": title, "artist": artist})
                except Exception:
                    pass

        if not tracks and data:
            print(f"    Data keys: {list(data.keys())[:5]}")

    if not tracks:
        tracks = fallback_extract_tracks_dom(page)

    print(f"  Found {len(tracks)} tracks")
    return tracks


def fallback_extract_tracks_dom(page) -> list:
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

                title = title_el.inner_text().strip() if title_el else ""
                artist = artist_el.inner_text().strip() if artist_el else ""

                if title:
                    tracks.append({"title": title, "artist": artist})
            except Exception:
                pass

        if tracks:
            return tracks

    return []


def save_playlist_txt(playlist_name: str, tracks: list, output_dir: Path, folder: str = "") -> Path:
    safe_name = "".join(c if c.isalnum() or c in " -_" else "-" for c in playlist_name).strip()

    if folder:
        safe_folder = "".join(c if c.isalnum() or c in " -_" else "-" for c in folder).strip()
        target_dir = output_dir / safe_folder
    else:
        target_dir = output_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{safe_name}.txt"

    with open(path, "w", encoding="utf-8") as f:
        for track in tracks:
            artist = track.get("artist", "")
            title = track.get("title", "")
            if artist and title:
                f.write(f"{artist} - {title}\n")
            elif title:
                f.write(f"{title}\n")

    return path


def main():
    parser = argparse.ArgumentParser(description="Export Apple Music playlists via web scraping")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Directory for playlist text files")
    parser.add_argument("--headless", action="store_true", help="Run browser headless (not recommended for first login)")
    parser.add_argument("--name-filter", type=str, default="^\\d+-", help="Regex to filter playlists by name (default: '^\\d+-' for digit+dash prefix)")
    parser.add_argument("--clear-state", action="store_true", help="Clear saved session state and force re-login")
    parser.add_argument("--timeout", type=int, default=60, help="Page load timeout in seconds (default: 60)")
    parser.add_argument("--resume", action="store_true", help="Skip playlists that already have exported .txt files")
    parser.add_argument("--cdp", type=str, default="", help="Connect to existing browser via CDP (e.g., http://localhost:9222)")
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print("  Apple Music Web Scraper")
    print(f"  Output: {args.output}")
    print(f"{'='*70}\n")

    if args.clear_state and STATE_FILE.exists():
        STATE_FILE.unlink()
        print(f"  Cleared saved session state: {STATE_FILE}")

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
            browser = p.chromium.launch(headless=args.headless)
            state_loaded = False

            if STATE_FILE.exists():
                print(f"  Restoring session state from {STATE_FILE}")
                context = browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    storage_state=str(STATE_FILE),
                )
            page = context.new_page()
            print(f"  Opening {APPLE_MUSIC_URL}...")
            page.goto(APPLE_MUSIC_URL, wait_until="domcontentloaded", timeout=15000)

            if page.query_selector('[data-testid="library-sidebar"] , [data-testid="your-library"] , [aria-label*="Library"]') is not None:
                print("  Session restored. Already logged in.")
                state_loaded = True
            else:
                print("  Saved session expired. Re-authentication required.")
                context.close()

        if not state_loaded:
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()
            print(f"  Opening {APPLE_MUSIC_URL}...")
            page.goto(APPLE_MUSIC_URL, wait_until="domcontentloaded", timeout=15000)

            if not wait_for_login(page):
                browser.close()
                sys.exit(1)

            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(STATE_FILE))
            print(f"  Session state saved to {STATE_FILE}")

        if not navigate_to_playlists(page):
            print("  Could not navigate to playlists.")
            browser.close()
            sys.exit(1)

        expand_all_folders(page)
        playlists = extract_playlists(page)
        if not playlists:
            print("  No playlists found.")
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

        for i, playlist in enumerate(playlists):
            name = playlist["name"]
            href = playlist["href"]
            folder = playlist.get("folder", "")

            if args.resume:
                safe_name = "".join(c if c.isalnum() or c in " -_" else "-" for c in name).strip()
                if folder:
                    safe_folder = "".join(c if c.isalnum() or c in " -_" else "-" for c in folder).strip()
                    existing = args.output / safe_folder / f"{safe_name}.txt"
                else:
                    existing = args.output / f"{safe_name}.txt"
                if existing.exists():
                    skipped += 1
                    continue

            print(f"\n  [{i+1}/{len(playlists)}] {name}")
            if folder:
                print(f"    Folder: {folder}")

            tracks = extract_playlist_tracks(page, href, timeout_ms=args.timeout * 1000)
            if tracks:
                path = save_playlist_txt(name, tracks, args.output, folder)
                print(f"  Saved: {path}")
                exported += 1
                total_tracks += len(tracks)
            else:
                print(f"  ⚠ No tracks found, skipping")

        if not args.cdp and browser:
            browser.close()

    print(f"\n{'='*70}")
    print(f"  Exported: {exported} playlists ({total_tracks} tracks)")
    if skipped:
        print(f"  Skipped:  {skipped} (already existed, use --resume)")
    print(f"  Output: {args.output}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
