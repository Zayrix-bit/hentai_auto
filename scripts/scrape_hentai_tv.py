"""
Hentai.tv Catalog and Stream Scraper
Extracts metadata from https://hentai.tv/browse and resolves direct MP4 streams from nhplayer.com.
"""

import argparse
import base64
import hashlib
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


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def solve_nhplayer_stream(embed_url: str, session: Optional[requests.Session] = None) -> Optional[str]:
    """
    Reverse-engineers and solves the nhplayer.com challenge:
    1. Extracts base64 video payload and player.php URL
    2. Parses DOM challenge parts (p1, p2, p3, p4, ts)
    3. Fetches dynamic player-core-v2.php for server tokens (sc, rid)
    4. Computes SHA-256 Proof of Work
    5. Requests authenticated direct Cloudflare R2 MP4 URL
    """
    if session is None:
        session = create_session()

    try:
        r1 = session.get(embed_url, headers={"Referer": "https://hentai.tv/"}, timeout=15)
        m_data_id = re.search(r'data-id="([^"]+)"', r1.text)
        if not m_data_id:
            return None

        player_rel = m_data_id.group(1)
        player_url = f"https://nhplayer.com{player_rel}" if player_rel.startswith("/") else player_rel

        r2 = session.get(player_url, headers={"Referer": embed_url}, timeout=15)
        html2 = r2.text

        m_pv = re.search(r'window\._pV\s*=\s*(\{.*?\});', html2)
        if not m_pv:
            return None

        pv = dict(re.findall(r'([a-zA-Z0-9_]+)\s*:\s*"([^"]*)"', m_pv.group(1)))

        m_core = re.search(r'src="(player-core-v2\.php\?[^"]+)"', html2)
        if not m_core:
            return None

        core_url = f"https://nhplayer.com/{m_core.group(1)}"
        r3 = session.get(core_url, headers={"Referer": player_url}, timeout=15)
        core_js = r3.text

        m_sc = re.search(r"var\s+[_a-zA-Z0-9]+\s*=\s*'([0-9a-f]+\.[0-9a-f]+)';", core_js)
        m_rid = re.search(r"var\s+[_a-zA-Z0-9]+\s*=\s*'([0-9a-f]{16})';", core_js)
        sc_val = m_sc.group(1) if m_sc else ""
        rid_val = m_rid.group(1) if m_rid else ""

        ids = re.findall(r"getElementById\('([^']+)'\)", core_js)
        attr_matches = re.findall(r"getAttribute\('([^']+)'\)", core_js)

        soup = BeautifulSoup(html2, "html.parser")
        p1_elem = soup.find(id=ids[0]) if len(ids) > 0 else None
        p2_elem = soup.find(id=ids[1]) if len(ids) > 1 else None
        p3_elem = soup.find(id=ids[2]) if len(ids) > 2 else None
        p4_elem = soup.find(id=ids[3]) if len(ids) > 3 else None
        ts_elem = soup.find(id=ids[4]) if len(ids) > 4 else None

        p1 = p1_elem.get(attr_matches[0], "") if p1_elem and len(attr_matches) > 0 else ""
        p2 = p2_elem.get("value", "") if p2_elem else ""
        p3 = p3_elem.get(attr_matches[1], "") if p3_elem and len(attr_matches) > 1 else ""
        p4 = p4_elem.text.strip() if p4_elem else ""
        ts = ts_elem.get(attr_matches[2], "") if ts_elem and len(attr_matches) > 2 else ""

        ch = p1 + p2 + p3 + p4 + ts
        pow_hex = None
        for n in range(5000000):
            cand = hex(n)[2:]
            if hashlib.sha256((ch + cand).encode("utf-8")).digest()[0] == 0:
                pow_hex = cand
                break

        time.sleep(1.2)

        fp_obj = {
            "t": 1200,
            "mm": [[120, 240, 150], [130, 250, 300]],
            "tm": [],
            "cl": [[150, 260, 500]],
            "kp": [],
            "sc": [],
            "i": 1,
            "mc": 2, "tc": 0, "cc": 1, "kc": 0,
            "b": {
                "sw": 1920, "sh": 1080, "aw": 1920, "ah": 1040, "cd": 24, "pd": 24,
                "tz": -330, "hc": 8, "dm": 8, "pl": "Win32", "lang": "en-US", "langs": "en-US,en",
                "dpr": 1, "ww": 1920, "wh": 950, "touch": False, "pdf": True, "fonts": 0
            }
        }
        fp_b64 = base64.b64encode(json.dumps(fp_obj).encode("utf-8")).decode("utf-8")

        params = {
            "vid": pv["vid"],
            "c": pv.get("ct", ""),
            "p1": p1, "p2": p2, "p3": p3, "p4": p4, "t": ts,
            "sc": sc_val, "rid": rid_val,
            "fp": fp_b64, "df": "", "pow": pow_hex,
            "pid": pv.get("pid", ""), "st": pv.get("st", "")
        }

        r4 = session.get("https://nhplayer.com/get-video-url-v2.php", params=params, headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": player_url
        }, timeout=15)

        res_json = r4.json()
        return res_json.get("url")
    except Exception as e:
        print(f"      [Error solving nhplayer] {e}", file=sys.stderr)
        return None


def scrape_browse_page(page: int, session: requests.Session) -> tuple[List[Dict], int, int]:
    """
    Fetches hentai.tv/browse?page={page} and extracts items from Next.js RSC payload.
    Returns (videos_list, total_videos, total_pages).
    """
    url = f"https://hentai.tv/browse?page={page}"
    resp = session.get(url, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} fetching {url}")

    pushes = re.findall(r'self\.__next_f\.push\(\[1,\s*"(.*?)"\]\)', resp.text)
    all_text = "".join(pushes).replace('\\"', '"').replace('\\\\', '\\')

    total_count = 0
    total_pages = 0
    m_tot = re.search(r'"initialTotal":(\d+),"initialPages":(\d+)', all_text)
    if m_tot:
        total_count = int(m_tot.group(1))
        total_pages = int(m_tot.group(2))

    start_tag = '"initialVideos":'
    end_tag = '],"initialTotal"'
    start_pos = all_text.find(start_tag)
    end_pos = all_text.find(end_tag)

    if start_pos == -1 or end_pos == -1:
        return [], total_count, total_pages

    json_str = all_text[start_pos + len(start_tag):end_pos + 1]
    raw_videos = json.loads(json_str)

    parsed_videos = []
    for v in raw_videos:
        cover_path = v.get("cover") or ""
        cover_url = f"https://hentai.tv{cover_path}" if cover_path.startswith("/") else cover_path

        backdrop_path = v.get("backdrop") or ""
        backdrop_url = f"https://hentai.tv{backdrop_path}" if backdrop_path.startswith("/") else backdrop_path

        item = {
            "id": v.get("id"),
            "slug": v.get("slug"),
            "title": v.get("title"),
            "episode": v.get("ep"),
            "brand": v.get("brand"),
            "quality": v.get("quality", "1080p"),
            "year": v.get("year"),
            "language": v.get("language", "Sub"),
            "duration": v.get("duration"),
            "tags": v.get("tags", []),
            "cover": cover_url,
            "backdrop": backdrop_url,
            "embed_url": v.get("embedUrl"),
            "description": v.get("description"),
            "views": v.get("views"),
            "rating": v.get("rating"),
            "censored": v.get("censored", True),
            "released_at": v.get("releasedAt")
        }
        parsed_videos.append(item)

    return parsed_videos, total_count, total_pages


def main():
    parser = argparse.ArgumentParser(description="Scrape Hentai.tv catalog and direct MP4 streams.")
    parser.add_argument("--start-page", type=int, default=1, help="Starting page number (default: 1)")
    parser.add_argument("--pages", type=int, default=1, help="Number of pages to scrape (default: 1)")
    parser.add_argument("--limit", type=int, default=0, help="Max total videos to process (0 = all on pages)")
    parser.add_argument("--resolve-stream", action="store_true", help="Resolve direct Cloudflare R2 MP4 URLs via nhplayer challenge")
    parser.add_argument("--output", type=str, default="data/hentai_tv_catalog.json", help="Output JSON path")
    args = parser.parse_args()

    session = create_session()
    all_videos = []
    scraped_slugs = set()

    print(f"=== Starting Hentai.tv Scraper ===")
    print(f"Pages: {args.start_page} to {args.start_page + args.pages - 1}")
    print(f"Resolve direct streams: {args.resolve_stream}")
    print(f"Output destination: {args.output}\n")

    for p in range(args.start_page, args.start_page + args.pages):
        print(f"[Page {p}] Fetching browse page...")
        try:
            page_videos, total_count, total_pages = scrape_browse_page(p, session)
            print(f"[Page {p}] Found {len(page_videos)} videos (Total catalog size: {total_count} videos / {total_pages} pages)")
        except Exception as e:
            print(f"[Page {p}] Error fetching page: {e}", file=sys.stderr)
            continue

        for v in page_videos:
            slug = v["slug"]
            if slug in scraped_slugs:
                continue
            scraped_slugs.add(slug)

            if args.resolve_stream and v.get("embed_url"):
                print(f"  -> Solving stream for: {v['title']} (Ep {v['episode']})")
                mp4_url = solve_nhplayer_stream(v["embed_url"], session)
                if mp4_url:
                    v["mp4_url"] = mp4_url
                    try:
                        h = session.head(mp4_url, headers={"Referer": "https://nhplayer.com/"}, timeout=10)
                        if h.status_code == 200:
                            v["file_size_bytes"] = int(h.headers.get("content-length", 0))
                            v["file_size_mb"] = round(v["file_size_bytes"] / (1024 * 1024), 2)
                            print(f"     Direct MP4: {mp4_url[:60]}... ({v['file_size_mb']} MB)")
                        else:
                            print(f"     Direct MP4 obtained (HEAD status: {h.status_code})")
                    except Exception:
                        pass
                else:
                    print(f"     [Warning] Failed to resolve stream for {slug}")

            all_videos.append(v)
            if args.limit and len(all_videos) >= args.limit:
                print(f"\nReached requested limit of {args.limit} videos.")
                break

        if args.limit and len(all_videos) >= args.limit:
            break

    # Save output
    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(all_videos, f, indent=2, ensure_ascii=False)

    print(f"\n[Completed] Successfully scraped {len(all_videos)} videos. Saved to: {args.output}")


if __name__ == "__main__":
    main()
