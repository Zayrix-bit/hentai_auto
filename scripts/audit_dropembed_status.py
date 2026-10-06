import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
vpath = REPO_ROOT / "data" / "videos.json"

with open(vpath, "r", encoding="utf-8") as f:
    catalog = json.load(f)

print(f"[Audit] Scanning DropEmbed status for all {len(catalog)} videos in catalog...")

def check_video(item):
    vid = item.get("video_id")
    if not vid:
        return {"item": item, "status": "no_video_id", "title": item.get("title")}
    
    url = f"https://dropembed.com/api/videos/{vid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        vdata = data.get("data", {})
        status = vdata.get("status", "unknown")
        has_stream = vdata.get("has_stream", False)
        return {"item": item, "video_id": vid, "status": status, "has_stream": has_stream, "title": item.get("title")}
    except urllib.error.HTTPError as e:
        return {"item": item, "video_id": vid, "status": f"http_{e.code}", "title": item.get("title")}
    except Exception as e:
        return {"item": item, "video_id": vid, "status": f"err_{str(e)[:20]}", "title": item.get("title")}

results = []
with ThreadPoolExecutor(max_workers=25) as ex:
    futures = [ex.submit(check_video, item) for item in catalog]
    for fut in as_completed(futures):
        results.append(fut.result())

by_status = {}
for r in results:
    s = r["status"]
    by_status.setdefault(s, []).append(r)

print("\n=== DROPEMBED STATUS SUMMARY ===")
for s, lst in sorted(by_status.items()):
    print(f"  * Status '{s}': {len(lst)} videos")

if "error" in by_status:
    print(f"\n❌ FAILED / ERROR VIDEOS ({len(by_status['error'])}):")
    for r in by_status["error"]:
        item = r["item"]
        print(f"  - [{r['video_id']}] {r['title']} (Source: {item.get('source_input')})")

    # Save failed videos to scratch/failed_dropembed.json
    out_failed = REPO_ROOT / "data" / "failed_dropembed_videos.json"
    with open(out_failed, "w", encoding="utf-8") as f:
        json.dump([r["item"] for r in by_status["error"]], f, indent=2)
    print(f"\nSaved {len(by_status['error'])} failed videos to {out_failed}")
