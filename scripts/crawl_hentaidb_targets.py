#!/usr/bin/env python3
"""
Crawl HentaiDB Targets:
Crawls pure 2D anime series from https://hentaidb.xyz/
Resolves direct high-quality MP4 streams (1080p/720p H.264).
Deduplicates against existing catalog in data/videos.json.
Enforces strict 2D anime only rule (blacklists any 3D/SFM/Blender content).
Outputs clean targets batch to data/hentaidb_targets.json.
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from auto_watcher import normalize_title
from scrape_hentaidb import (
    BASE_URL,
    create_session,
    parse_balanced_json,
    scrape_all_series_index,
    scrape_episode_streams,
    scrape_series_details,
)

# Strict 3D/SFM/Blender blacklist to guarantee only 100% pure 2D anime
BLACKLIST_3D = [
    "3d", "sfm", "blender", "koikatsu", "mmd", "c4d",
    "overwatch", "genshin", "subverse", "honkai", "source filmmaker",
    "unreal", "unity", "vroid", "daz"
]


def is_3d_content(text: str) -> bool:
    """Returns True if title or slug contains 3D/SFM indicators."""
    t_lower = text.lower()
    for kw in BLACKLIST_3D:
        if re.search(rf"\b{re.escape(kw)}\b", t_lower):
            return True
    return False


def load_catalog_signatures(repo_root: Path) -> Tuple[Set[str], Set[str], Set[str]]:
    """Loads existing catalog titles and source IDs to avoid duplicate processing."""
    vpath = repo_root / "data" / "videos.json"
    existing_raw = set()
    existing_norm = set()
    existing_sids = set()

    if vpath.is_file():
        try:
            with open(vpath, "r", encoding="utf-8") as f:
                records = json.load(f)
            for r in records:
                t = r.get("title", "").strip().lower()
                if t:
                    existing_raw.add(t)
                    nt = normalize_title(t)
                    if nt:
                        existing_norm.add(nt)
                sid = r.get("source_id")
                if sid:
                    existing_sids.add(str(sid).lower())
        except Exception as e:
            print(f"[Warning] Failed loading catalog signatures: {e}", file=sys.stderr)

    return existing_raw, existing_norm, existing_sids


def crawl_hentaidb_batch(
    target_count: int = 50,
    start_series_index: int = 0,
    output_path: Optional[str] = None
) -> List[Dict]:
    """
    Finds unprocessed 2D anime episodes and resolves direct MP4 streams.
    """
    session = create_session()
    existing_raw, existing_norm, existing_sids = load_catalog_signatures(REPO_ROOT)
    print(f"[Catalog] Loaded {len(existing_raw)} existing titles and {len(existing_sids)} source IDs.")

    index_path = REPO_ROOT / "data" / "hentaidb_series_index.json"
    if index_path.is_file():
        with open(index_path, "r", encoding="utf-8") as f:
            all_series = json.load(f)
        print(f"[Index] Loaded {len(all_series)} series from {index_path.name}.")
    else:
        print("[Index] Fetching series index dynamically from HentaiDB...")
        all_series = scrape_all_series_index(session)

    targets = []
    seen_in_batch = set()

    for i in range(start_series_index, len(all_series)):
        if len(targets) >= target_count:
            break

        s_entry = all_series[i]
        slug = s_entry.get("slug", "")
        series_title = s_entry.get("title", slug)

        # Check 3D blacklist
        if is_3d_content(slug) or is_3d_content(series_title):
            continue

        print(f"[{i + 1}/{len(all_series)}] Checking: {series_title} ({slug})...")
        details = scrape_series_details(slug, session)
        if not details or not details.get("episodes"):
            continue

        for ep in details["episodes"]:
            if len(targets) >= target_count:
                break

            ep_num = ep.get("episode_number", 1)
            ep_str = f"{ep_num:02d}"
            ep_title = f"{details['title']} - {ep_str}"
            ep_title_raw = ep_title.strip().lower()
            ep_title_norm = normalize_title(ep_title_raw)
            sid = f"hdb-{ep['episode_slug']}".lower()

            # Deduplication checks
            if sid in existing_sids or sid in seen_in_batch:
                continue
            if ep_title_raw in existing_raw or (ep_title_norm and ep_title_norm in existing_norm):
                continue

            # Fetch streams for this episode
            print(f"   -> Resolving MP4 for Episode {ep_num}: {ep['watch_url']}")
            st = scrape_episode_streams(ep["watch_url"], session)
            if not st or not st.get("preferred_stream"):
                print("      [Skip] Could not resolve direct MP4.")
                continue

            target_item = {
                "series": details["title"],
                "title": ep_title,
                "episode": ep_str,
                "part": "",
                "source": st["preferred_stream"],
                "torrent": st["preferred_stream"],
                "magnet": "",
                "mp4_url": st["preferred_stream"],
                "quality": st.get("preferred_quality"),
                "size_mb": st.get("preferred_size_mb"),
                "thumbnail": st.get("poster") or details.get("cover") or "",
                "source_id": sid,
                "source_page": ep["watch_url"],
            }

            targets.append(target_item)
            seen_in_batch.add(sid)
            print(f"      [Target Added #{len(targets)}] {ep_title} ({st.get('preferred_quality')}, {st.get('preferred_size_mb')} MB)")

    out_file = Path(output_path) if output_path else REPO_ROOT / "data" / "hentaidb_targets.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(targets, f, indent=2, ensure_ascii=False)

    print(f"\n[Completed] Generated {len(targets)} new targets -> {out_file}")
    return targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate HentaiDB upload targets batch")
    parser.add_argument("--count", "-c", type=int, default=50, help="Number of targets to generate (default: 50)")
    parser.add_argument("--start", "-s", type=int, default=0, help="Start series index (default: 0)")
    parser.add_argument("--output", "-o", type=str, default="", help="Output JSON path")
    args = parser.parse_args()

    crawl_hentaidb_batch(
        target_count=args.count,
        start_series_index=args.start,
        output_path=args.output or None
    )
