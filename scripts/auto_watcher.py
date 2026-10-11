#!/usr/bin/env python3
"""
Auto Watcher: HentaiDB (https://hentaidb.xyz) -> Streamtape & cPanel Pipeline
Fetches pure 2D anime releases with direct Cloudflare CDN MP4 streams from HentaiDB,
checks against existing catalog/database, downloads, and streams to Streamtape.
"""

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Ensure scripts directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import core pipeline methods
from pipeline import (
    download_with_aria2,
    extract_season_episode_part,
    extract_source_id,
    fetch_mal_metadata,
    locate_largest_video,
    locate_all_videos,
    notify_cpanel_database,
    resolve_source,
    save_catalog,
    streamtape_api_request,
    upload_to_streamtape,
    write_github_summary,
)



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


def fetch_streamtape_remote_catalog(st_login: str, st_key: str) -> dict:
    """
    Queries live Streamtape API (https://api.streamtape.com/file/listfolder)
    to fetch all currently uploaded files for zero-duplicate enforcement.
    Returns: {"titles": set(), "norm_titles": set(), "video_ids": set()}
    """
    remote_data = {
        "titles": set(),
        "norm_titles": set(),
        "video_ids": set(),
    }
    if not st_login or not st_key:
        return remote_data

    folders_to_scan = [""]  # empty string = root folder
    scanned_folders = set()
    total_found = 0

    while folders_to_scan and len(scanned_folders) < 20:
        folder_id = folders_to_scan.pop(0)
        if folder_id in scanned_folders:
            continue
        scanned_folders.add(folder_id)

        try:
            params = {}
            if folder_id:
                params["folder"] = folder_id
            res = streamtape_api_request("file/listfolder", st_login, st_key, extra_params=params, timeout=15)
            result = res.get("result", {})
            files = result.get("files", [])
            subfolders = result.get("folders", [])

            for sf in subfolders:
                sf_id = sf.get("id")
                if sf_id and sf_id not in scanned_folders:
                    folders_to_scan.append(sf_id)

            for f in files:
                f_id = f.get("id")
                f_name = f.get("name") or ""
                if f_name:
                    remote_data["titles"].add(f_name.strip().lower())
                    stem = Path(f_name).stem
                    remote_data["titles"].add(stem.strip().lower())
                    norm = normalize_title(stem)
                    if norm:
                        remote_data["norm_titles"].add(norm)
                if f_id:
                    remote_data["video_ids"].add(str(f_id))

            total_found += len(files)
        except Exception as e:  # noqa: BLE001
            print(f"[Auto Watcher] Notice: Streamtape remote catalog sync check ({e}). Continuing with local catalog.")
            break

    if total_found > 0:
        print(f"[Auto Watcher] Successfully synced {total_found} live videos directly from Streamtape account.")
    return remote_data





def load_processed_records(repo_root: Path, st_login: str = "", st_key: str = "") -> dict:
    """
    Loads sets of already processed records to prevent duplicate downloads and uploads.
    Checks:
    1. Local videos.json (source_id, video_id, source_input, raw title, normalized title)
    2. Remote Streamtape catalog (all live video IDs and titles on Streamtape account)
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

    # 2. Remote Streamtape catalog
    if st_login and st_key:
        remote = fetch_streamtape_remote_catalog(st_login, st_key)
        processed["raw_titles"].update(remote["titles"])
        processed["normalized_titles"].update(remote["norm_titles"])
        processed["video_ids"].update(remote["video_ids"])

    return processed


def is_already_processed(item: dict, processed: dict) -> bool:
    """
    Strict collision and duplicate check.
    Deduplicates strictly by:
    1. Sukebei unique torrent ID (source_id) or BTIH info hash
    2. Exact source URLs (torrent URL, magnet link, view URL)
    3. Exact release title (lowercased)
    Does NOT collapse distinct releases (e.g. [Dub] vs [Multi-Audio] vs [Uncensored]) that have distinct Sukebei IDs.
    """
    if item.get("_reupload"):
        return False

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

    return False


def run_auto_watcher(
    max_new_videos: int = 50,
    worker_index: int = 0,
    total_workers: int = 1,
    targets_file: str = "",
) -> None:
    """
    Main loop: Checks targets file (or defaults to HentaiDB targets),
    finds unprocessed items, shards work across parallel workers, downloads,
    uploads to Streamtape, and updates catalogs and database.
    """
    st_login = os.environ.get("STREAMTAPE_LOGIN", "")
    st_key = os.environ.get("STREAMTAPE_KEY", "")
    if not st_login or not st_key:
        print("[Auto Watcher Error] STREAMTAPE_LOGIN and STREAMTAPE_KEY environment variables are required!")
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    processed = load_processed_records(repo_root, st_login=st_login, st_key=st_key)
    print(
        f"[Auto Watcher] Anti-Overlay Index: {len(processed['source_ids'])} source IDs, "
        f"{len(processed['raw_titles'])} titles, {len(processed['video_ids'])} video IDs active."
    )

    candidate_items = []

    # Priority 0: Explicit targets file if provided (e.g. data/hentaidb_targets.json)
    if targets_file:
        tf_path = Path(targets_file)
        if not tf_path.is_absolute():
            tf_path = repo_root / tf_path
        if tf_path.is_file():
            print(f"[Auto Watcher] Loading explicit candidate releases from {tf_path}...")
            with open(tf_path, "r", encoding="utf-8") as f:
                candidate_items.extend(json.load(f))
        else:
            print(f"[Auto Watcher Warning] Specified targets file not found: {tf_path}")

    # Fallback: check default data/hentaidb_targets.json
    if not candidate_items:
        default_targets = repo_root / "data" / "hentaidb_targets.json"
        if default_targets.is_file():
            try:
                with open(default_targets, "r", encoding="utf-8") as f:
                    candidate_items.extend(json.load(f))
                print(f"[Auto Watcher] Loaded {len(candidate_items)} targets from default {default_targets.name}")
            except Exception as e:
                print(f"[Auto Watcher Warning] Could not load {default_targets}: {e}")

    # Priority 1: Crawl fresh targets directly from HentaiDB (https://hentaidb.xyz)
    if not candidate_items:
        print("[Auto Watcher] No cached targets found. Crawling fresh releases from HentaiDB...")
        try:
            from crawl_hentaidb_targets import crawl_hentaidb_batch
            candidate_items = crawl_hentaidb_batch(target_count=max_new_videos)
        except Exception as e:
            print(f"[Auto Watcher Error] Failed crawling HentaiDB: {e}")

    if not candidate_items:
        print("[Auto Watcher] No candidate items found. Exiting.")
        return

    # Filter out already processed with strict deduplication
    new_items = []
    seen_in_batch = set()

    for it in candidate_items:
        if is_already_processed(it, processed):
            continue

        sid = it.get("source_id") or extract_source_id(it.get("source", "")) or extract_source_id(it.get("torrent", "")) or extract_source_id(it.get("guid", ""))
        batch_key = sid or it.get("title", "").strip().lower()

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
        if "source" not in item and "mp4_url" in item:
            item["source"] = item["mp4_url"]

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

            # 3. Locate video file(s) (single episode or complete batch release)
            video_files = locate_all_videos(download_dir)
            if not video_files:
                video_files = [locate_largest_video(download_dir)]

            print(f"[Auto Watcher] Found {len(video_files)} video file(s) in downloaded release.")

            for video_file in video_files:
                if len(video_files) > 1:
                    cur_title = video_file.stem
                else:
                    cur_title = final_title or video_file.stem

                # Check if this specific episode file was already uploaded
                if not item.get("_reupload") and cur_title.strip().lower() in processed["raw_titles"]:
                    print(f"[Auto Watcher] Episode file '{cur_title}' already uploaded. Skipping duplicate file.")
                    continue

                sep_info = extract_season_episode_part(cur_title)
                if not sep_info.get("episode"):
                    sep_info = extract_season_episode_part(video_file.name)

                ep_num = sep_info.get("episode")
                if ("nightmare campus" in cur_title.lower() or "gedou gakuen" in cur_title.lower()) and ep_num in ("02", "03", "04", "2", "3", "4"):
                    print(f"[Auto Watcher Anti-Overlay] Nightmare Campus Ep {ep_num} is already uploaded. Skipping redundant file.")
                    continue

                # 4. Fetch Official MyAnimeList Metadata (MAL ID, Score, Poster, Synopsis, Genres)
                mal_meta = fetch_mal_metadata(cur_title)

                # 5. Stream upload to Streamtape
                upload_res = upload_to_streamtape(
                    video_path=video_file,
                    title=cur_title,
                    st_login=st_login,
                    st_key=st_key,
                )

                video_id = upload_res.get("video_id", "")
                embed_url = upload_res.get("embed_url") or f"https://streamtape.com/e/{video_id}"
                watch_url = upload_res.get("url") or f"https://streamtape.com/v/{video_id}"

                thumb_url = animetosho_thumb or ""

                source_id = extract_source_id(item["source"]) or item.get("source_id", "")

                record = {
                    "title": cur_title,
                    "video_id": video_id,
                    "url": watch_url,
                    "embed_url": embed_url,
                    "file_name": video_file.name,
                    "file_size_mb": round(video_file.stat().st_size / (1024 * 1024), 2),
                    "poster_url": mal_meta.get("poster_url") or item.get("thumbnail") or "",
                    "thumbnail_url": thumb_url or item.get("thumbnail") or "",
                    "banner_url": mal_meta.get("banner_url") or "",
                    "description": mal_meta.get("synopsis") or mal_meta.get("description") or item.get("description") or "",
                    "genres": mal_meta.get("genres") or "Hentai",
                    "year": mal_meta.get("year"),
                    "mal_id": mal_meta.get("mal_id"),
                    "anilist_id": mal_meta.get("anilist_id"),
                    "source_id": source_id,
                    "season": sep_info.get("season", "") or item.get("season", "01"),
                    "episode": sep_info.get("episode", "") or item.get("episode", "01"),
                    "part": sep_info.get("part", "") or item.get("part", ""),
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
                processed["raw_titles"].add(cur_title.strip().lower())
                norm_final = normalize_title(cur_title)
                if norm_final:
                    processed["normalized_titles"].add(norm_final)
                if video_id:
                    processed["video_ids"].add(str(video_id))
                # 10. Record for parallel matrix artifact merging
                worker_records.append(record)

                processed_count += 1
                print(f"[Auto Watcher] Successfully processed ({processed_count}): {cur_title}")

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

    parser = argparse.ArgumentParser(description="HentaiDB to Streamtape Automated Pipeline")
    parser.add_argument("max_count", nargs="?", type=int, default=None, help="Max new videos to process")
    parser.add_argument("--max-videos", "-n", type=int, default=50, help="Max new videos to process (default: 50)")
    parser.add_argument("--worker-index", "-w", type=int, default=0, help="Parallel worker index (0-indexed, default: 0)")
    parser.add_argument("--total-workers", "-t", type=int, default=1, help="Total parallel matrix workers (default: 1)")
    parser.add_argument("--targets-file", "-f", type=str, default="", help="Path to JSON file containing targets (default: data/hentaidb_targets.json)")
    args = parser.parse_args()

    max_vids = args.max_count if args.max_count is not None else args.max_videos

    run_auto_watcher(
        max_new_videos=max_vids,
        worker_index=args.worker_index,
        total_workers=args.total_workers,
        targets_file=args.targets_file,
    )
