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

DEFAULT_OUTPUT = Path.home() / "playlists"
APPLE_MUSIC_URL = "https://music.apple.com"


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

    library_selectors = [
        '[data-testid="library-sidebar"]',
        '[data-testid="your-library"]',
        'a[href*="library"]',
        '[aria-label*="Library"]',
    ]

    for sel in library_selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                page.wait_for_timeout(2000)
                break
        except Exception:
            pass

    playlist_selectors = [
        'a[href*="library/playlists"]',
        '[data-testid*="playlist"]',
        'text=Playlists',
    ]

    for sel in playlist_selectors:
        try:
            page.click(sel, timeout=5000)
            page.wait_for_timeout(3000)
            return True
        except Exception:
            pass

    page.goto(f"{APPLE_MUSIC_URL}/us/library/playlists", wait_until="networkidle")
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


def extract_playlists(page) -> list:
    print("  Extracting playlist list...")
    data = extract_serialized_data(page)
    playlists = []

    sections = data.get("sections", []) if isinstance(data, dict) else []
    for section in sections:
        items = section.get("items", [])
        for item in items:
            try:
                title = item.get("title", "")
                href = item.get("url", "")
                if title and href:
                    playlists.append({"name": title, "href": href})
            except Exception:
                pass

    if not playlists:
        playlists = fallback_extract_playlists_dom(page)

    print(f"  Found {len(playlists)} playlists")
    return playlists


def fallback_extract_playlists_dom(page) -> list:
    playlists = []
    selectors = [
        '[data-testid="library-playlist-row"]',
        '[data-testid="shelf-grid"] [data-testid*="playlist"]',
        '.songs-list-row',
        '[class*="playlist"]',
    ]
    for sel in selectors:
        items = page.query_selector_all(sel)
        if items:
            for item in items:
                try:
                    name_el = item.query_selector('[class*="title"] , [class*="name"] , h3 , [data-testid*="title"]')
                    name = name_el.inner_text().strip() if name_el else "Unknown"
                    link_el = item.query_selector('a[href*="playlist"]')
                    href = link_el.get_attribute("href") if link_el else None
                    if name and href:
                        playlists.append({"name": name, "href": href})
                except Exception:
                    pass
            break
    return playlists


def extract_playlist_tracks(page, playlist_url: str) -> list:
    print(f"  Opening playlist...")
    page.goto(f"{APPLE_MUSIC_URL}{playlist_url}", wait_until="networkidle")
    page.wait_for_timeout(3000)

    print("  Extracting tracks...")
    tracks = []
    data = extract_serialized_data(page)

    sections = data.get("sections", []) if isinstance(data, dict) else []
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
        og = data.get("seoData", {}).get("ogSongs", []) if isinstance(data, dict) else []
        for song in og:
            try:
                title = song.get("title", "")
                artist = song.get("artist", "")
                if title:
                    tracks.append({"title": title, "artist": artist})
            except Exception:
                pass

    if not tracks:
        tracks = fallback_extract_tracks_dom(page)

    print(f"  Found {len(tracks)} tracks")
    return tracks


def fallback_extract_tracks_dom(page) -> list:
    tracks = []
    selectors = [
        '[data-testid="track-list-row"]',
        '[data-testid="songs-list-row"]',
        '.songs-list-row',
        'div[class*="track"]',
    ]
    for sel in selectors:
        items = page.query_selector_all(sel)
        if items:
            for item in items:
                try:
                    title_el = item.query_selector('[class*="title"] , [data-testid*="title"] , [id*="title"]')
                    artist_el = item.query_selector('[class*="artist"] , [data-testid*="artist"]')
                    title = title_el.inner_text().strip() if title_el else ""
                    artist = artist_el.inner_text().strip() if artist_el else ""
                    if title:
                        tracks.append({"title": title, "artist": artist})
                except Exception:
                    pass
            break
    return tracks


def save_playlist_txt(playlist_name: str, tracks: list, output_dir: Path) -> Path:
    safe_name = "".join(c if c.isalnum() or c in " -_" else "-" for c in playlist_name).strip()
    path = output_dir / f"{safe_name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)

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
    args = parser.parse_args()

    print(f"\n{'='*70}")
    print("  Apple Music Web Scraper")
    print(f"  Output: {args.output}")
    print(f"{'='*70}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        print(f"  Opening {APPLE_MUSIC_URL}...")
        page.goto(APPLE_MUSIC_URL, wait_until="networkidle")
        page.wait_for_timeout(2000)

        if not wait_for_login(page):
            browser.close()
            sys.exit(1)

        if not navigate_to_playlists(page):
            print("  Could not navigate to playlists.")
            browser.close()
            sys.exit(1)

        playlists = extract_playlists(page)
        if not playlists:
            print("  No playlists found.")
            browser.close()
            sys.exit(0)

        exported = 0
        total_tracks = 0

        for playlist in playlists:
            name = playlist["name"]
            href = playlist["href"]
            print(f"\n  [{exported+1}/{len(playlists)}] {name}")

            tracks = extract_playlist_tracks(page, href)
            if tracks:
                path = save_playlist_txt(name, tracks, args.output)
                print(f"  Saved: {path}")
                exported += 1
                total_tracks += len(tracks)

        browser.close()

    print(f"\n{'='*70}")
    print(f"  Exported: {exported} playlists ({total_tracks} tracks)")
    print(f"  Output: {args.output}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
