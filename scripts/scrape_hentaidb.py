"""
HentaiDB Catalog and Direct MP4 Stream Scraper
Scrapes 2,080 pure 2D anime series and 4,941 episodes with direct Cloudflare CDN MP4 streams from https://hentaidb.xyz/
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
BASE_URL = "https://hentaidb.xyz"


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Referer": f"{BASE_URL}/",
    })
    return session


def parse_balanced_json(text: str, start_prefix: str) -> Optional[Dict]:
    """
    Finds a JSON object starting after `start_prefix` using balanced bracket parsing.
    """
    idx = text.find(start_prefix)
    if idx == -1:
        return None
    json_start = idx + len(start_prefix)

    count = 0
    in_string = False
    escape = False
    json_end = -1

    for i in range(json_start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if not in_string:
            if c == "{":
                count += 1
            elif c == "}":
                count -= 1
                if count == 0:
                    json_end = i + 1
                    break

    if json_end == -1:
        return None

    try:
        return json.loads(text[json_start:json_end])
    except Exception:
        return None


def scrape_all_series_index(session: Optional[requests.Session] = None) -> List[Dict]:
    """
    Scrapes the master A-Z directory at https://hentaidb.xyz/all (2,080 pure 2D anime series).
    """
    if session is None:
        session = create_session()

    url = f"{BASE_URL}/all"
    print(f"Fetching master series directory: {url}...")
    resp = session.get(url, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} fetching {url}")

    soup = BeautifulSoup(resp.text, "html.parser")
    series_list = []
    seen_slugs = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("/s/"):
            continue

        slug = href.replace("/s/", "").strip("/")
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        raw_text = a.get_text(strip=True)

        series_list.append({
            "title": raw_text,
            "slug": slug,
            "url": f"{BASE_URL}{href}"
        })

    return series_list


def scrape_series_details(series_slug: str, session: Optional[requests.Session] = None) -> Optional[Dict]:
    """
    Scrapes a specific anime series page, extracting episode links and metadata.
    """
    if session is None:
        session = create_session()

    series_url = f"{BASE_URL}/s/{series_slug}"
    resp = session.get(series_url, timeout=15)
    if resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else series_slug

    episodes = []
    seen_eps = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/watch/"):
            ep_slug = href.replace("/watch/", "")
            if ep_slug not in seen_eps:
                seen_eps.add(ep_slug)
                ep_num = 1
                if "~" in ep_slug:
                    try:
                        ep_num = int(ep_slug.split("~")[-1])
                    except ValueError:
                        pass
                episodes.append({
                    "episode_number": ep_num,
                    "watch_url": f"{BASE_URL}{href}",
                    "episode_slug": ep_slug
                })

    # Sort episodes by episode number
    episodes.sort(key=lambda x: x["episode_number"])

    # Cover / Poster
    img = soup.select_one("img")
    cover_url = f"{BASE_URL}{img['src']}" if img and img.has_attr("src") and img["src"].startswith("/") else (img.get("src") if img else None)

    return {
        "title": title,
        "slug": series_slug,
        "url": series_url,
        "cover": cover_url,
        "total_episodes": len(episodes),
        "episodes": episodes
    }


def scrape_episode_streams(watch_url: str, session: Optional[requests.Session] = None) -> Optional[Dict]:
    """
    Fetches an episode watch page and extracts direct Cloudflare CDN MP4 stream URLs from window.EP.
    """
    if session is None:
        session = create_session()

    resp = session.get(watch_url, timeout=15)
    if resp.status_code != 200:
        return None

    ep_data = parse_balanced_json(resp.text, "window.EP=")
    if not ep_data:
        return None

    raw_sources = ep_data.get("src", [])
    streams = []
    for s in raw_sources:
        file_path = s.get("file", "")
        if not file_path:
            continue
        stream_url = f"{BASE_URL}{file_path}" if file_path.startswith("/") else file_path
        sz_bytes = s.get("sz")
        sz_mb = round(sz_bytes / (1024 * 1024), 2) if sz_bytes else None

        streams.append({
            "quality": s.get("q"),
            "resolution": s.get("r"),
            "codec": s.get("c"),
            "size_bytes": sz_bytes,
            "size_mb": sz_mb,
            "url": stream_url
        })

    # Pick the best H.264 stream (1080p -> 720p -> 480p)
    # H.264 is preferred for DropEmbed compatibility over AV1
    preferred_stream = None
    for target_res in [1080, 720, 480, 360]:
        h264_matches = [st for st in streams if st.get("resolution") == target_res and st.get("codec") == "h264"]
        if h264_matches:
            preferred_stream = h264_matches[0]
            break

    # Fallback to any stream if no H.264 found
    if not preferred_stream and streams:
        preferred_stream = streams[0]

    img_rel = ep_data.get("img") or ""
    poster_url = f"{BASE_URL}{img_rel}" if img_rel.startswith("/") else img_rel

    return {
        "id": ep_data.get("id"),
        "series": ep_data.get("series"),
        "episode_number": ep_data.get("num"),
        "duration_seconds": ep_data.get("d"),
        "poster": poster_url,
        "watch_url": watch_url,
        "preferred_stream": preferred_stream.get("url") if preferred_stream else None,
        "preferred_quality": preferred_stream.get("quality") if preferred_stream else None,
        "preferred_size_mb": preferred_stream.get("size_mb") if preferred_stream else None,
        "all_streams": streams
    }


def main():
    parser = argparse.ArgumentParser(description="HentaiDB 2D Anime Catalog & Direct MP4 Scraper")
    parser.add_argument("--index-only", action="store_true", help="Scrape all 2,080 series directory")
    parser.add_argument("--series", type=str, default="", help="Scrape a specific series slug (e.g. 'imaizumin-chi-wa-douyara-gal-no-tamariba-ni-natteru-rashii')")
    parser.add_argument("--limit-series", type=int, default=0, help="Max series to process (0 = all)")
    parser.add_argument("--limit-episodes", type=int, default=0, help="Max total episodes to scrape")
    parser.add_argument("--output", type=str, default="", help="Output JSON path")
    args = parser.parse_args()

    session = create_session()

    if args.index_only:
        print("=== Scrapping HentaiDB Master Series Directory ===")
        all_series = scrape_all_series_index(session)
        print(f"Total pure 2D anime series found: {len(all_series)}")
        out_path = args.output or "data/hentaidb_series_index.json"
        os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(all_series, f, indent=2, ensure_ascii=False)
        print(f"[Completed] Saved master directory to: {out_path}")
        return

    # Series processing
    series_to_process = []
    if args.series:
        series_to_process = [{"slug": args.series}]
    else:
        print("=== Fetching series index to pick targets ===")
        all_series = scrape_all_series_index(session)
        limit = args.limit_series if args.limit_series > 0 else 5
        series_to_process = all_series[:limit]

    print(f"\nProcessing {len(series_to_process)} anime series...")
    total_episodes_scraped = []

    for s_info in series_to_process:
        slug = s_info["slug"]
        print(f"\n-> Fetching Series: {slug}")
        details = scrape_series_details(slug, session)
        if not details:
            print(f"   [Error] Could not fetch details for {slug}")
            continue

        print(f"   Title: {details['title']} | Total Episodes: {details['total_episodes']}")
        for ep in details["episodes"]:
            watch_url = ep["watch_url"]
            print(f"   -> Resolving Episode {ep['episode_number']}: {watch_url}")
            stream_info = scrape_episode_streams(watch_url, session)
            if stream_info:
                item = {
                    "id": f"hdb_{stream_info.get('id', slug + '~' + str(ep['episode_number']))}",
                    "source": "hentaidb",
                    "series": stream_info.get("series") or details["title"],
                    "title": f"{details['title']} - Episode {ep['episode_number']}",
                    "episode": ep["episode_number"],
                    "duration_seconds": stream_info.get("duration_seconds"),
                    "poster": stream_info.get("poster") or details.get("cover"),
                    "page_url": watch_url,
                    "mp4_url": stream_info.get("preferred_stream"),
                    "quality": stream_info.get("preferred_quality"),
                    "size_mb": stream_info.get("preferred_size_mb"),
                    "all_streams": stream_info.get("all_streams")
                }
                total_episodes_scraped.append(item)
                print(f"      [OK] Direct MP4 ({item['quality']}, {item['size_mb']} MB): {item['mp4_url']}")
            else:
                print(f"      [Failed] Could not resolve stream for {watch_url}")

            if args.limit_episodes and len(total_episodes_scraped) >= args.limit_episodes:
                break

        if args.limit_episodes and len(total_episodes_scraped) >= args.limit_episodes:
            break

    out_path = args.output or "data/hentaidb_episodes.json"
    os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(total_episodes_scraped, f, indent=2, ensure_ascii=False)

    print(f"\n[Completed] Successfully scraped {len(total_episodes_scraped)} anime episodes. Saved to: {out_path}")


if __name__ == "__main__":
    main()
