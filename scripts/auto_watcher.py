#!/usr/bin/env python3
"""
Auto Watcher: Fully Automated Torrent Feed -> DropEmbed & cPanel Pipeline
Fetches latest anime/hentai releases from RSS feeds (Sukebei Nyaa / AnimeTosho),
checks against existing database, and automatically downloads and streams to DropEmbed.
"""

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

# Ensure scripts directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import core pipeline methods
from pipeline import (
    download_with_aria2,
    dropembed_api_request,
    extract_season_episode_part,
    extract_source_id,
    fetch_mal_metadata,
    locate_largest_video,
    notify_cpanel_database,
    resolve_source,
    save_catalog,
    upload_to_dropembed,
    write_github_summary,
)

# Default Sukebei uploader & general fallback RSS feeds (Unblocked on GitHub cloud runners)
DEFAULT_TARGET_USER = "Doomdos"
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


def normalize_title(title: str) -> str:
    """
    Standardizes a release title for collision and duplicate detection.
    Strips bracketed metadata [tag], (tag), video resolutions, audio tags, and punctuation.
    """
    if not title:
        return ""
    t = re.sub(r"\[.*?\]", " ", title)
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(
        r"\b(1080p|720p|480p|uncensored|censored|web-dl|bdrip|dvdrip|aac|h264|x264|h265|x265|hevc|avc|multi-audio|multi-sub|dub|sub|dual-audio|raw)\b",
        " ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"[^a-zA-Z0-9]+", " ", t).strip().lower()
    return re.sub(r"\s+", " ", t)


def fetch_dropembed_remote_catalog(api_key: str) -> dict:
    """
    Queries live DropEmbed API (https://upload.dropembed.com/api/videos)
    to fetch all currently uploaded videos for zero-duplicate enforcement.
    Returns: {"titles": set(), "norm_titles": set(), "video_ids": set()}
    """
    remote_data = {
        "titles": set(),
        "norm_titles": set(),
        "video_ids": set(),
    }
    if not api_key:
        return remote_data

    page = 1
    max_pages = 10
    total_found = 0

    while page <= max_pages:
        url = f"https://upload.dropembed.com/api/videos?page={page}&limit=100"
        try:
            res = dropembed_api_request("GET", url, api_key, timeout=15)
            raw_data = res.get("data", [])
            items = []
            if isinstance(raw_data, list):
                items = raw_data
            elif isinstance(raw_data, dict):
                items = raw_data.get("videos") or raw_data.get("items") or raw_data.get("data") or []

            if not items:
                break

            for v in items:
                v_title = v.get("title") or ""
                if v_title:
                    remote_data["titles"].add(v_title.strip().lower())
                    norm = normalize_title(v_title)
                    if norm:
                        remote_data["norm_titles"].add(norm)
                v_id = v.get("id") or v.get("video_id")
                if v_id:
                    remote_data["video_ids"].add(str(v_id))

            total_found += len(items)
            if len(items) < 100:
                break
            page += 1
        except Exception as e:  # noqa: BLE001
            print(f"[Auto Watcher] Notice: Remote DropEmbed catalog sync check ({e}). Continuing with local catalog.")
            break

    if total_found > 0:
        print(f"[Auto Watcher] Successfully synced {total_found} live videos directly from DropEmbed account.")
    return remote_data


def fetch_sukebei_user_items(username: str = DEFAULT_TARGET_USER, max_pages: int = 5) -> list[dict]:
    """
    Scrapes user uploads from Sukebei Nyaa (e.g. https://sukebei.nyaa.si/user/Doomdos).
    Collects from both official RSS feed and multi-page HTML parsing to gather all releases.
    Merges items cleanly by Sukebei ID/title with seeders count.
    """
    items_by_key = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 1. Try official user RSS feed first (fastest)
    rss_url = f"https://sukebei.nyaa.si/?page=rss&u={urllib.parse.quote(username)}"
    rss_items = fetch_feed_items(rss_url, max_items=100)
    for it in rss_items:
        it["uploader"] = username
        sid = extract_source_id(it.get("source", "")) or extract_source_id(it.get("guid", ""))
        norm = normalize_title(it.get("title", ""))
        key = sid or norm or it.get("title", "").strip().lower()
        if key:
            it["source_id"] = sid
            it["seeders"] = it.get("seeders", 1)  # Default seeders for RSS
            items_by_key[key] = it

    print(f"[Auto Watcher] Fetched {len(items_by_key)} items from {username} RSS feed.")

    # 2. Scrape HTML user pages (for full catalogue / pagination)
    for page in range(1, max_pages + 1):
        page_url = f"https://sukebei.nyaa.si/user/{urllib.parse.quote(username)}?p={page}"
        print(f"[Auto Watcher] Scraping {username} uploads page {page}: {page_url}...")
        try:
            req = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", class_="torrent-list")
            if not table:
                print(f"[Auto Watcher] No torrent table found on page {page}. Reached end of uploads.")
                break

            tbody = table.find("tbody")
            rows = tbody.find_all("tr") if tbody else table.find_all("tr")
            page_found = 0

            for tr in rows:
                links = tr.find_all("a")
                title = ""
                view_url = ""
                torrent_url = ""
                magnet_url = ""

                for a in links:
                    href = a.get("href", "")
                    if href.startswith("/view/") and not href.endswith("#comments"):
                        title = a.get("title") or a.get_text(strip=True)
                        view_url = urllib.parse.urljoin("https://sukebei.nyaa.si", href)
                    elif href.startswith("/download/") and href.endswith(".torrent"):
                        torrent_url = urllib.parse.urljoin("https://sukebei.nyaa.si", href)
                    elif href.startswith("magnet:?"):
                        magnet_url = href

                tds = tr.find_all("td")
                seeders = 0
                if len(tds) >= 6:
                    try:
                        seeders = int(tds[5].get_text(strip=True).replace(",", ""))
                    except (ValueError, TypeError):
                        seeders = 0

                source_link = torrent_url or magnet_url or view_url
                if not (title and source_link):
                    continue

                sid = extract_source_id(torrent_url) or extract_source_id(view_url) or extract_source_id(source_link)
                norm = normalize_title(title)
                key = sid or norm or title.strip().lower()

                if key in items_by_key:
                    # Upgrade with direct .torrent URL & real seeders
                    existing = items_by_key[key]
                    if torrent_url:
                        existing["torrent"] = torrent_url
                        existing["source"] = torrent_url  # Prefer .torrent over magnet for faster aria2c start
                    if magnet_url:
                        existing["magnet"] = magnet_url
                    existing["seeders"] = max(existing.get("seeders", 0), seeders)
                    if sid:
                        existing["source_id"] = sid
                else:
                    items_by_key[key] = {
                        "title": title,
                        "source": source_link,
                        "guid": view_url or source_link,
                        "torrent": torrent_url,
                        "magnet": magnet_url,
                        "uploader": username,
                        "seeders": seeders,
                        "source_id": sid,
                    }
                    page_found += 1

            print(f"[Auto Watcher] Page {page} processed ({page_found} new releases discovered, total {len(items_by_key)} unique releases so far).")
            if not rows or (page_found == 0 and page > 2):
                break

        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"[Auto Watcher] Page {page} returned 404. Reached end of catalog.")
                break
            print(f"[Auto Watcher Warning] HTTP {e.code} on page {page}: {e}")
            break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            print(f"[Auto Watcher Warning] Could not scrape {username} page {page}: {e}")
            break

    all_items = list(items_by_key.values())
    print(f"[Auto Watcher] Total {len(all_items)} unique releases collected from provider {username}.")
    return all_items


def load_processed_records(repo_root: Path, api_key: str = "") -> dict:
    """
    Loads sets of already processed records to prevent duplicate downloads and uploads.
    Checks:
    1. Local videos.json (source_id, video_id, source_input, raw title, normalized title)
    2. Remote DropEmbed catalog (all live video IDs and titles on DropEmbed account)
    """
    processed = {
        "source_ids": set(),
        "video_ids": set(),
        "source_urls": set(),
        "raw_titles": set(),
        "normalized_titles": set(),
    }

    # 1. Local catalog (data/videos.json)
    json_path = repo_root / "data" / "videos.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    src_inp = item.get("source_input")
                    if src_inp:
                        processed["source_urls"].add(src_inp)
                        sid = extract_source_id(src_inp)
                        if sid:
                            processed["source_ids"].add(str(sid))

                    sid_direct = item.get("source_id")
                    if sid_direct:
                        processed["source_ids"].add(str(sid_direct))

                    v_title = item.get("title")
                    if v_title:
                        processed["raw_titles"].add(v_title.strip().lower())
                        norm = normalize_title(v_title)
                        if norm:
                            processed["normalized_titles"].add(norm)

                    v_id = item.get("video_id")
                    if v_id:
                        processed["video_ids"].add(str(v_id))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Auto Watcher Warning] Could not parse local videos.json: {e}")

    # 2. Remote DropEmbed catalog
    if api_key:
        remote = fetch_dropembed_remote_catalog(api_key)
        processed["raw_titles"].update(remote["titles"])
        processed["normalized_titles"].update(remote["norm_titles"])
        processed["video_ids"].update(remote["video_ids"])

    return processed


def is_already_processed(item: dict, processed: dict) -> bool:
    """
    Strict collision and duplicate check.
    Returns True if the item has already been downloaded or uploaded.
    """
    # 1. Check numeric source_id (Sukebei ID or BTIH hash)
    source_id = item.get("source_id") or extract_source_id(item.get("source", "")) or extract_source_id(item.get("guid", "")) or extract_source_id(item.get("torrent", ""))
    if source_id and str(source_id) in processed["source_ids"]:
        return True

    # 2. Check source URLs
    for k in ("source", "torrent", "magnet", "guid"):
        val = item.get(k)
        if val and val in processed["source_urls"]:
            return True

    # 3. Check exact title
    raw_title = item.get("title", "").strip().lower()
    if raw_title and raw_title in processed["raw_titles"]:
        return True

    # 4. Check normalized title
    norm_title = normalize_title(item.get("title", ""))
    if norm_title and norm_title in processed["normalized_titles"]:
        return True

    return False


def run_auto_watcher(
    max_new_videos: int = 85,
    target_user: str = DEFAULT_TARGET_USER,
    max_pages: int = 5,
    worker_index: int = 0,
    total_workers: int = 1,
) -> None:
    """
    Main loop: Checks target uploader (e.g. Doomdos) and RSS feeds, finds unprocessed items,
    shards work across parallel workers, downloads, uploads, and updates catalogs and database.
    """
    api_key = os.environ.get("DROPEMBED_API_KEY")
    if not api_key:
        print("[Auto Watcher Error] DROPEMBED_API_KEY environment variable is required!")
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    processed = load_processed_records(repo_root, api_key=api_key)
    print(
        f"[Auto Watcher] Anti-Overlay Index: {len(processed['source_ids'])} source IDs, "
        f"{len(processed['raw_titles'])} titles, {len(processed['video_ids'])} video IDs active."
    )

    candidate_items = []

    # Priority 1: Fetch from target uploader (Doomdos)
    if target_user:
        print(f"[Auto Watcher] Targeting provider: {target_user}...")
        user_items = fetch_sukebei_user_items(username=target_user, max_pages=max_pages)
        if user_items:
            candidate_items.extend(user_items)

    # Priority 2: Fallback to general feeds if no candidate items found
    if not candidate_items:
        print("[Auto Watcher] Target user returned no items. Checking general RSS feeds...")
        for feed in DEFAULT_RSS_FEEDS:
            items = fetch_feed_items(feed)
            if items:
                candidate_items.extend(items)
                break

    if not candidate_items:
        print("[Auto Watcher] No feed items found. Exiting.")
        return

    # Filter out already processed with strict deduplication
    new_items = []
    seen_in_batch = set()

    for it in candidate_items:
        if is_already_processed(it, processed):
            continue

        sid = it.get("source_id") or extract_source_id(it.get("source", "")) or extract_source_id(it.get("torrent", ""))
        norm_t = normalize_title(it.get("title", ""))
        batch_key = sid or norm_t or it.get("title", "").strip().lower()

        if batch_key in seen_in_batch:
            continue
        seen_in_batch.add(batch_key)
        new_items.append(it)

    print(f"[Auto Watcher] Found {len(new_items)} new unprocessed release(s) out of {len(candidate_items)} total releases.")
    # Deterministic sorting so all parallel workers agree on the exact same list order
    new_items.sort(key=lambda x: (x.get("seeders", 0), str(x.get("source_id", "")), x.get("title", "")), reverse=True)

    # Shard items across parallel matrix workers
    if total_workers > 1:
        sharded = [it for idx, it in enumerate(new_items) if idx % total_workers == worker_index]
        print(f"[Auto Watcher Matrix] Worker {worker_index + 1}/{total_workers}: assigned {len(sharded)} releases out of {len(new_items)} total.")
        new_items = sharded

    if not new_items:
        print(f"[Auto Watcher] Worker {worker_index + 1}/{total_workers}: No pending releases assigned. Everything is up to date!")
        # Write empty worker output file
        worker_out_file = repo_root / "data" / f"worker_output_{worker_index}.json"
        try:
            (repo_root / "data").mkdir(parents=True, exist_ok=True)
            with open(worker_out_file, "w", encoding="utf-8") as f:
                json.dump([], f)
        except OSError:
            pass
        return

    # Process up to max_new_videos per run
    processed_count = 0
    worker_records = []
    download_dir = repo_root / f"downloads_w{worker_index}"

    for item in new_items:
        if processed_count >= max_new_videos:
            print(f"[Auto Watcher] Target quota reached ({processed_count}/{max_new_videos} videos uploaded). Stopping.")
            break

        print("\n" + "=" * 50)
        print(f"▶ Processing Release ({processed_count + 1}/{len(new_items)} remaining, max {max_new_videos}): {item['title']}")
        print(f"  Seeders: {item.get('seeders', 0)} | Source: {item['source'][:60]}...")
        print("=" * 50)

        try:
            # 1. Resolve source & AnimeTosho preview
            source, title_hint, animetosho_thumb = resolve_source(item["source"])
            final_title = title_hint or item["title"]

            # 2. Download via aria2 with fallback to magnet/torrent alternative
            try:
                download_with_aria2(source, download_dir)
            except Exception as dl_err:
                fallback_src = item.get("magnet") or item.get("torrent")
                if fallback_src and fallback_src != source:
                    print(f"[Auto Watcher] Primary download failed ({dl_err}), trying alternative source...")
                    download_with_aria2(fallback_src, download_dir)
                else:
                    raise

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
                    info_r = dropembed_api_request("GET", f"https://upload.dropembed.com/api/videos/{video_id}", api_key, timeout=10)
                    thumb_url = info_r.get("data", {}).get("thumbnail") or ""
                except Exception:
                    pass

            sep_info = extract_season_episode_part(final_title)
            if not sep_info.get("episode"):
                sep_info = extract_season_episode_part(video_file.name)

            source_id = extract_source_id(item["source"]) or item.get("source_id", "")

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
                "season": sep_info.get("season", ""),
                "episode": sep_info.get("episode", ""),
                "part": sep_info.get("part", ""),
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

            # 9. Register in processed sets in-memory
            if source_id:
                processed["source_ids"].add(str(source_id))
            processed["source_urls"].add(item["source"])
            if item.get("torrent"):
                processed["source_urls"].add(item["torrent"])
            if item.get("magnet"):
                processed["source_urls"].add(item["magnet"])
            processed["raw_titles"].add(final_title.strip().lower())
            norm_final = normalize_title(final_title)
            if norm_final:
                processed["normalized_titles"].add(norm_final)
            if video_id:
                processed["video_ids"].add(str(video_id))
            # 10. Record for parallel matrix artifact merging
            worker_records.append(record)

            processed_count += 1
            print(f"[Auto Watcher] Successfully processed ({processed_count}): {final_title}")

        except Exception as e:
            print(f"[Auto Watcher Error] Failed to process {item['title']}: {e}")

        finally:
            if download_dir.exists():
                shutil.rmtree(download_dir, ignore_errors=True)

    # Save worker-specific catalog output for aggregation job
    try:
        data_dir = repo_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        worker_out_file = data_dir / f"worker_output_{worker_index}.json"
        with open(worker_out_file, "w", encoding="utf-8") as f:
            json.dump(worker_records, f, indent=2, ensure_ascii=False)
        print(f"[Auto Watcher Matrix] Worker {worker_index + 1}/{total_workers}: saved {len(worker_records)} records to {worker_out_file.name}")
    except OSError as e:
        print(f"[Auto Watcher Matrix Warning] Could not save worker catalog: {e}")

    print(f"\n[Auto Watcher] Worker {worker_index + 1}/{total_workers} Finished! Total new videos uploaded: {processed_count}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Automated Sukebei/Nyaa Watcher for DropEmbed Pipeline")
    parser.add_argument("max_count", nargs="?", type=int, default=None, help="Max new videos to process (positional shortcut)")
    parser.add_argument("--max-videos", "-n", type=int, default=85, help="Max new videos to process (default: 85)")
    parser.add_argument("--user", "-u", type=str, default=DEFAULT_TARGET_USER, help=f"Target Sukebei/Nyaa uploader (default: {DEFAULT_TARGET_USER})")
    parser.add_argument("--pages", "-p", type=int, default=5, help="Max user profile pages to scrape (default: 5)")
    parser.add_argument("--worker-index", "-w", type=int, default=0, help="Parallel worker index (0-indexed, default: 0)")
    parser.add_argument("--total-workers", "-t", type=int, default=1, help="Total parallel matrix workers (default: 1)")
    args = parser.parse_args()

    max_vids = args.max_count if args.max_count is not None else args.max_videos
    target_u = os.environ.get("TARGET_UPLOADER", args.user)

    run_auto_watcher(
        max_new_videos=max_vids,
        target_user=target_u,
        max_pages=args.pages,
        worker_index=args.worker_index,
        total_workers=args.total_workers,
    )
