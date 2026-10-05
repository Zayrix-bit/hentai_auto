#!/usr/bin/env python3
"""
Auto Watcher: Fully Automated Torrent Feed -> DropEmbed & cPanel Pipeline
Fetches latest anime/hentai releases from RSS feeds (Sukebei Nyaa / AnimeTosho),
checks against existing database, and automatically downloads and streams to DropEmbed.
"""

import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

# Import core pipeline methods
from pipeline import (
    download_with_aria2,
    extract_episode,
    extract_source_id,
    fetch_mal_metadata,
    locate_largest_video,
    notify_cpanel_database,
    resolve_source,
    save_catalog,
    upload_to_dropembed,
    write_github_summary,
)

# Default public RSS feeds (Unblocked on GitHub cloud runners)
DEFAULT_RSS_FEEDS = [
    "https://sukebei.nyaa.si/?page=rss&c=1_1",  # English-translated Art/Anime
    "https://sukebei.nyaa.si/?page=rss",        # All latest releases
]


def fetch_feed_items(feed_url: str, max_items: int = 15) -> list[dict]:
    """
    Parses RSS XML feed and returns list of items with title, link, and magnet.
    """
    print(f"[Auto Watcher] Fetching RSS feed: {feed_url}...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

    try:
        req = urllib.request.Request(feed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[Auto Watcher Warning] Failed to fetch {feed_url}: {e}")
        return []

    items = []
    try:
        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return []

        for item in channel.findall("item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or "").strip()

            # Some feeds provide direct magnet or torrent download link
            magnet = ""
            for child in item:
                # Nyaa specific namespace or enclosure
                if "magnet" in child.tag.lower() or (child.text and child.text.startswith("magnet:?")):
                    magnet = child.text.strip()
                    break

            # If no magnet in special tag, link or guid may be magnet or torrent URL
            source_link = magnet or link or guid

            if title and source_link:
                items.append({
                    "title": title,
                    "source": source_link,
                    "guid": guid or link,
                })
    except ET.ParseError as e:
        print(f"[Auto Watcher Error] XML parse error for {feed_url}: {e}")

    print(f"[Auto Watcher] Found {len(items)} items in feed.")
    return items


def load_processed_guids(repo_root: Path) -> set:
    """
    Loads list of already processed video titles/IDs to prevent duplicate uploads.
    """
    processed = set()
    json_path = repo_root / "data" / "videos.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    if "source_input" in item:
                        processed.add(item["source_input"])
                    if "title" in item:
                        processed.add(item["title"].lower())
                    if "video_id" in item:
                        processed.add(item["video_id"])
        except (json.JSONDecodeError, OSError):
            pass

    return processed


def run_auto_watcher(max_new_videos: int = 2) -> None:
    """
    Main loop: Checks RSS, finds unprocessed items, downloads, uploads, and updates catalogs.
    Limits to `max_new_videos` per workflow run to stay well within GitHub Actions limits.
    """
    api_key = os.environ.get("DROPEMBED_API_KEY")
    if not api_key:
        print("[Auto Watcher Error] DROPEMBED_API_KEY environment variable is required!")
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    processed = load_processed_guids(repo_root)
    print(f"[Auto Watcher] Loaded {len(processed)} existing catalog records.")

    # Collect items from feeds
    candidate_items = []
    for feed in DEFAULT_RSS_FEEDS:
        items = fetch_feed_items(feed)
        if items:
            candidate_items.extend(items)
            break  # Got items from primary feed

    if not candidate_items:
        print("[Auto Watcher] No feed items found. Exiting.")
        return

    # Filter out already processed
    new_items = []
    for it in candidate_items:
        source = it["source"]
        title_lower = it["title"].lower()
        if source not in processed and title_lower not in processed:
            new_items.append(it)

    print(f"[Auto Watcher] Found {len(new_items)} new unprocessed release(s).")
    if not new_items:
        print("[Auto Watcher] Everything is up to date! Nothing to process.")
        return

    # Process up to max_new_videos per run
    processed_count = 0
    download_dir = repo_root / "downloads"

    for item in new_items[:max_new_videos]:
        print("\n" + "=" * 50)
        print(f"▶ Processing Release: {item['title']}")
        print("=" * 50)

        try:
            # 1. Resolve source & AnimeTosho preview
            source, title_hint, animetosho_thumb = resolve_source(item["source"])
            final_title = title_hint or item["title"]

            # 2. Download via aria2
            download_with_aria2(source, download_dir)

            # 3. Locate video file
            video_file = locate_largest_video(download_dir)
            if not final_title:
                final_title = video_file.stem

            # 4. Fetch Official MyAnimeList Metadata (MAL ID, Score, Poster, Synopsis, Genres)
            mal_meta = fetch_mal_metadata(final_title)

            # 5. Stream upload to DropEmbed
            upload_res = upload_to_dropembed(
                video_path=video_file,
                title=final_title,
                api_key=api_key,
            )

            video_id = upload_res.get("video_id", "")
            embed_url = upload_res.get("embed_url") or f"https://dropembed.com/e/{video_id}"
            watch_url = upload_res.get("url") or f"https://dropembed.com/v/{video_id}"

            thumb_url = animetosho_thumb or ""
            if not thumb_url and not mal_meta.get("poster_url") and video_id:
                try:
                    info_r = requests.get(
                        f"https://dropembed.com/api/videos/{video_id}",
                        headers={"X-API-Key": api_key},
                        timeout=10,
                    )
                    if info_r.status_code == 200:
                        thumb_url = info_r.json().get("data", {}).get("thumbnail") or ""
                except (requests.RequestException, ValueError, KeyError):
                    pass

            episode = extract_episode(final_title) or extract_episode(video_file.name)
            source_id = extract_source_id(item["source"])

            record = {
                "title": final_title,
                "video_id": video_id,
                "url": watch_url,
                "embed_url": embed_url,
                "file_name": video_file.name,
                "file_size_mb": round(video_file.stat().st_size / (1024 * 1024), 2),
                "poster_url": mal_meta.get("poster_url") or "",
                "thumbnail_url": thumb_url,
                "banner_url": mal_meta.get("banner_url") or "",
                "description": mal_meta.get("synopsis") or mal_meta.get("description") or "",
                "genres": mal_meta.get("genres") or "Hentai",
                "year": mal_meta.get("year"),
                "mal_id": mal_meta.get("mal_id"),
                "anilist_id": mal_meta.get("anilist_id"),
                "source_id": source_id,
                "episode": episode,
                "score": mal_meta.get("score"),
                "mal_url": mal_meta.get("mal_url") or "",
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "source_input": item["source"],
            }

            # 6. Save to local repository catalog
            save_catalog(record, repo_root)

            # 7. Sync to cPanel MySQL Database
            notify_cpanel_database(record)

            # 8. GitHub Actions Summary
            write_github_summary(record)

            processed_count += 1
            print(f"[Auto Watcher] Successfully processed: {final_title}")

        except Exception as e:  # noqa: BLE001 - protect batch loop from single item failures
            print(f"[Auto Watcher Error] Failed to process {item['title']}: {e}")

        finally:
            if download_dir.exists():
                shutil.rmtree(download_dir, ignore_errors=True)

    print(f"\n[Auto Watcher] Finished! Total new videos uploaded: {processed_count}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Automated RSS Watcher for DropEmbed Pipeline")
    parser.add_argument("max_count", nargs="?", type=int, default=2, help="Max new videos to process (default: 2)")
    args = parser.parse_args()
    run_auto_watcher(max_new_videos=args.max_count)
