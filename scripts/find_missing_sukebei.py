import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import json
import re
import sys
from bs4 import BeautifulSoup

# Ensure UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

# The exact missing episodes we discovered from our audit
MISSING_TARGETS = [
    {
        "series": "Overflow",
        "missing_eps": ["06", "08"],
        "queries": ["Overflow 06", "Overflow 08"],
        "alt_queries": ["おーばーふろぉ 06", "おーばーふろぉ 08"]
    },
    {
        "series": "What She Fell on Was the Tip of My Dick",
        "missing_eps": ["08"],
        "queries": ["Joshiochi 08", "What She Fell on 08"],
        "alt_queries": ["じょしおち 08"]
    },
    {
        "series": "Harem Camp",
        "missing_eps": ["02", "04", "05", "06", "07", "08"],
        "queries": [f"Harem Camp {ep}" for ep in ["02", "04", "05", "06", "07", "08"]],
        "alt_queries": [f"ハーレムきゃんぷっ {ep}" for ep in ["02", "04", "05", "06", "07", "08"]]
    },
    {
        "series": "Nightmare Campus",
        "missing_eps": ["01"],
        "queries": ["Nightmare Campus 01", "Nightmare Campus 1"],
        "alt_queries": []
    },
    {
        "series": "Sex on the Train with Horny Sluts",
        "missing_eps": ["01"],
        "queries": ["Sex on the Train 01", "Sex on the Train 1"],
        "alt_queries": []
    },
    {
        "series": "Bijukubo",
        "missing_eps": ["01"],
        "queries": ["Bijukubo 01", "Bijukubo 1", "Bijukubo Part One"],
        "alt_queries": ["美熟母 1"]
    },
    {
        "series": "Fuzzy Lips",
        "missing_eps": ["02"],
        "queries": ["Fuzzy Lips 02", "Fuzzy Lips 2"],
        "alt_queries": []
    }
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def search_sukebei(query: str, category: str = "1_1", max_results: int = 10) -> list[dict]:
    """Searches Sukebei Nyaa RSS and HTML for a query, sorted by seeders descending."""
    encoded_q = urllib.parse.quote(query)
    
    # 1. Try RSS feed (fastest & structured)
    rss_url = f"https://sukebei.nyaa.si/?page=rss&f=0&c={category}&q={encoded_q}&s=seeders&o=desc"
    results = []
    try:
        req = urllib.request.Request(rss_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read()
            root = ET.fromstring(content)
            for item in root.findall('./channel/item')[:max_results]:
                title = item.find('title').text if item.find('title') is not None else ''
                link = item.find('link').text if item.find('link') is not None else ''
                guid = item.find('guid').text if item.find('guid') is not None else ''
                
                sid = guid.split('/')[-1] if '/' in guid else ''
                torrent_url = f"https://sukebei.nyaa.si/download/{sid}.torrent" if sid.isdigit() else link
                
                results.append({
                    "title": title,
                    "torrent": torrent_url,
                    "link": link,
                    "guid": guid,
                    "source_id": sid,
                    "source": "rss"
                })
    except Exception as e:
        # RSS might fail or return nothing, proceed to HTML
        pass

    if results:
        return results

    # 2. Try HTML search page
    html_url = f"https://sukebei.nyaa.si/?f=0&c={category}&q={encoded_q}&s=seeders&o=desc"
    try:
        req = urllib.request.Request(html_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", class_="torrent-list")
        if not table:
            return results

        rows = table.find_all("tr")
        for tr in rows[1:max_results+1]:
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
                except:
                    seeders = 0

            sid = view_url.split('/')[-1] if view_url else ""
            if title and (torrent_url or magnet_url or view_url):
                results.append({
                    "title": title,
                    "torrent": torrent_url or view_url,
                    "magnet": magnet_url,
                    "seeders": seeders,
                    "source_id": sid,
                    "source": "html"
                })
    except Exception as e:
        pass

    return results

def find_all_missing():
    print("=== SUKEBEI SEARCH FOR MISSING EPISODES ===")
    found_summary = {}

    for target in MISSING_TARGETS:
        series_name = target["series"]
        print(f"\nSearching for Series: {series_name} (Missing: {target['missing_eps']})")
        found_summary[series_name] = {}

        for q in target["queries"]:
            print(f"  > Query: '{q}' ...", end=" ", flush=True)
            results = search_sukebei(q)
            if not results and target["alt_queries"]:
                # Try first alt query
                results = search_sukebei(target["alt_queries"][0])

            if results:
                best = results[0]
                print(f"FOUND! [{best.get('title')}] (ID: {best.get('source_id')})")
                found_summary[series_name][q] = best
            else:
                print("Not found.")

    # Save findings to JSON
    with open("data/missing_episodes_found.json", "w", encoding="utf-8") as f:
        json.dump(found_summary, f, indent=2, ensure_ascii=False)
    
    print("\nSaved search results to data/missing_episodes_found.json")

if __name__ == "__main__":
    find_all_missing()
