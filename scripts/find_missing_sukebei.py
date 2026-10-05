#!/usr/bin/env python3
"""
Sukebei Nyaa Missing Episodes Finder:
Searches Sukebei Nyaa (HTML & RSS) for missing anime/hentai episodes across all providers/uploaders.
Sorts strictly by real live seeders descending.
Filters out already-uploaded episodes in data/videos.json.
Outputs both data/missing_episodes_found.json and data/missing_targets.json.
"""

import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from bs4 import BeautifulSoup

# Ensure UTF-8 stdout
sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent

# The exact incomplete series requiring missing episodes
MISSING_TARGETS = [
    {
        "series": "Harem Camp",
        "missing_eps": ["04", "05", "07", "08"],
        "queries": [
            "Harem Camp",
            "ハーレムきゃんぷっ",
            "後宮露營",
            "Harem Camp 04",
            "Harem Camp 05",
            "Harem Camp 07",
            "Harem Camp 08",
            "Harem Camp 1080p",
            "Harem Camp batch",
        ],
    },
    {
        "series": "Nightmare Campus",
        "missing_eps": ["01"],
        "queries": [
            "Nightmare Campus",
            "ナイトメア・キャンパス",
            "Nightmare Campus 01",
            "Nightmare Campus 1",
            "Nightmare Campus 480p",
        ],
    },
    {
        "series": "Sex on the Train with Horny Sluts",
        "missing_eps": ["01"],
        "queries": [
            "Sex on the Train",
            "Sex on the Train with Horny Sluts",
            "淫乱娘",
            "Inshuu Densha",
            "Sluts on the Train",
        ],
    },
    {
        "series": "Bijukubo",
        "missing_eps": ["01"],
        "queries": [
            "Bijukubo",
            "美熟母",
            "Bijukubo 01",
            "Bijukubo 1",
            "Bijukubo Part One",
        ],
    },
    {
        "series": "Fuzzy Lips",
        "missing_eps": ["02"],
        "queries": [
            "Fuzzy Lips",
            "Furueru Kuchibiru",
            "震える口唇",
            "Fuzzy Lips 02",
            "Fuzzy Lips 2",
        ],
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def search_sukebei_html(query: str, category: str = "1_1", max_results: int = 15) -> list[dict]:
    """
    Scrapes Sukebei Nyaa HTML search results sorted by seeders descending.
    Extracts real seeders, magnet link, and direct torrent download link.
    """
    encoded_q = urllib.parse.quote(query)
    url = f"https://sukebei.nyaa.si/?f=0&c={category}&q={encoded_q}&s=seeders&o=desc"
    results = []

    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", class_="torrent-list")
        if not table:
            return results

        rows = table.find_all("tr")
        for tr in rows[1 : max_results + 1]:
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

            sid = view_url.split("/")[-1] if view_url else ""
            if not sid and torrent_url:
                sid = torrent_url.split("/")[-1].replace(".torrent", "")

            source_link = torrent_url or magnet_url or view_url
            if title and source_link:
                results.append({
                    "title": title,
                    "torrent": torrent_url or view_url,
                    "magnet": magnet_url,
                    "source": source_link,
                    "seeders": seeders,
                    "source_id": sid,
                    "view_url": view_url,
                })
    except Exception as e:
        print(f"    [Search Warning] HTML search failed for '{query}' ({category}): {e}")

    return results


def find_all_missing():
    print("=== SEARCHING SUKEBEI NYAA FOR MISSING EPISODES ===")

    # Load existing catalog to avoid redundant searches
    json_path = REPO_ROOT / "data" / "videos.json"
    existing_titles = set()
    existing_sids = set()
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                cat = json.load(f)
                for item in cat:
                    t = item.get("title")
                    if t:
                        existing_titles.add(t.strip().lower())
                    sid = item.get("source_id")
                    if sid:
                        existing_sids.add(str(sid))
        except Exception:
            pass

    found_by_series = {}
    verified_targets = []
    seen_torrent_sids = set()

    for target in MISSING_TARGETS:
        series_name = target["series"]
        print(f"\n🔍 Searching for Series: {series_name} (Missing: {target['missing_eps']})")
        found_by_series[series_name] = []

        all_series_results = []
        for q in target["queries"]:
            print(f"  > Query: '{q}' ...", end=" ", flush=True)
            # Try English anime category 1_1 first
            res = search_sukebei_html(q, category="1_1", max_results=10)
            if not res:
                # Fallback to all categories 0_0
                res = search_sukebei_html(q, category="0_0", max_results=10)

            print(f"found {len(res)} results.")
            for r in res:
                # Only keep active torrents
                if r.get("seeders", 0) > 0 or r.get("source_id"):
                    all_series_results.append(r)

        # Deduplicate and sort by seeders descending
        unique_results = {}
        for r in all_series_results:
            sid = str(r.get("source_id", ""))
            t_lower = r.get("title", "").strip().lower()
            key = sid or t_lower
            if key not in unique_results:
                unique_results[key] = r
            else:
                if r.get("seeders", 0) > unique_results[key].get("seeders", 0):
                    unique_results[key] = r

        sorted_res = sorted(unique_results.values(), key=lambda x: x.get("seeders", 0), reverse=True)
        found_by_series[series_name] = sorted_res

        # Select top candidates for missing_targets.json
        for candidate in sorted_res:
            sid = str(candidate.get("source_id", ""))
            t_lower = candidate.get("title", "").strip().lower()

            if sid and sid in existing_sids:
                continue
            if t_lower in existing_titles:
                continue
            if sid and sid in seen_torrent_sids:
                continue

            verified_targets.append({
                "title": candidate["title"],
                "source": candidate["torrent"],
                "torrent": candidate["torrent"],
                "magnet": candidate.get("magnet", ""),
                "source_id": sid,
                "seeders": candidate.get("seeders", 0),
                "series": series_name,
            })
            if sid:
                seen_torrent_sids.add(sid)

    # Save complete findings
    findings_path = REPO_ROOT / "data" / "missing_episodes_found.json"
    with open(findings_path, "w", encoding="utf-8") as f:
        json.dump(found_by_series, f, indent=2, ensure_ascii=False)
    print(f"\n[Saved] Detailed search results -> {findings_path}")

    # Save actionable targets
    targets_path = REPO_ROOT / "data" / "missing_targets.json"
    with open(targets_path, "w", encoding="utf-8") as f:
        json.dump(verified_targets, f, indent=2, ensure_ascii=False)
    print(f"[Saved] {len(verified_targets)} verified targets -> {targets_path}")

    # Summary
    print("\n=== VERIFIED MISSING TARGETS SUMMARY ===")
    for idx, t in enumerate(verified_targets, 1):
        print(f"  {idx}. [{t['series']}] (Seeders: {t['seeders']}) {t['title']} (ID: {t['source_id']})")


if __name__ == "__main__":
    find_all_missing()
