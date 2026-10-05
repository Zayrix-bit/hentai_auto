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
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import requests
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


def fetch_anilist_metadata(title: str) -> dict:
    """
    Fetches official HD vertical poster, banner, description, and genres from AniList GraphQL API.
    """
    url = "https://graphql.anilist.co"
    # Clean release brackets, resolutions, episode numbers for search accuracy
    clean_title = re.sub(r"\[.*?\]|\(.*?\)", "", title)
    clean_title = re.sub(
        r"\b(1080p|720p|480p|HEVC|x264|x265|AAC|Sub|Dub|Batch|OVA|Complete)\b",
        "",
        clean_title,
        flags=re.IGNORECASE,
    )
    clean_title = re.sub(r"-\s*\d+.*", "", clean_title).strip()
    if not clean_title:
        clean_title = title

    print(f"[AniList] Searching official anime metadata for: '{clean_title}'...")

    gql_query = """
    query ($search: String) {
      Media (search: $search, type: ANIME) {
        id
        title {
          romaji
          english
        }
        coverImage {
          extraLarge
          large
        }
        bannerImage
        description(asHtml: false)
        genres
        seasonYear
      }
    }
    """

    try:
        resp = requests.post(
            url,
            json={"query": gql_query, "variables": {"search": clean_title}},
            timeout=12,
        )
        if resp.status_code == 200:
            data = resp.json()
            media = data.get("data", {}).get("Media")
            if media:
                cover = media.get("coverImage") or {}
                poster = cover.get("extraLarge") or cover.get("large") or ""
                genres = ", ".join(media.get("genres", []))
                print(f"[AniList] Found: {media.get('title', {}).get('romaji')} (Year: {media.get('seasonYear')})")
                print(f"[AniList] Poster URL: {poster}")
                return {
                    "poster_url": poster,
                    "banner_url": media.get("bannerImage") or "",
                    "description": media.get("description") or "",
                    "genres": genres,
                    "year": media.get("seasonYear"),
                }
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[AniList Warning] Metadata lookup error: {e}")

    return {
        "poster_url": "",
        "banner_url": "",
        "description": "",
        "genres": "",
        "year": None,
    }


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
    ]

    if source.startswith("magnet:?") or source.endswith(".torrent"):
        print("[Aria2c] Torrent download mode initiated...")
        tracker_arg = ",".join(PUBLIC_TRACKERS)
        cmd.extend([
            "--seed-time=0",               # Stop seeding immediately once download completes
            "--bt-stop-timeout=300",       # Timeout if no seeders for 5 mins
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


def upload_to_dropembed(video_path: Path, title: str, api_key: str, folder_id: str = "") -> dict:
    """
    Uploads the video file to DropEmbed using streaming multipart/form-data.
    Uses context manager to guarantee proper file closure.
    """
    url = "https://dropembed.com/api/videos/upload"
    file_size = video_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)

    print(f"\n[DropEmbed] Uploading: {video_path.name} ({file_size_mb:.2f} MB)...")
    print(f"[DropEmbed] Target Title: {title}")

    # Open file using context manager to avoid file descriptor leaks
    with open(video_path, "rb") as video_fp:
        fields = {
            "title": title,
            "video": (video_path.name, video_fp, "application/octet-stream"),
        }
        if folder_id:
            fields["folder_id"] = str(folder_id)

        encoder = MultipartEncoder(fields=fields)

        # Progress monitor callback
        last_reported_percent = [-1]

        def callback(monitor):
            current_percent = int((monitor.bytes_read / monitor.len) * 100)
            if current_percent % 10 == 0 and current_percent != last_reported_percent[0]:
                last_reported_percent[0] = current_percent
                read_mb = monitor.bytes_read / (1024 * 1024)
                print(f"[DropEmbed Upload Progress] {current_percent}% ({read_mb:.1f} MB / {file_size_mb:.1f} MB)")

        monitor = MultipartEncoderMonitor(encoder, callback)

        headers = {
            "X-API-Key": api_key,
            "Content-Type": monitor.content_type,
        }

        response = requests.post(url, data=monitor, headers=headers, timeout=1800)

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"DropEmbed invalid JSON response (HTTP {response.status_code}): {response.text}")

    if not data.get("success"):
        raise RuntimeError(f"DropEmbed Upload failed: {data}")

    print("[DropEmbed] Upload succeeded!")
    return data


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
        "| Date | Title | Watch Link | Embed Player Link | Embed Code (`<iframe>`) |",
        "|---|---|---|---|---|",
    ]
    for v in catalog:
        date_str = v.get("uploaded_at", "")[:10]
        v_title = v.get("title", "").replace("|", "\\|")
        v_url = v.get("url", "")
        v_embed = v.get("embed_url", "")
        v_iframe = f"`<iframe src=\"{v_embed}\" width=\"640\" height=\"360\" frameborder=\"0\" allowfullscreen></iframe>`"
        md_lines.append(f"| {date_str} | **{v_title}** | [Watch]({v_url}) | [Embed Player]({v_embed}) | {v_iframe} |")

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
        resp = requests.post(cpanel_url, json=record, headers=headers, timeout=15)
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
    v_genres = record.get("genres") or "N/A"
    v_desc = record.get("description") or ""

    poster_markdown = f"\n![Poster]({v_poster})\n" if v_poster else ""

    summary_content = f"""
### 🚀 DropEmbed Upload Complete!

{poster_markdown}

| Field | Details |
|---|---|
| **Title** | **{v_title}** |
| **Video ID** | `{v_id}` |
| **File Size** | {v_size:.2f} MB |
| **Genres** | {v_genres} |
| **Watch URL** | [Open in DropEmbed]({v_url}) |
| **Embed URL** | [Open Player]({v_embed}) |

{f"> **Synopsis:** {v_desc[:250]}..." if v_desc else ""}

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

        # Step 4: Fetch Official AniList Metadata (HD Poster, Description, Genres)
        anilist_meta = fetch_anilist_metadata(final_title)

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

        record = {
            "title": final_title,
            "video_id": video_id,
            "url": watch_url,
            "embed_url": embed_url,
            "file_name": video_file.name,
            "file_size_mb": round(video_file.stat().st_size / (1024 * 1024), 2),
            "poster_url": anilist_meta.get("poster_url") or "",
            "thumbnail_url": animetosho_thumb or "",
            "banner_url": anilist_meta.get("banner_url") or "",
            "description": anilist_meta.get("description") or "",
            "genres": anilist_meta.get("genres") or "",
            "year": anilist_meta.get("year"),
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
