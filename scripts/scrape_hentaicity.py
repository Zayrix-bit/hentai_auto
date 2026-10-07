"""
HentaiCity Categories and Video Stream Scraper
Scrapes categories and direct MP4 streams from https://www.hentaicity.com/categories/
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional
import requests
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BASE_URL = "https://www.hentaicity.com"


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": "https://www.hentaicity.com/",
    })
    return session


def scrape_categories(session: Optional[requests.Session] = None) -> List[Dict]:
    """
    Scrapes all video and gallery categories from https://www.hentaicity.com/categories/
    """
    if session is None:
        session = create_session()

    url = f"{BASE_URL}/categories/"
    resp = session.get(url, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} fetching {url}")

    soup = BeautifulSoup(resp.text, "html.parser")
    categories = []

    for it in soup.select(".item"):
        is_video = "video-category" in it.get("class", [])
        h2_links = it.select("h2 a")
        if not h2_links:
            continue

        link = h2_links[0].get("href", "")
        if link.startswith("/"):
            link = f"{BASE_URL}{link}"
        name = h2_links[0].get_text(strip=True)

        img = it.select_one("img")
        img_url = img.get("src") if img else None
        if img_url and img_url.startswith("/"):
            img_url = f"{BASE_URL}{img_url}"

        all_h2 = it.select("h2")
        count_val = None
        if len(all_h2) >= 2:
            raw_c = all_h2[1].get_text(strip=True)
            if raw_c.isdigit():
                count_val = int(raw_c)

        cat_type = "video" if is_video or ("/videos/" in link) else ("gallery" if ("/galleries/" in link) else "other")

        # extract category slug/key
        slug_m = re.search(r'/([^/]+)-(?:popular|recent)\.html', link)
        slug = slug_m.group(1) if slug_m else name.lower().replace(" ", "")

        categories.append({
            "name": name,
            "slug": slug,
            "type": cat_type,
            "count": count_val,
            "url": link,
            "thumbnail": img_url
        })

    return categories


def parse_video_card(card: BeautifulSoup, category_name: str = "") -> Optional[Dict]:
    """
    Parses a single video card from a category listing page, extracting metadata
    and direct MP4 URLs from the thumbnail file structure.
    """
    a_link = card.find("a", href=lambda h: h and "/video/" in h)
    if not a_link:
        return None

    page_url = a_link.get("href", "")
    if page_url.startswith("/"):
        page_url = f"{BASE_URL}{page_url}"

    img = card.find("img")
    title = ""
    thumb_url = ""
    if img:
        title = img.get("alt", "") or a_link.get("title", "")
        thumb_url = img.get("src", "")
    if not title:
        title = a_link.get("title", "") or a_link.get_text(strip=True)

    # Filter out 3D, SFM, Blender, CGI, and non-anime animations
    blacklist_terms = ["3d", "sfm", "blender", "cgi", "overwatch", "pixar", "western"]
    title_lower = title.lower()
    for term in blacklist_terms:
        if re.search(r'\b' + re.escape(term) + r'\b', title_lower):
            return None

    # Extract time and HD badge
    time_badge = card.select_one(".time, .badge")
    duration = time_badge.get_text(strip=True) if time_badge else None

    hd_badge = card.select_one(".flag-hd")
    is_hd = hd_badge is not None

    # Extract internal video path (e.g., 0498/38179)
    # Thumbnail pattern: https://cdn1.images.hentaicity.com/videos/{dir1}/{dir2}/main.jpg
    path_m = re.search(r'/videos/(\d+/\d+)/', thumb_url)
    video_path = path_m.group(1) if path_m else None

    mp4_sources = {}
    if video_path:
        mp4_sources = {
            "720p": f"{BASE_URL}/flv/{video_path}/720p.mp4",
            "480p": f"{BASE_URL}/flv/{video_path}/480p.mp4",
            "mobile": f"{BASE_URL}/flv/{video_path}/mobile.mp4",
            "default": f"{BASE_URL}/flv/{video_path}/default.mp4"
        }

    poster_1080p = f"https://cdn1.images.hentaicity.com/videos/{video_path}/1080p.jpg" if video_path else None

    # Extract slug/id from page_url
    slug_m = re.search(r'/video/(.*?)-([A-Za-z0-9]+)\.html', page_url)
    slug = slug_m.group(1) if slug_m else title.lower().replace(" ", "-")[:50]
    embed_id = slug_m.group(2) if slug_m else None
    embed_url = f"{BASE_URL}/embed/{embed_id}" if embed_id else None

    return {
        "title": title,
        "slug": slug,
        "category": category_name,
        "duration": duration,
        "is_hd": is_hd,
        "video_path": video_path,
        "page_url": page_url,
        "embed_url": embed_url,
        "thumbnail": thumb_url,
        "poster_1080p": poster_1080p,
        "mp4_sources": mp4_sources,
        "preferred_stream": mp4_sources.get("720p") if mp4_sources else None
    }


def scrape_category_videos(
    category_slug: str = "all-recent",
    sort: str = "recent",
    pages: int = 1,
    start_page: int = 1,
    limit: int = 0,
    verify_streams: bool = False,
    session: Optional[requests.Session] = None
) -> List[Dict]:
    """
    Scrapes video items from a given category across multiple pages.
    """
    if session is None:
        session = create_session()

    videos = []
    seen_paths = set()

    for p in range(start_page, start_page + pages):
        if p == 1:
            page_url = f"{BASE_URL}/videos/straight/{category_slug}-{sort}.html" if not category_slug.endswith(f"-{sort}") else f"{BASE_URL}/videos/straight/{category_slug}.html"
        else:
            base_slug = category_slug.replace(f"-{sort}", "")
            page_url = f"{BASE_URL}/videos/straight/{base_slug}-{sort}-{p}.html"

        print(f"[Page {p}] Requesting {page_url}...")
        resp = session.get(page_url, timeout=20)
        if resp.status_code != 200:
            print(f"[Page {p}] HTTP {resp.status_code}, stopping pagination.")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".item")
        print(f"[Page {p}] Found {len(cards)} item cards.")

        for card in cards:
            parsed = parse_video_card(card, category_name=category_slug)
            if not parsed or not parsed.get("video_path"):
                continue

            v_path = parsed["video_path"]
            if v_path in seen_paths:
                continue
            seen_paths.add(v_path)

            if verify_streams and parsed.get("mp4_sources"):
                # Probe highest available stream quality
                for q in ["720p", "mobile", "480p", "default"]:
                    test_url = parsed["mp4_sources"].get(q)
                    try:
                        h = session.head(test_url, timeout=8)
                        if h.status_code == 200:
                            cl = h.headers.get("content-length")
                            parsed["verified_stream"] = test_url
                            parsed["quality"] = q
                            parsed["file_size_bytes"] = int(cl) if cl else None
                            parsed["file_size_mb"] = round(int(cl) / (1024 * 1024), 2) if cl else None
                            break
                    except Exception:
                        pass

            videos.append(parsed)
            if limit and len(videos) >= limit:
                break

        if limit and len(videos) >= limit:
            break

    return videos


def main():
    parser = argparse.ArgumentParser(description="HentaiCity Categories & Video Stream Scraper")
    parser.add_argument("--categories-only", action="store_true", help="Only scrape category directory")
    parser.add_argument("--category", type=str, default="", help="Specific category slug (e.g., '3d', 'anal', 'all-recent')")
    parser.add_argument("--pages", type=int, default=1, help="Number of listing pages to scrape (default: 1)")
    parser.add_argument("--start-page", type=int, default=1, help="Start page number (default: 1)")
    parser.add_argument("--limit", type=int, default=0, help="Max videos to scrape (default: 0 = all)")
    parser.add_argument("--verify-streams", action="store_true", help="Probe HEAD on MP4 files to verify availability and file size")
    parser.add_argument("--output", type=str, default="", help="Output JSON path")
    args = parser.parse_args()

    session = create_session()

    if args.categories_only or not args.category:
        print("=== Scrapping HentaiCity Categories ===")
        cats = scrape_categories(session)
        video_cats = [c for c in cats if c["type"] == "video"]
        gallery_cats = [c for c in cats if c["type"] == "gallery"]

        print(f"Total Categories: {len(cats)} ({len(video_cats)} Video, {len(gallery_cats)} Gallery)\n")
        print("Video Categories:")
        for vc in video_cats:
            print(f"  - {vc['name']:15} ({vc['count']:4} videos) -> slug: '{vc['slug']}'")

        out_path = args.output or "data/hentaicity_categories.json"
        os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(cats, f, indent=2, ensure_ascii=False)
        print(f"\n[Completed] Categories saved to: {out_path}")

        if args.categories_only:
            return

    # If category specified or user didn't request categories-only:
    cat_to_scrape = args.category if args.category else "all-recent"
    print(f"\n=== Scrapping Videos from Category '{cat_to_scrape}' ===")
    vids = scrape_category_videos(
        category_slug=cat_to_scrape,
        pages=args.pages,
        start_page=args.start_page,
        limit=args.limit,
        verify_streams=args.verify_streams,
        session=session
    )

    out_vids = args.output or f"data/hentaicity_{cat_to_scrape}_videos.json"
    os.makedirs(os.path.dirname(out_vids) if os.path.dirname(out_vids) else ".", exist_ok=True)
    with open(out_vids, "w", encoding="utf-8") as f:
        json.dump(vids, f, indent=2, ensure_ascii=False)

    print(f"\n[Completed] Scraped {len(vids)} videos. Saved to: {out_vids}")


if __name__ == "__main__":
    main()
