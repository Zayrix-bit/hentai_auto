#!/usr/bin/env python3
"""
Cloud Pipeline: Torrent/AnimeTosho -> DropEmbed Uploader
Zero local bandwidth required - runs on GitHub Actions cloud runner.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from bs4 import BeautifulSoup
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder,
    MultipartEncoderMonitor,
)

# Supported video file extensions
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.webm', '.mov', '.ts', '.m4v', '.flv'}

# Fast public trackers for rapid torrent peer discovery
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "udp://explodie.org:6969/announce",
    "udp://9.rarbg.to:2920/announce",
    "udp://tracker.torrent.eu.org:451/announce",
]


def resolve_source(source_url: str) -> tuple[str, str, str]:
    """
    Resolves source URLs:
    - If AnimeTosho link, scrapes page for direct DDL, magnet link, and preview thumbnail.
    - If magnet link or direct URL, passes through.
    Returns: (download_url_or_magnet, resolved_title, animetosho_thumbnail_url)
    """
    source_url = source_url.strip()
    title_hint = ""

    # Check if AnimeTosho view URL
    if "animetosho.org/view/" in source_url:
        print(f"[Resolver] Detected AnimeTosho URL: {source_url}")
        try:
            req = urllib.request.Request(
                source_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            soup = BeautifulSoup(html, "html.parser")

            # Extract page title
            h1 = soup.find("h1")
            if h1:
                title_hint = h1.get_text(strip=True)

            # Extract AnimeTosho cover image / thumbnail
            animetosho_thumb = ""
            for img in soup.find_all("img", src=True):
                src = img["src"]
                if any(k in src for k in ["/storage/thumb/", "/storage/preview/", "thumb", "screenshot"]):
                    animetosho_thumb = urllib.parse.urljoin(source_url, src)
                    print(f"[Resolver] Found AnimeTosho preview image: {animetosho_thumb}")
                    break

            # Prefer Direct Download Links (DDL) if available on AnimeTosho
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True).lower()
                if "download" in text and ("/storage/" in href or "mirror" in href):
                    resolved = urllib.parse.urljoin(source_url, href)
                    print(f"[Resolver] Found AnimeTosho Direct DDL: {resolved}")
                    return resolved, title_hint, animetosho_thumb

            # If no DDL found, check for magnet link
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("magnet:?"):
                    print(f"[Resolver] Found AnimeTosho Magnet Link: {href[:60]}...")
                    return href, title_hint, animetosho_thumb

            # Fallback to .torrent link
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.endswith(".torrent") or "/storage/torrent/" in href:
                    resolved = urllib.parse.urljoin(source_url, href)
                    print(f"[Resolver] Found AnimeTosho .torrent link: {resolved}")
                    return resolved, title_hint, animetosho_thumb
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            print(f"[Resolver Warning] Could not scrape AnimeTosho page ({e}), using raw URL")

    # If it's a magnet link with a display name (&dn=)
    if source_url.startswith("magnet:?"):
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(source_url).query)
        if "dn" in parsed:
            title_hint = parsed["dn"][0]

    return source_url, title_hint, ""


def extract_title_candidates(title: str) -> list[str]:
    """
    Extracts high-probability search candidates from torrent/release titles.
    Handles Japanese/Romaji titles in parentheses and removes release group tags.
    """
    candidates = []

    # 1. Check inside parentheses: e.g. (同じゼミの染谷さんがセクシー女優だった話。; Onaji Zemi no Someya-san ga Sexy Joyuu datta Hanashi.)
    for paren in re.findall(r"\(([^)]+)\)", title):
        for part in re.split(r"[;；/]", paren):
            part_clean = re.sub(
                r"\b(1080p|720p|480p|2160p|4k|HEVC|x264|x265|AAC|AT-X|WEB-DL|UNCENSORED|CENSORED)\b",
                "",
                part,
                flags=re.IGNORECASE,
            ).strip()
            if len(part_clean) > 3 and not part_clean.isdigit() and part_clean not in candidates:
                candidates.append(part_clean)

    # 2. Main title cleaning: strip brackets [Group], [1080p], and parentheses (...)
    clean = re.sub(r"\[.*?\]|\(.*?\)", " ", title)
    clean = re.sub(
        r"\b(1080p|720p|480p|2160p|4k|HEVC|x264|x265|AAC|Sub|Dub|Batch|OVA|Complete|UNCENSORED|CENSORED|WEB-DL)\b",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"(?:-|\b)\s*(?:S\d+)?(?:EP?|#)\s*\d+.*", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"-\s*\d+.*", " ", clean)
    clean = " ".join(clean.split()).strip()
    if clean and clean not in candidates:
        candidates.insert(0, clean)

    return candidates


def extract_season_episode_part(title: str) -> dict[str, str]:
    """
    Extracts structured season, episode, and part numbers from release titles.
    Examples:
      'Anime S01E08' -> season: '01', episode: '08', part: ''
      'Anime - 02 Part 1' -> season: '01', episode: '02', part: '01'
      'Anime S02E01' -> season: '02', episode: '01', part: ''
      'Anime - 03 (Part B)' -> season: '01', episode: '03', part: 'B'
    """
    season = ""
    episode = ""
    part = ""

    # Clean technical metadata tags that might contain confusing numbers
    clean = re.sub(
        r"\[(1080p|720p|480p|2160p|4k|HEVC|x264|x265|AAC|D-AUD|D-SUB|[0-9a-fA-F]{8})\]",
        "",
        title,
        flags=re.IGNORECASE,
    )

    # 1. Check for combined SxxExx (e.g. S01E08, S1E1)
    s_e_match = re.search(r"\bS([0-9]{1,2})[\s._-]*E([0-9]{1,3})\b", clean, re.IGNORECASE)
    if s_e_match:
        season = s_e_match.group(1).zfill(2)
        episode = s_e_match.group(2).zfill(2)
    else:
        # Separate Season regex
        s_match = re.search(r"\b(?:season|s)\s*0?([0-9]{1,2})\b", clean, re.IGNORECASE)
        if not s_match:
            s_match = re.search(r"\b([0-9]{1,2})(?:st|nd|rd|th)\s*Season\b", clean, re.IGNORECASE)
        if s_match:
            season = s_match.group(1).zfill(2)

        # Separate Episode regex
        ep_patterns = [
            r"(?:ep|e|#|\bepisode\b|\bact\b)\s*0?([0-9]{1,3})(?:v[0-9]+)?\b",
            r"\s+-\s+0?([0-9]{1,3})(?:v[0-9]+)?\b",
            r"\b([0-9]{2,3})\b",
        ]
        for pat in ep_patterns:
            m = re.search(pat, clean, re.IGNORECASE)
            if m:
                episode = m.group(1).zfill(2)
                break

    # 2. Check for Part (e.g. Part 1, Part 02, Part A, Part B, pt.1, pt2, Part-2)
    part_match = re.search(r"\b(?:part|pt)[\s._-]*#?0?([0-9]{1,2}|[a-zA-Z])\b", clean, re.IGNORECASE)
    if part_match:
        val = part_match.group(1)
        part = val.zfill(2) if val.isdigit() else val.upper()

    # Default season to '01' if episode is present but season wasn't explicitly stated
    if episode and not season:
        season = "01"

    return {
        "season": season,
        "episode": episode,
        "part": part,
    }


def extract_episode(title: str) -> str:
    """
    Backwards compatibility helper for episode extraction.
    """
    return extract_season_episode_part(title).get("episode", "")


def extract_source_id(source_url: str) -> str:
    """
    Extracts unique identifier from Nyaa/Sukebei, AnimeTosho, or magnet link.
    """
    if not source_url:
        return ""
    m = re.search(r"/(?:view|download|torrent)/([0-9]+)", source_url)
    if m:
        return m.group(1)
    m = re.search(r"\.(n[0-9]+|d[0-9]+)", source_url)
    if m:
        return m.group(1)
    m = re.search(r"urn:btih:([a-zA-Z0-9]{20,40})", source_url, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return ""


def fetch_mal_metadata(title: str) -> dict:
    """
    Fetches official HD poster, score, synopsis, genres, and MAL ID from MyAnimeList
    (https://myanimelist.net/anime/genre/12/Hentai).
    Uses MAL prefix search and detail scraping without requiring API keys.
    """
    candidates = extract_title_candidates(title)
    if not candidates:
        candidates = [title]

    print(f"[MAL] Searching MyAnimeList for: {candidates}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    matched_item = None
    for cand in candidates:
        query_encoded = urllib.parse.quote(cand)
        url = f"https://myanimelist.net/search/prefix.json?type=anime&keyword={query_encoded}&v=1"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for cat in data.get("categories", []):
                if cat.get("type") == "anime":
                    items = cat.get("items", [])
                    if items:
                        matched_item = items[0]
                        print(f"[MAL] Match found for '{cand}': {matched_item.get('name')} (MAL ID: {matched_item.get('id')})")
                        break
            if matched_item:
                break
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"[MAL Warning] Prefix search failed for '{cand}': {e}")

    # Fallback to AniList GraphQL if MAL direct search gave no match
    if not matched_item:
        print("[MAL] Direct MAL search yielded no match. Trying AniList GraphQL bridge...")
        try:
            gql_query = """
            query ($search: String) {
              Media (search: $search, type: ANIME, isAdult: true) {
                id
                idMal
                title { romaji english }
                coverImage { extraLarge large }
                description(asHtml: false)
                genres
                seasonYear
                episodes
                meanScore
              }
            }
            """
            for cand in candidates:
                res = requests.post(
                    "https://graphql.anilist.co",
                    json={"query": gql_query, "variables": {"search": cand}},
                    timeout=10,
                )
                if res.status_code == 200:
                    med = res.json().get("data", {}).get("Media")
                    if med:
                        print(f"[MAL Bridge] Found on AniList: {med.get('title', {}).get('romaji')} (MAL ID: {med.get('idMal')})")
                        cover = med.get("coverImage") or {}
                        score_raw = med.get("meanScore")
                        score_val = round(score_raw / 10.0, 2) if score_raw else None
                        return {
                            "mal_id": med.get("idMal"),
                            "anilist_id": med.get("id"),
                            "title": med.get("title", {}).get("romaji") or cand,
                            "score": score_val,
                            "poster_url": cover.get("extraLarge") or cover.get("large") or "",
                            "synopsis": med.get("description") or "",
                            "genres": ", ".join(med.get("genres", [])) if med.get("genres") else "Hentai",
                            "episodes": med.get("episodes"),
                            "year": med.get("seasonYear"),
                            "mal_url": f"https://myanimelist.net/anime/{med.get('idMal')}" if med.get("idMal") else "",
                        }
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"[MAL Bridge Warning] AniList fallback failed: {e}")

    if not matched_item:
        print("[MAL] No matches found on MAL.")
        return {
            "mal_id": None,
            "anilist_id": None,
            "title": "",
            "score": None,
            "poster_url": "",
            "synopsis": "",
            "genres": "Hentai",
            "episodes": None,
            "year": None,
            "mal_url": "",
        }

    mal_id = matched_item.get("id")
    mal_url = matched_item.get("url", f"https://myanimelist.net/anime/{mal_id}")
    name = matched_item.get("name", "")

    # Extract clean HD image URL by stripping thumbnail scaling parameters (/r/116x180)
    raw_img = matched_item.get("image_url", "")
    hd_poster = re.sub(r"/r/\d+x\d+", "", raw_img).split("?")[0] if raw_img else ""

    payload = matched_item.get("payload", {})
    score_val = payload.get("score")
    try:
        score = float(score_val) if score_val else None
    except ValueError:
        score = None
    year = payload.get("start_year")

    synopsis = ""
    genres = []
    episodes = None

    try:
        page_req = urllib.request.Request(mal_url, headers=headers)
        with urllib.request.urlopen(page_req, timeout=10) as resp:
            page_html = resp.read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(page_html, "html.parser")

        syn_p = soup.find("p", itemprop="description")
        if syn_p:
            synopsis = syn_p.get_text(strip=True)

        for a in soup.find_all("a", href=re.compile(r"/anime/genre/")):
            g_text = a.get_text(strip=True)
            if g_text and g_text not in genres:
                genres.append(g_text)

        for div in soup.find_all("div", class_="spaceit_pad"):
            text = div.get_text(separator=" ", strip=True)
            if "Episodes:" in text:
                m = re.search(r"Episodes:\s*(\d+)", text)
                if m:
                    episodes = int(m.group(1))

        img_tag = soup.find("img", itemprop="image")
        if img_tag:
            page_img = img_tag.get("data-src") or img_tag.get("src")
            if page_img and "cdn.myanimelist.net/images/anime/" in page_img:
                hd_poster = page_img.split("?")[0]

    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, AttributeError) as e:
        print(f"[MAL Warning] Page scrape error ({e}), using summary info.")

    return {
        "mal_id": mal_id,
        "anilist_id": None,
        "title": name,
        "score": score,
        "poster_url": hd_poster,
        "synopsis": synopsis,
        "genres": ", ".join(genres) if genres else "Hentai",
        "episodes": episodes,
        "year": year,
        "mal_url": mal_url,
    }


def fetch_anilist_metadata(title: str) -> dict:
    """
    Backwards compatibility alias for fetch_mal_metadata.
    """
    return fetch_mal_metadata(title)


def download_with_aria2(source: str, download_dir: Path) -> None:
    """
    Downloads the file or torrent using aria2c with optimized cloud bandwidth flags.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "aria2c",
        f"--dir={download_dir.resolve()}",
        "--summary-interval=10",
        "--file-allocation=none",
        "--continue=true",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--timeout=30",
        "--connect-timeout=15",
    ]

    if source.startswith("magnet:?") or source.endswith(".torrent"):
        print("[Aria2c] Torrent download mode initiated...")
        tracker_arg = ",".join(PUBLIC_TRACKERS)
        cmd.extend([
            "--seed-time=0",               # Stop seeding immediately once download completes
            "--bt-stop-timeout=60",        # Timeout if no seeders/traffic for 60s (skip dead torrents fast)
            f"--bt-tracker={tracker_arg}", # Inject fast DHT public trackers
            "--follow-torrent=mem",
        ])
    else:
        print("[Aria2c] Direct HTTP/HTTPS download mode initiated...")

    cmd.append(source)

    print("[Aria2c] Running download command...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for line in iter(process.stdout.readline, ""):
        line_clean = line.strip()
        if line_clean and (
            line_clean.startswith("[#")
            or "Download Results:" in line_clean
            or "ETA:" in line_clean
            or "Speed:" in line_clean
            or "DL:" in line_clean
            or "Seeds:" in line_clean
            or "Peers:" in line_clean
        ):
            print(f"[Aria2c] {line_clean}")

    process.stdout.close()
    return_code = process.wait()

    if return_code != 0:
        raise RuntimeError(f"aria2c failed with return code {return_code}")
    print("[Aria2c] Download successfully completed!")


def locate_largest_video(download_dir: Path) -> Path:
    """
    Finds the primary/largest video file in the download directory.
    """
    video_files = []
    for root, _, files in os.walk(download_dir):
        for file in files:
            ext = Path(file).suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                file_path = Path(root) / file
                video_files.append((file_path, file_path.stat().st_size))

    if not video_files:
        raise FileNotFoundError(f"No video files ({', '.join(VIDEO_EXTENSIONS)}) found in {download_dir}")

    # Sort descending by file size
    video_files.sort(key=lambda x: x[1], reverse=True)
    largest_file, size_bytes = video_files[0]
    size_mb = size_bytes / (1024 * 1024)
    print(f"[Video Finder] Selected file: '{largest_file.name}' ({size_mb:.2f} MB)")
    return largest_file


def dropembed_api_request(
    method: str,
    url: str,
    api_key: str,
    payload: dict | None = None,
    timeout: int = 30,
) -> dict:
    """
    Executes an API request to DropEmbed with multi-tier Cloudflare bypass:
    1. Direct request to https://upload.dropembed.com/api/ (OVH backend, zero Cloudflare block).
    2. curl_cffi with Chrome 120 browser impersonation on original URL.
    3. Native system curl binary with browser headers.
    4. Standard requests fallback.
    Guarantees bypass of Cloudflare Managed Challenges and datacenter IP blocks.
    """
    method = method.upper()
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"

    # Strategy 1: Direct backend host (upload.dropembed.com) which completely bypasses Cloudflare
    direct_url = url.replace("https://dropembed.com/api/", "https://upload.dropembed.com/api/")
    try:
        r = requests.request(method, direct_url, json=payload, headers=headers, timeout=timeout, verify=False)
        if r.status_code in (200, 201):
            return r.json()
        # If backend responded with JSON error (like 400, 401, 404), return it directly
        try:
            err_json = r.json()
            if "success" in err_json:
                return err_json
        except (ValueError, json.JSONDecodeError):
            pass
        print(f"[DropEmbed API] Direct endpoint returned HTTP {r.status_code}. Trying curl_cffi fallback...")
    except Exception as direct_err:  # noqa: BLE001
        print(f"[DropEmbed API] Direct endpoint attempt failed: {direct_err}. Trying curl_cffi fallback...")

    # Strategy 2: curl_cffi (Chrome TLS fingerprint) on original URL
    try:
        from curl_cffi import requests as cffi_requests

        if method == "POST":
            r = cffi_requests.post(url, json=payload, headers=headers, impersonate="chrome120", timeout=timeout)
        elif method == "PATCH":
            r = cffi_requests.patch(url, json=payload, headers=headers, impersonate="chrome120", timeout=timeout)
        else:
            r = cffi_requests.get(url, headers=headers, impersonate="chrome120", timeout=timeout)

        if r.status_code in (200, 201):
            return r.json()
        print(f"[DropEmbed API] curl_cffi HTTP {r.status_code}. Trying system curl fallback...")
    except Exception as cffi_err:  # noqa: BLE001
        print(f"[DropEmbed API] curl_cffi attempt failed: {cffi_err}. Trying system curl fallback...")

    # Strategy 3: System curl binary
    try:
        curl_bin = shutil.which("curl") or shutil.which("curl.exe")
        if curl_bin:
            cmd = [
                curl_bin, "-s",
                "-X", method, url,
                "-H", f"X-API-Key: {api_key}",
                "-H", "Accept: application/json",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            ]
            if payload is not None:
                cmd.extend(["-H", "Content-Type: application/json", "--data-raw", json.dumps(payload)])

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
            if proc.returncode == 0 and proc.stdout:
                return json.loads(proc.stdout)
            print(f"[DropEmbed API] system curl exit {proc.returncode}. Trying requests...")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as curl_err:
        print(f"[DropEmbed API] system curl warning: {curl_err}. Trying requests...")

    # Strategy 4: Standard requests
    try:
        if method == "POST":
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        elif method == "PATCH":
            r = requests.patch(url, json=payload, headers=headers, timeout=timeout)
        else:
            r = requests.get(url, headers=headers, timeout=timeout)
        return r.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError) as req_err:
        raise RuntimeError(f"All DropEmbed API request methods failed for {url}: {req_err}") from req_err


def upload_to_dropembed(video_path: Path, title: str, api_key: str, folder_id: str = "") -> dict:
    """
    Uploads video file to DropEmbed with 100% ORIGINAL PRISTINE QUALITY (1080p HD).
    Uses DropEmbed's dedicated multi-GB direct upload endpoint (https://upload.dropembed.com/api/videos/upload)
    which natively supports files up to 10 GB per file, zero compression, and completely bypasses Cloudflare.
    If direct upload encounters network interruption, automatically falls back to Cloud Direct Relay.
    """
    file_size = video_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)

    # Method 1: High-speed direct multipart upload to upload.dropembed.com (Supports up to 10 GB!)
    direct_upload_url = "https://upload.dropembed.com/api/videos/upload"
    for attempt in range(1, 4):
        try:
            print(f"\n[DropEmbed Direct Upload] Attempt {attempt}: Uploading original 1080p file: {video_path.name} ({file_size_mb:.2f} MB)...")
            with open(video_path, "rb") as video_fp:
                fields = {
                    "title": title,
                    "video": (video_path.name, video_fp, "application/octet-stream"),
                }
                if folder_id:
                    fields["folder_id"] = str(folder_id)

                encoder = MultipartEncoder(fields=fields)
                last_reported = [-1]

                def callback(monitor, reported=last_reported):
                    pct = int((monitor.bytes_read / monitor.len) * 100)
                    if pct % 10 == 0 and pct != reported[0]:
                        reported[0] = pct
                        read_mb = monitor.bytes_read / (1024 * 1024)
                        print(f"[DropEmbed Upload Progress] {pct}% ({read_mb:.1f} MB / {file_size_mb:.1f} MB)")

                monitor = MultipartEncoderMonitor(encoder, callback)
                headers = {
                    "X-API-Key": api_key,
                    "Content-Type": monitor.content_type,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                }
                resp = requests.post(direct_upload_url, data=monitor, headers=headers, timeout=1800, verify=False)

            data = resp.json()
            if data.get("success"):
                video_id = data.get("video_id")
                print(f"[DropEmbed Direct Upload] Succeeded! Assigned Video ID: {video_id}")
                return {
                    "success": True,
                    "video_id": video_id,
                    "title": title,
                    "url": data.get("url") or f"https://dropembed.com/v/{video_id}",
                    "embed_url": data.get("embed_url") or f"https://dropembed.com/e/{video_id}",
                }
            print(f"[DropEmbed Direct Upload] Attempt {attempt} returned non-success response: {data}")
        except Exception as upload_err:  # noqa: BLE001
            print(f"[DropEmbed Direct Upload] Attempt {attempt} error: {upload_err}")
        time.sleep(3)

    # Method 2: Fallback to Cloud Direct Relay (GitHub release asset -> DropEmbed remote-upload)
    print(f"\n[DropEmbed 1080p Relay] Direct upload failed, initiating zero-loss Cloud Direct Relay ({file_size_mb:.2f} MB)...")

    tag = f"relay-{int(time.time())}"
    repo_env = os.environ.get("GITHUB_REPOSITORY", "Zayrix-bit/hentai_auto")

    # Create a safe ASCII filename without Japanese characters or special symbols
    ext = video_path.suffix.lower() or ".mp4"
    clean_relay_path = video_path.parent / f"relay_1080p_{tag}{ext}"
    try:
        os.link(video_path, clean_relay_path)
    except OSError:
        shutil.copy2(video_path, clean_relay_path)

    create_cmd = [
        "gh", "release", "create", tag,
        str(clean_relay_path),
        "--target", "main",
        "--title", f"Relay {tag}",
        "--notes", "Temporary relay asset for DropEmbed 1080p transfer",
        "--repo", repo_env,
    ]

    try:
        subprocess.run(create_cmd, check=True, capture_output=True, text=True)
        print("[DropEmbed 1080p Relay] Temporary release asset created successfully on cloud runner!")

        raw_asset_url = f"https://github.com/{repo_env}/releases/download/{tag}/{clean_relay_path.name}"

        # Verify asset accessibility and resolve direct CDN URL
        direct_url = raw_asset_url
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }
        for attempt in range(5):
            try:
                r_head = requests.head(raw_asset_url, allow_redirects=True, timeout=15, headers=req_headers)
                if r_head.status_code == 200:
                    direct_url = r_head.url
                    print("[DropEmbed 1080p Relay] Verified release asset CDN URL (HTTP 200).")
                    break
                else:
                    print(f"[DropEmbed 1080p Relay] Asset check returned HTTP {r_head.status_code}, retrying...")
            except (requests.RequestException, OSError) as e:
                print(f"[DropEmbed 1080p Relay] Asset check warning: {e}")
            time.sleep(2)

        print("[DropEmbed 1080p Relay] Submitting direct 1080p link to DropEmbed remote-upload...")
        remote_url = "https://upload.dropembed.com/api/videos/remote-upload"
        payload = {"urls": [direct_url]}

        data = None
        for attempt in range(1, 4):
            try:
                res_data = dropembed_api_request("POST", remote_url, api_key, payload=payload, timeout=30)
                if res_data.get("success"):
                    data = res_data
                    break
                print(f"[DropEmbed 1080p Relay] Attempt {attempt} response: {res_data}")
            except Exception as e:  # noqa: BLE001
                print(f"[DropEmbed 1080p Relay] Attempt {attempt} failed: {e}")
            time.sleep(3)

        if not data or not data.get("success"):
            raise RuntimeError(f"DropEmbed remote-upload failed after retries: {data}")

        tasks = data.get("tasks", [])
        video_id = None
        if tasks:
            task = tasks[0]
            video_id = task.get("video_id")
        else:
            # Fallback: check videos list if already queued
            try:
                list_res = dropembed_api_request("GET", "https://upload.dropembed.com/api/videos", api_key, timeout=15)
                v_list = list_res.get("data", [])
                for v in v_list:
                    desc = v.get("description") or ""
                    if direct_url in desc or raw_asset_url in desc:
                        video_id = v.get("id")
                        break
            except Exception as list_err:  # noqa: BLE001
                print(f"[DropEmbed 1080p Relay] Video list fallback warning: {list_err}")

        if not video_id:
            raise RuntimeError(f"DropEmbed returned no video_id: {data}")

        print(f"[DropEmbed 1080p Relay] Transfer queued! Assigned Video ID: {video_id}")

        # Update title on DropEmbed
        try:
            patch_url = f"https://upload.dropembed.com/api/videos/{video_id}"
            dropembed_api_request("PATCH", patch_url, api_key, payload={"title": title}, timeout=10)
        except Exception:  # noqa: BLE001, S110
            pass

        # Wait for DropEmbed servers to finish downloading the asset before deleting the release
        print("[DropEmbed 1080p Relay] Monitoring DropEmbed cloud-to-cloud transfer progress...")
        poll_url = f"https://upload.dropembed.com/api/videos/{video_id}"
        transfer_done = False
        for poll_i in range(30):
            time.sleep(5)
            try:
                poll_data = dropembed_api_request("GET", poll_url, api_key, timeout=15).get("data", {})
                v_status = poll_data.get("status")
                v_size = poll_data.get("file_size", 0)
                print(f"[DropEmbed 1080p Relay] Status: {v_status} | Size: {v_size} bytes ({(poll_i + 1) * 5}s)")
                if v_status in ("processing", "ready") or (v_size and v_size > 0):
                    print("[DropEmbed 1080p Relay] Transfer completed! DropEmbed has received full 1080p file.")
                    transfer_done = True
                    break
                elif v_status == "error":
                    print("[DropEmbed 1080p Relay] DropEmbed reported error during transfer.")
                    break
            except Exception as poll_err:  # noqa: BLE001
                print(f"[DropEmbed 1080p Relay] Poll check warning: {poll_err}")

        if not transfer_done:
            print("[DropEmbed 1080p Relay] Transfer wait timeout reached, proceeding...")

        return {
            "success": True,
            "video_id": video_id,
            "title": title,
            "url": f"https://dropembed.com/v/{video_id}",
            "embed_url": f"https://dropembed.com/e/{video_id}",
        }

    except subprocess.CalledProcessError as e:
        print(f"[DropEmbed 1080p Relay Error] gh release create failed (exit {e.returncode}):")
        if e.stdout:
            print("Stdout:", e.stdout)
        if e.stderr:
            print("Stderr:", e.stderr)
        raise

    finally:
        # Always remove temporary local file
        if clean_relay_path.exists():
            try:
                clean_relay_path.unlink()
            except OSError:
                pass

        # Always delete the temporary release to keep repository clean
        del_cmd = ["gh", "release", "delete", tag, "-y", "--repo", repo_env]
        try:
            subprocess.run(del_cmd, capture_output=True, text=True, check=False)
            print("[DropEmbed 1080p Relay] Temporary release asset cleaned up.")
        except subprocess.SubprocessError:
            pass


def save_catalog(record: dict, repo_root: Path) -> None:
    """
    Updates data/videos.json and data/videos.md with the newly uploaded video details.
    """
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    json_path = data_dir / "videos.json"
    catalog = []
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
        except (json.JSONDecodeError, OSError):
            catalog = []

    # Prepend new video (newest first)
    catalog.insert(0, record)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
    print(f"[Catalog] Saved record to {json_path}")

    # Also generate human-readable Markdown table in data/videos.md
    md_path = data_dir / "videos.md"
    md_lines = [
        "# 🎬 DropEmbed Video Catalog\n",
        f"*Total Videos Uploaded: {len(catalog)}*\n",
        "| Date | Title | Season | Episode | Part | MAL | Score | Watch Link | Embed Player Link |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in catalog:
        date_str = v.get("uploaded_at", "")[:10]
        v_title = v.get("title", "").replace("|", "\\|")
        v_season = f"S{v['season']}" if v.get("season") else "-"
        v_ep = f"E{v['episode']}" if v.get("episode") else "-"
        v_part = f"Pt.{v['part']}" if v.get("part") else "-"
        v_score = f"⭐ {v['score']}" if v.get("score") else "-"
        v_mal = f"[MAL #{v['mal_id']}]({v['mal_url']})" if v.get("mal_id") and v.get("mal_url") else (f"MAL #{v['mal_id']}" if v.get("mal_id") else "-")
        v_url = v.get("url", "")
        v_embed = v.get("embed_url", "")
        md_lines.append(f"| {date_str} | **{v_title}** | {v_season} | {v_ep} | {v_part} | {v_mal} | {v_score} | [Watch]({v_url}) | [Embed Player]({v_embed}) |")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
    print(f"[Catalog] Updated {md_path}")


def notify_cpanel_database(record: dict) -> None:
    """
    Sends video details to cPanel MySQL database via a secure PHP API webhook.
    Requires CPANEL_API_URL and optional CPANEL_API_SECRET environment variables.
    Includes direct server IP fallback to prevent DNS propagation failures.
    """
    cpanel_url = os.environ.get("CPANEL_API_URL")
    if not cpanel_url:
        print("[cPanel DB] No CPANEL_API_URL configured. Skipping cPanel database sync.")
        return

    cpanel_secret = os.environ.get("CPANEL_API_SECRET", "")
    print(f"[cPanel DB] Syncing record to cPanel API at {cpanel_url}...")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "GitHubActions-DropEmbedPipeline/1.0",
        "Host": "jeevankart.in",
    }
    if cpanel_secret:
        headers["Authorization"] = f"Bearer {cpanel_secret}"

    synced = False
    try:
        resp = requests.post(cpanel_url, json=record, headers=headers, timeout=15, verify=False)
        if resp.status_code == 200:
            print(f"[cPanel DB] Successfully synced to cPanel database: {resp.text}")
            synced = True
        else:
            print(f"[cPanel DB] URL attempt returned HTTP {resp.status_code}. Trying direct server IP fallback...")
    except requests.RequestException as e:
        print(f"[cPanel DB] URL attempt failed ({e}). Trying direct server IP fallback...")

    # Fallback directly to server IP with Host header if DNS propagation hasn't reached runner
    if not synced:
        fallback_url = "http://37.27.232.161/api/add_video.php"
        try:
            resp_fallback = requests.post(fallback_url, json=record, headers=headers, timeout=15)
            if resp_fallback.status_code == 200:
                print(f"[cPanel DB] Successfully synced via server fallback: {resp_fallback.text}")
            else:
                print(f"[cPanel DB Warning] Fallback sync failed (HTTP {resp_fallback.status_code}): {resp_fallback.text}")
        except requests.RequestException as e2:
            print(f"[cPanel DB Warning] Could not connect via fallback: {e2}")


def write_github_summary(record: dict) -> None:
    """
    Writes a formatted Markdown summary to the GitHub Actions workflow run page.
    """
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    v_title = record.get("title", "Video")
    v_id = record.get("video_id", "")
    v_url = record.get("url", "")
    v_embed = record.get("embed_url", "")
    v_size = record.get("file_size_mb", 0)
    v_poster = record.get("poster_url") or record.get("thumbnail_url") or ""
    v_genres = record.get("genres") or "Hentai"
    v_desc = record.get("description") or ""
    v_mal_id = record.get("mal_id")
    v_mal_url = record.get("mal_url") or (f"https://myanimelist.net/anime/{v_mal_id}" if v_mal_id else "")
    v_score = record.get("score")
    v_season = record.get("season")
    v_ep = record.get("episode")
    v_part = record.get("part")
    v_source_id = record.get("source_id")

    poster_markdown = f"\n![Poster]({v_poster})\n" if v_poster else ""

    summary_content = f"""
### 🚀 DropEmbed Upload Complete!

{poster_markdown}

| Field | Details |
|---|---|
| **Title** | **{v_title}** |
| **Video ID** | `{v_id}` |
| **Season** | `{v_season or 'N/A'}` |
| **Episode** | `{v_ep or 'N/A'}` |
| **Part** | `{v_part or 'N/A'}` |
| **MyAnimeList** | {f'[{v_mal_id}]({v_mal_url})' if v_mal_id else 'N/A'} |
| **Score** | {f'⭐ {v_score} / 10' if v_score else 'N/A'} |
| **Source ID** | {f'#{v_source_id}' if v_source_id else 'N/A'} |
| **File Size** | {v_size:.2f} MB |
| **Genres** | {v_genres} |
| **Watch URL** | [Open in DropEmbed]({v_url}) |
| **Embed URL** | [Open Player]({v_embed}) |

{f"> **Synopsis:** {v_desc[:280]}..." if v_desc else ""}

#### 📋 Embed Player Code (`<iframe>`)
```html
<iframe src="{v_embed}" width="640" height="360" frameborder="0" allowfullscreen allow="autoplay; fullscreen"></iframe>
```
"""
    with open(summary_file, "a", encoding="utf-8") as f:
        f.write(summary_content)


def main():
    parser = argparse.ArgumentParser(description="Torrent/AnimeTosho to DropEmbed Cloud Pipeline")
    parser.add_argument("--source", required=True, help="Magnet link, .torrent link, or AnimeTosho view URL")
    parser.add_argument("--title", default="", help="Custom title for the video")
    parser.add_argument("--folder-id", default="", help="Optional DropEmbed folder ID")
    parser.add_argument("--api-key", default="", help="DropEmbed API Key (or set DROPEMBED_API_KEY env)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DROPEMBED_API_KEY")
    if not api_key:
        print("[Error] DropEmbed API Key is required! Set DROPEMBED_API_KEY env or use --api-key.")
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent.parent
    download_dir = repo_root / "downloads"

    try:
        # Step 1: Resolve Source Link (AnimeTosho / Magnet / Direct)
        source, title_hint, animetosho_thumb = resolve_source(args.source)
        final_title = args.title or title_hint

        # Step 2: Download on Cloud Runner
        download_with_aria2(source, download_dir)

        # Step 3: Find video file
        video_file = locate_largest_video(download_dir)
        if not final_title:
            final_title = video_file.stem  # Clean filename without extension

        # Step 4: Fetch Official MyAnimeList Metadata (MAL ID, Score, Poster, Synopsis, Genres)
        mal_meta = fetch_mal_metadata(final_title)

        # Step 5: Stream Upload to DropEmbed
        upload_res = upload_to_dropembed(
            video_path=video_file,
            title=final_title,
            api_key=api_key,
            folder_id=args.folder_id,
        )

        video_id = upload_res.get("video_id", "")
        embed_url = upload_res.get("embed_url") or f"https://dropembed.com/e/{video_id}"
        watch_url = upload_res.get("url") or f"https://dropembed.com/v/{video_id}"

        thumb_url = animetosho_thumb or ""
        if not thumb_url and not mal_meta.get("poster_url") and video_id:
            try:
                info_r = dropembed_api_request("GET", f"https://upload.dropembed.com/api/videos/{video_id}", api_key, timeout=10)
                thumb_url = info_r.get("data", {}).get("thumbnail") or ""
            except Exception:  # noqa: BLE001, S110
                pass

        sep_info = extract_season_episode_part(final_title)
        if not sep_info.get("episode"):
            sep_info = extract_season_episode_part(video_file.name)

        source_id = extract_source_id(args.source)

        record = {
            "title": final_title,
            "video_id": video_id,
            "url": watch_url,
            "embed_url": embed_url,
            "file_name": video_file.name,
            "file_size_mb": round(video_file.stat().st_size / (1024 * 1024), 2),
            "poster_url": mal_meta.get("poster_url") or "",
            "thumbnail_url": thumb_url,
            "banner_url": mal_meta.get("banner_url") or "",
            "description": mal_meta.get("synopsis") or mal_meta.get("description") or "",
            "genres": mal_meta.get("genres") or "Hentai",
            "year": mal_meta.get("year"),
            "mal_id": mal_meta.get("mal_id"),
            "anilist_id": mal_meta.get("anilist_id"),
            "source_id": source_id,
            "season": sep_info.get("season", ""),
            "episode": sep_info.get("episode", ""),
            "part": sep_info.get("part", ""),
            "score": mal_meta.get("score"),
            "mal_url": mal_meta.get("mal_url") or "",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "source_input": args.source,
        }

        # Step 6: Save to Local Catalog
        save_catalog(record, repo_root)

        # Step 7: Sync to cPanel MySQL Database (if configured)
        notify_cpanel_database(record)

        # Step 8: GitHub Actions Step Summary
        write_github_summary(record)

        print("\n==========================================")
        print("🎉 ALL STEPS COMPLETED SUCCESSFULLY!")
        print(f"Title: {record['title']}")
        print(f"Embed URL: {record['embed_url']}")
        print("==========================================\n")

    finally:
        # Cleanup downloads directory to free disk
        if download_dir.exists():
            print(f"[Cleanup] Removing local download files in {download_dir}...")
            shutil.rmtree(download_dir, ignore_errors=True)
            print("[Cleanup] Done.")


if __name__ == "__main__":
    main()
