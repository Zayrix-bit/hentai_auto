#!/usr/bin/env python3
"""
Sync DropEmbed Remote Catalog:
Fetches all videos currently uploaded to DropEmbed account,
identifies any videos that were successfully uploaded but not yet cataloged
in data/videos.json (or not synced to cPanel database),
enriches them with official MyAnimeList metadata,
syncs them to cPanel MySQL database, and saves them into data/videos.json and data/videos.md.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Set UTF-8 encoding
sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import helpers from pipeline & auto_watcher
from scripts.pipeline import (
    dropembed_api_request,
    fetch_mal_metadata,
    extract_season_episode_part,
    notify_cpanel_database,
)
from scripts.auto_watcher import normalize_title


def fetch_all_dropembed_videos(api_key: str) -> list[dict]:
    """
    Fetches all videos from DropEmbed account across all pages.
    """
    all_videos = []
    page = 1
    max_pages = 20

    print("[Sync] Querying DropEmbed API for all account videos...")
    while page <= max_pages:
        url = f"https://upload.dropembed.com/api/videos?page={page}&limit=100"
        try:
            res = dropembed_api_request("GET", url, api_key, timeout=20)
            raw = res.get("data", [])
            items = []
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                items = raw.get("videos") or raw.get("items") or raw.get("data") or []

            if not items:
                break

            all_videos.extend(items)
            print(f"[Sync] Page {page}: Retrieved {len(items)} videos (total so far: {len(all_videos)}).")
            if len(items) < 100:
                break
            page += 1
        except Exception as e:
            print(f"[Sync Error] Failed on page {page}: {e}")
            break

    print(f"[Sync] Total {len(all_videos)} live videos found on DropEmbed account.")
    return all_videos


def sync_dropembed_to_local_and_cpanel():
    api_key = os.environ.get("DROPEMBED_API_KEY")
    if not api_key:
        print("[Sync Error] DROPEMBED_API_KEY is required!")
        sys.exit(1)

    # 1. Load existing local catalog
    json_path = REPO_ROOT / "data" / "videos.json"
    catalog = []
    existing_video_ids = set()
    existing_titles = set()

    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
                for item in catalog:
                    vid = item.get("video_id")
                    if vid:
                        existing_video_ids.add(str(vid).lower())
                    t = item.get("title")
                    if t:
                        existing_titles.add(t.strip().lower())
        except (json.JSONDecodeError, OSError) as e:
            print(f"[Sync Warning] Failed to read existing videos.json: {e}")

    print(f"[Sync] Currently cataloged locally: {len(catalog)} videos.")

    # 2. Fetch all live DropEmbed videos
    remote_videos = fetch_all_dropembed_videos(api_key)
    if not remote_videos:
        print("[Sync] No remote videos found on DropEmbed. Exiting.")
        return

    # 3. Identify missing videos
    recovered_count = 0
    new_records = []

    for v in remote_videos:
        v_id = str(v.get("id") or v.get("video_id") or "").strip()
        v_title = (v.get("title") or "").strip()

        if not v_id or not v_title:
            continue

        if v_id.lower() in existing_video_ids:
            continue

        print(f"\n[Recover Found] Video on DropEmbed NOT in catalog: ID={v_id} | Title='{v_title}'")

        # Enrich with MyAnimeList metadata
        mal_meta = fetch_mal_metadata(v_title)
        sep_info = extract_season_episode_part(v_title)

        watch_url = v.get("url") or f"https://dropembed.com/v/{v_id}"
        embed_url = v.get("embed_url") or f"https://dropembed.com/e/{v_id}"
        thumb = v.get("thumbnail") or mal_meta.get("poster_url") or ""

        file_size_mb = 0.0
        raw_size = v.get("size")
        if raw_size:
            try:
                file_size_mb = round(float(raw_size) / (1024 * 1024), 2)
            except (ValueError, TypeError):
                pass

        record = {
            "title": v_title,
            "video_id": v_id,
            "url": watch_url,
            "embed_url": embed_url,
            "file_name": v.get("file_name") or f"{v_title}.mp4",
            "file_size_mb": file_size_mb,
            "poster_url": mal_meta.get("poster_url") or "",
            "thumbnail_url": thumb,
            "banner_url": mal_meta.get("banner_url") or "",
            "description": mal_meta.get("synopsis") or mal_meta.get("description") or "",
            "genres": mal_meta.get("genres") or [],
            "mal_id": mal_meta.get("mal_id") or None,
            "mal_url": mal_meta.get("mal_url") or "",
            "mal_score": mal_meta.get("mal_score") or None,
            "japanese_title": mal_meta.get("japanese_title") or "",
            "season": sep_info.get("season"),
            "episode": sep_info.get("episode"),
            "part": sep_info.get("part"),
            "uploaded_at": v.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }

        # Sync to cPanel MySQL Database
        notify_cpanel_database(record)

        new_records.append(record)
        existing_video_ids.add(v_id.lower())
        existing_titles.add(v_title.strip().lower())
        recovered_count += 1

    if not new_records:
        print("\n[Sync] Everything is already 100% in sync! No uncataloged videos on DropEmbed.")
        return

    print(f"\n[Sync] Successfully recovered and enriched {len(new_records)} missing videos from DropEmbed!")

    # Merge into catalog (newest first)
    all_combined = new_records + catalog

    # Save to data/videos.json
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_combined, f, indent=2, ensure_ascii=False)
    print(f"[Sync] Saved updated {len(all_combined)} total records to {json_path}")

    # Generate Markdown table
    md_path = REPO_ROOT / "data" / "videos.md"
    md_lines = [
        "# 🎬 DropEmbed Video Catalog\n",
        f"*Total Videos Uploaded: {len(all_combined)}*\n",
        "| Date | Title | Season | Episode | Part | MAL | Score | Watch Link | Embed Player Link |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for rec in all_combined:
        date_str = rec.get("uploaded_at", "")[:10]
        rec_title = rec.get("title", "").replace("|", "\\|")
        v_season = f"S{rec['season']}" if rec.get("season") else "-"
        v_ep = f"E{rec['episode']}" if rec.get("episode") else "-"
        v_part = f"Pt.{rec['part']}" if rec.get("part") else "-"
        v_score = f"⭐ {rec['mal_score']}" if rec.get("mal_score") else "-"
        v_mal = (
            f"[MAL #{rec['mal_id']}]({rec['mal_url']})"
            if rec.get("mal_id") and rec.get("mal_url")
            else (f"MAL #{rec['mal_id']}" if rec.get("mal_id") else "-")
        )
        v_url = rec.get("url", "")
        v_embed = rec.get("embed_url", "")
        md_lines.append(
            f"| {date_str} | **{rec_title}** | {v_season} | {v_ep} | {v_part} | {v_mal} | {v_score} | [Watch]({v_url}) | [Embed Player]({v_embed}) |"
        )

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
    print(f"[Sync] Updated {md_path}")


if __name__ == "__main__":
    sync_dropembed_to_local_and_cpanel()
