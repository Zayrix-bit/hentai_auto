#!/usr/bin/env python3
"""
Sync Streamtape Remote Catalog:
Fetches all files currently uploaded to Streamtape account across all folders,
identifies any files that were successfully uploaded but not yet cataloged
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
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import helpers from pipeline & auto_watcher
from scripts.pipeline import (
    streamtape_api_request,
    fetch_mal_metadata,
    extract_season_episode_part,
    notify_cpanel_database,
)
from scripts.auto_watcher import normalize_title


def fetch_all_streamtape_files(st_login: str, st_key: str) -> list[dict]:
    """
    Fetches all files from Streamtape account by traversing all folders.
    Endpoint: /file/listfolder
    """
    all_files = []
    folders_to_scan = [""]  # empty string represents root folder
    scanned_folders = set()

    print("[Sync] Querying Streamtape API for account folders and files...")
    while folders_to_scan:
        current_folder = folders_to_scan.pop(0)
        if current_folder in scanned_folders:
            continue
        scanned_folders.add(current_folder)

        try:
            params = {}
            if current_folder:
                params["folder"] = current_folder
            res = streamtape_api_request("file/listfolder", st_login, st_key, extra_params=params, timeout=25)
            result = res.get("result", {})
            files = result.get("files", [])
            subfolders = result.get("folders", [])

            for sf in subfolders:
                sf_id = sf.get("id")
                if sf_id and sf_id not in scanned_folders:
                    folders_to_scan.append(sf_id)

            all_files.extend(files)
            folder_label = current_folder if current_folder else "root"
            print(f"[Sync] Folder '{folder_label}': Found {len(files)} files, {len(subfolders)} subfolders (total files: {len(all_files)}).")

        except Exception as e:
            print(f"[Sync Error] Failed scanning folder '{current_folder}': {e}")
            break

    print(f"[Sync] Total {len(all_files)} live files found on Streamtape account.")
    return all_files


def sync_streamtape_to_local_and_cpanel():
    st_login = os.environ.get("STREAMTAPE_LOGIN")
    st_key = os.environ.get("STREAMTAPE_KEY")
    if not st_login or not st_key:
        print("[Sync Error] STREAMTAPE_LOGIN and STREAMTAPE_KEY are required!")
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

    # 2. Fetch all live Streamtape files
    remote_files = fetch_all_streamtape_files(st_login, st_key)
    if not remote_files:
        print("[Sync] No remote files found on Streamtape. Exiting.")
        return

    # 3. Identify missing videos
    recovered_count = 0
    new_records = []

    for f in remote_files:
        v_id = str(f.get("id") or "").strip()
        raw_name = (f.get("name") or "").strip()
        v_title = Path(raw_name).stem if raw_name else ""

        if not v_id or not v_title:
            continue

        if v_id.lower() in existing_video_ids:
            continue

        print(f"\n[Recover Found] Video on Streamtape NOT in catalog: ID={v_id} | Title='{v_title}'")

        # Enrich with MyAnimeList metadata
        mal_meta = fetch_mal_metadata(v_title)
        sep_info = extract_season_episode_part(v_title)

        watch_url = f.get("link") or f"https://streamtape.com/v/{v_id}"
        embed_url = f"https://streamtape.com/e/{v_id}"

        file_size_mb = 0.0
        raw_size = f.get("size")
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
            "file_name": raw_name or f"{v_title}.mp4",
            "file_size_mb": file_size_mb,
            "poster_url": mal_meta.get("poster_url") or "",
            "thumbnail_url": mal_meta.get("poster_url") or "",
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
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }

        # Sync to cPanel MySQL Database
        notify_cpanel_database(record)

        new_records.append(record)
        existing_video_ids.add(v_id.lower())
        existing_titles.add(v_title.strip().lower())
        recovered_count += 1

    if not new_records:
        print("\n[Sync] Everything is already 100% in sync! No uncataloged videos on Streamtape.")
        return

    print(f"\n[Sync] Successfully recovered and enriched {len(new_records)} missing videos from Streamtape!")

    # Merge into catalog (newest first)
    all_combined = new_records + catalog

    # Save to data/videos.json
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_combined, f, indent=2, ensure_ascii=False)
    print(f"[Sync] Saved updated {len(all_combined)} total records to {json_path}")

    # Generate Markdown table
    md_path = REPO_ROOT / "data" / "videos.md"
    md_lines = [
        "# 🎬 Streamtape Video Catalog\n",
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
    sync_streamtape_to_local_and_cpanel()
