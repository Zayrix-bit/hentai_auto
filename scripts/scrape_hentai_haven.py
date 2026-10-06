#!/usr/bin/env python3
"""
Hentai Haven & Hentai Ocean Scraper & MP4 Extractor:
Scrapes videos, episodes, and entire series from https://hentai-haven.cc/
Extracts direct, high-speed CDN MP4 video streaming URLs.
Optionally integrates directly with the DropEmbed & cPanel pipeline.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

# Ensure UTF-8 stdout on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def extract_embed_mp4(embed_url: str) -> dict | None:
    """
    Extracts direct CDN MP4 URL from HentaiOcean embed iframe.
    """
    try:
        req = urllib.request.Request(embed_url, headers=HEADERS)
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")

        start = html.find("var jsondata")
        if start == -1:
            return None

        brace_start = html.find("{", start)
        end = html.find("</script>", brace_start)
        raw_json = html[brace_start:end].strip().rstrip(";")
        data = json.loads(raw_json)

        info = data.get("info", [{}])[0] if data.get("info") else {}
        title = info.get("videoname", "")
        desc = info.get("description", "")
        cover = info.get("coverimg", "")
        cover_url = f"https://hentaiocean.com/assets/cover/{cover}" if cover else ""

        mirrors = data.get("mirrors", [])
        for mir in mirrors:
            murl = mir.get("mirrorurl", "")
            if "vid=" in murl:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(murl).query)
                vid = parsed.get("vid", [""])[0]
                if vid:
                    base_domain = urllib.parse.urlparse(murl).netloc
                    mp4_url = f"https://{base_domain}/video/{urllib.parse.quote(vid)}"
                    return {
                        "title": title,
                        "mp4_url": mp4_url,
                        "filename": vid,
                        "description": desc,
                        "thumbnail": cover_url,
                        "embed_url": embed_url,
                    }
    except Exception as e:
        print(f"[Scraper Error] Could not parse embed {embed_url}: {e}", file=sys.stderr)

    return None


def scrape_video_page(video_url: str) -> dict | None:
    """
    Scrapes a video page (e.g. https://hentai-haven.cc/video/muchuu-no-tou-2)
    Extracts title, thumbnail, iframe embed, and direct MP4 URL.
    """
    try:
        req = urllib.request.Request(video_url, headers=HEADERS)
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")

        title_el = soup.find("h1", class_="theatre-title") or soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""

        thumb_el = soup.find("img", class_="part-thumb") or soup.find("img")
        thumb = thumb_el["src"] if (thumb_el and thumb_el.get("src")) else ""

        iframe = soup.find("iframe")
        if not iframe or not iframe.get("src"):
            print(f"[Scraper Warning] No player iframe found on {video_url}", file=sys.stderr)
            return None

        embed_url = iframe["src"]
        result = extract_embed_mp4(embed_url)
        if result:
            result["source_page"] = video_url
            if title:
                result["title"] = title
            if thumb and not result.get("thumbnail"):
                result["thumbnail"] = thumb
            return result
    except Exception as e:
        print(f"[Scraper Error] Failed scraping video page {video_url}: {e}", file=sys.stderr)

    return None


def scrape_title_page(title_url: str) -> dict:
    """
    Scrapes an entire series title page (e.g. https://hentai-haven.cc/title/muchuu-no-tou)
    Extracts all episode parts and their direct MP4 URLs.
    """
    req = urllib.request.Request(title_url, headers=HEADERS)
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    series_name = h1.get_text(strip=True) if h1 else ""

    desc_el = soup.find("p", class_="detail-desc")
    desc = desc_el.get_text(strip=True) if desc_el else ""

    poster_el = soup.find("div", class_="detail-poster")
    poster_img = poster_el.find("img")["src"] if (poster_el and poster_el.find("img")) else ""

    parts = []
    for a in soup.find_all("a", class_="part"):
        part_url = urllib.parse.urljoin(title_url, a.get("href", ""))
        name_el = a.find("span", class_="part-name")
        part_name = name_el.get_text(strip=True) if name_el else ""
        badge_el = a.find("span", class_="part-badge")
        badge = badge_el.get_text(strip=True) if badge_el else ""
        img_el = a.find("img")
        part_thumb = img_el["src"] if (img_el and img_el.get("src")) else ""

        parts.append({
            "url": part_url,
            "name": part_name,
            "badge": badge,
            "thumbnail": part_thumb,
        })

    return {
        "series": series_name,
        "title_url": title_url,
        "description": desc,
        "poster": poster_img,
        "parts_count": len(parts),
        "parts": parts,
    }


def scrape_latest(limit: int = 15) -> list[dict]:
    """
    Scrapes latest titles from Hentai Haven homepage.
    """
    url = "https://hentai-haven.cc/"
    req = urllib.request.Request(url, headers=HEADERS)
    html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    results = []
    for article in soup.find_all("article", class_="tile")[:limit]:
        a = article.find("a", class_="tile-link")
        if not a:
            continue
        href = urllib.parse.urljoin(url, a.get("href", ""))
        name_el = article.find("h3", class_="tile-name")
        name = name_el.get_text(strip=True) if name_el else ""
        count_el = article.find("span", class_="tile-count")
        count = count_el.get_text(strip=True) if count_el else ""
        img_el = article.find("img")
        cover = img_el["src"] if (img_el and img_el.get("src")) else ""

        results.append({
            "title": name,
            "url": href,
            "parts_count": count,
            "cover": cover,
        })

    return results


def resolve_hentai_haven(url: str) -> dict | None:
    """
    Unified resolver: Handles video URLs, title URLs, and embed URLs.
    Returns: {"title": ..., "mp4_url": ..., "thumbnail": ..., "filename": ...}
    """
    url = url.strip()
    if "/video/" in url:
        return scrape_video_page(url)
    elif "/title/" in url:
        series_info = scrape_title_page(url)
        if series_info.get("parts"):
            # By default resolve the first part or return parts list
            first_part = series_info["parts"][0]["url"]
            res = scrape_video_page(first_part)
            if res:
                res["all_parts"] = series_info["parts"]
                res["series_name"] = series_info["series"]
            return res
    elif "/embed/" in url:
        return extract_embed_mp4(url)

    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hentai Haven & Hentai Ocean Direct MP4 Scraper")
    parser.add_argument("url", nargs="?", default="", help="Hentai Haven video or title URL")
    parser.add_argument("--latest", action="store_true", help="List latest releases from homepage")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    parser.add_argument("--pipeline", action="store_true", help="Pass direct MP4 into DropEmbed & cPanel pipeline")
    args = parser.parse_args()

    if args.latest or not args.url:
        print("=== LATEST RELEASES ON HENTAI HAVEN ===")
        items = scrape_latest(limit=10)
        for idx, it in enumerate(items, 1):
            print(f"{idx:2d}. {it['title']} ({it['parts_count']}) -> {it['url']}")
        if not args.url:
            sys.exit(0)

    print(f"\n[Scraper] Resolving: {args.url}")
    if "/title/" in args.url:
        series = scrape_title_page(args.url)
        print(f"\nSeries: {series['series']} ({series['parts_count']} parts)")
        for idx, p in enumerate(series["parts"], 1):
            print(f"  [{idx}] {p['name']} -> {p['url']}")
            v = scrape_video_page(p["url"])
            if v:
                print(f"      MP4: {v['mp4_url']}")
                p["mp4_url"] = v["mp4_url"]
        if args.json:
            print("\n" + json.dumps(series, indent=2))
    else:
        v = scrape_video_page(args.url)
        if v:
            print(f"\n✓ Found Direct MP4 Video!")
            print(f"  Title:    {v['title']}")
            print(f"  Filename: {v['filename']}")
            print(f"  MP4 URL:  {v['mp4_url']}")
            print(f"  Cover:    {v.get('thumbnail')}")
            if args.json:
                print("\n" + json.dumps(v, indent=2))

            if args.pipeline:
                print("\n[Pipeline] Launching DropEmbed Cloud Pipeline for extracted MP4...")
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from pipeline import run_pipeline
                run_pipeline(source=v["mp4_url"], custom_title=v["title"])
        else:
            print("[Error] Could not extract direct MP4 from URL", file=sys.stderr)
            sys.exit(1)
