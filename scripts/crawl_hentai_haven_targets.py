#!/usr/bin/env python3
"""
Crawl Hentai Haven Targets:
Discovers series and episode parts on https://hentai-haven.cc/
Resolves direct CDN MP4 URLs from HentaiOcean.
Deduplicates against existing catalog in data/videos.json.
Outputs a clean list of exactly N target videos to data/hentai_haven_targets.json.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from bs4 import BeautifulSoup

# Ensure UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from pipeline import extract_season_episode_part
from auto_watcher import normalize_title

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def load_catalog_signatures(repo_root: Path) -> tuple[set, set]:
    """
    Loads raw and normalized titles, plus source IDs already present in videos.json.
    """
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
            print(f"[Warning] Failed loading catalog: {e}", file=sys.stderr)

    return existing_raw, existing_norm, existing_sids


def get_series_from_page(page_num: int, order: str = "new_added") -> list[dict]:
    """
    Scrapes a browse page for series cards.
    """
    url = f"https://hentai-haven.cc/browse?order={order}&page={page_num}"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        series = []
        for tile in soup.find_all("article", class_="tile"):
            a = tile.find("a", class_="tile-link")
            if not a or not a.get("href"):
                continue
            href = urllib.parse.urljoin("https://hentai-haven.cc", a["href"])
            h_el = tile.find(["h3", "h2", "div"], class_=lambda x: x and "name" in x)
            name = h_el.get_text(strip=True) if h_el else ""
            img_el = tile.find("img")
            cover = img_el["src"] if (img_el and img_el.get("src")) else ""
            series.append({
                "series_name": name,
                "url": href,
                "cover": cover,
            })
        return series
    except Exception as e:
        print(f"[Error] Failed to scrape browse page {page_num}: {e}", file=sys.stderr)
        return []


def get_parts_for_series(series_info: dict) -> list[dict]:
    """
    Visits a series page to extract all episode parts and their metadata.
    """
    series_url = series_info["url"]
    try:
        req = urllib.request.Request(series_url, headers=HEADERS)
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")

        parts = []
        for a in soup.find_all("a", class_="part"):
            href = a.get("href", "")
            slug = href.split("/")[-1]
            if not slug:
                continue

            name_el = a.find("span", class_="part-name")
            part_name = name_el.get_text(strip=True) if name_el else slug
            badge_el = a.find("span", class_="part-badge")
            badge = badge_el.get_text(strip=True) if badge_el else ""
            img_el = a.find("img")
            part_thumb = img_el["src"] if (img_el and img_el.get("src")) else series_info.get("cover", "")

            parts.append({
                "series_name": series_info["series_name"],
                "series_url": series_url,
                "slug": slug,
                "part_name": part_name,
                "badge": badge,
                "thumbnail": part_thumb,
            })
        return parts
    except Exception as e:
        print(f"[Error] Failed to fetch parts for {series_url}: {e}", file=sys.stderr)
        return []


def resolve_embed_to_mp4(part_info: dict) -> dict | None:
    """
    Directly extracts CDN MP4 from HentaiOcean embed URL for a given slug.
    """
    slug = part_info["slug"]
    embed_url = f"https://hentaiocean.com/embed/{slug}?pt=81"
    try:
        req = urllib.request.Request(embed_url, headers=HEADERS)
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="ignore")

        start = html.find("var jsondata")
        if start == -1:
            return None

        brace_start = html.find("{", start)
        end = html.find("</script>", brace_start)
        raw_json = html[brace_start:end].strip().rstrip(";")
        data = json.loads(raw_json)

        info = data.get("info", [{}])[0] if data.get("info") else {}
        videoname = info.get("videoname", "") or part_info["part_name"]
        cover = info.get("coverimg", "")
        if cover:
            cover_url = f"https://hentaiocean.com/assets/cover/{cover}"
        else:
            cover_url = part_info.get("thumbnail", "")

        mirrors = data.get("mirrors", [])
        for mir in mirrors:
            murl = mir.get("mirrorurl", "")
            if "vid=" in murl:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(murl).query)
                vid = parsed.get("vid", [""])[0]
                if vid:
                    base_domain = urllib.parse.urlparse(murl).netloc
                    mp4_url = f"https://{base_domain}/video/{urllib.parse.quote(vid)}"

                    # Accurate Season/Episode extraction
                    m_badge = re.search(r"(?:part|ep|episode)\s*(\d+)", part_info.get("badge", ""), re.I)
                    m_file = re.search(r"[\s_]+(\d{1,2})\.mp4$", vid, re.I)
                    m_title = re.search(r"\s+(\d+)$", videoname)
                    if m_badge:
                        ep = f"{int(m_badge.group(1)):02d}"
                    elif m_file:
                        ep = f"{int(m_file.group(1)):02d}"
                    elif m_title:
                        ep = f"{int(m_title.group(1)):02d}"
                    else:
                        sep = extract_season_episode_part(videoname)
                        ep = sep.get("episode") or sep.get("part") or "01"

                    return {
                        "series": part_info["series_name"],
                        "title": videoname,
                        "episode": ep,
                        "part": part_info.get("badge", ""),
                        "source": mp4_url,
                        "torrent": mp4_url,
                        "magnet": "",
                        "mp4_url": mp4_url,
                        "filename": vid,
                        "thumbnail": cover_url,
                        "source_id": f"hh-{slug}",
                        "source_page": f"https://hentai-haven.cc/video/{slug}",
                    }
    except Exception as e:
        print(f"[Error] Failed to resolve MP4 for {slug}: {e}", file=sys.stderr)

    return None


def crawl_targets(target_count: int = 100, max_pages: int = 15) -> list[dict]:
    """
    Crawls Hentai Haven browse pages, extracts parts, deduplicates, and resolves MP4s.
    """
    existing_raw, existing_norm, existing_sids = load_catalog_signatures(REPO_ROOT)
    print(f"[Catalog] Loaded {len(existing_raw)} existing titles to avoid duplicates.")

    discovered_parts = []
    seen_slugs = set()

    for page in range(1, max_pages + 1):
        if len(discovered_parts) >= target_count * 2:
            break
        print(f"\n[Crawl] Scanning Browse Page {page}...")
        series_list = get_series_from_page(page, order="new_added")
        if not series_list:
            break
        print(f"  > Found {len(series_list)} series on Page {page}.")

        # Fetch parts in parallel
        with ThreadPoolExecutor(max_workers=10) as ex:
            parts_per_series = list(ex.map(get_parts_for_series, series_list))

        for parts in parts_per_series:
            for p in parts:
                slug = p["slug"]
                sid = f"hh-{slug}"
                if slug in seen_slugs or sid in existing_sids:
                    continue

                # Check title duplication
                t_lower = p["part_name"].strip().lower()
                if t_lower in existing_raw or normalize_title(t_lower) in existing_norm:
                    continue

                seen_slugs.add(slug)
                discovered_parts.append(p)

        print(f"  > Total candidate unuploaded parts collected so far: {len(discovered_parts)}")
        if len(discovered_parts) >= target_count + 30:
            break

    print(f"\n[Resolver] Resolving direct CDN MP4 links for {min(len(discovered_parts), target_count)} targets...")
    verified_targets = []

    # Resolve direct MP4 URLs in parallel
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = {ex.submit(resolve_embed_to_mp4, p): p for p in discovered_parts}
        for fut in as_completed(futures):
            res = fut.result()
            if res and res.get("mp4_url"):
                verified_targets.append(res)
                print(f"  [{len(verified_targets):3d}/{target_count}] Resolved: {res['title']} -> {res['filename']}")
                if len(verified_targets) >= target_count:
                    break

    # Save to JSON
    out_file = REPO_ROOT / "data" / "hentai_haven_targets.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(verified_targets, f, indent=2, ensure_ascii=False)

    print(f"\n✓ Successfully collected and saved {len(verified_targets)} direct MP4 targets to:")
    print(f"  {out_file}")
    return verified_targets


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hentai Haven Target Crawler & MP4 Resolver")
    parser.add_argument("--count", "-n", type=int, default=100, help="Target number of videos to crawl (default: 100)")
    parser.add_argument("--pages", "-p", type=int, default=6, help="Maximum browse pages to scan (default: 6)")
    args = parser.parse_args()

    crawl_targets(target_count=args.count, max_pages=args.pages)
