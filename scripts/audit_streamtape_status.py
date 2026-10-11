import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import urllib.request
import urllib.error

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
vpath = REPO_ROOT / "data" / "videos.json"

with open(vpath, "r", encoding="utf-8") as f:
    catalog = json.load(f)

st_login = os.environ.get("STREAMTAPE_LOGIN", "")
st_key = os.environ.get("STREAMTAPE_KEY", "")

print(f"[Audit] Scanning Streamtape status for all {len(catalog)} videos in catalog...")


def check_video(item):
    vid = item.get("video_id")
    if not vid:
        return {"item": item, "status": "no_video_id", "title": item.get("title")}

    if st_login and st_key:
        api_url = f"https://api.streamtape.com/file/info?file={vid}&login={st_login}&key={st_key}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            resp = urllib.request.urlopen(req, timeout=12)
            data = json.loads(resp.read().decode())
            res = data.get("result", {})
            file_info = res.get(vid) or (res if isinstance(res, dict) else {})
            status_code = file_info.get("status") if isinstance(file_info, dict) else None
            if status_code == 200:
                return {"item": item, "video_id": vid, "status": "active", "title": item.get("title")}
            elif status_code == 404:
                return {"item": item, "video_id": vid, "status": "deleted_or_missing", "title": item.get("title")}
            else:
                return {"item": item, "video_id": vid, "status": f"st_status_{status_code}", "title": item.get("title")}
        except urllib.error.HTTPError as e:
            return {"item": item, "video_id": vid, "status": f"http_{e.code}", "title": item.get("title")}
        except Exception as e:
            return {"item": item, "video_id": vid, "status": f"err_{str(e)[:20]}", "title": item.get("title")}
    else:
        # Fallback to checking embed page HTTP status
        embed_url = item.get("embed_url") or f"https://streamtape.com/e/{vid}"
        req = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            resp = urllib.request.urlopen(req, timeout=12)
            if resp.status == 200:
                return {"item": item, "video_id": vid, "status": "active", "title": item.get("title")}
            return {"item": item, "video_id": vid, "status": f"http_{resp.status}", "title": item.get("title")}
        except urllib.error.HTTPError as e:
            return {"item": item, "video_id": vid, "status": f"http_{e.code}", "title": item.get("title")}
        except Exception as e:
            return {"item": item, "video_id": vid, "status": f"err_{str(e)[:20]}", "title": item.get("title")}


results = []
with ThreadPoolExecutor(max_workers=20) as ex:
    futures = [ex.submit(check_video, item) for item in catalog]
    for fut in as_completed(futures):
        results.append(fut.result())

by_status = {}
for r in results:
    s = r["status"]
    by_status.setdefault(s, []).append(r)

print("\n=== STREAMTAPE STATUS SUMMARY ===")
for s, lst in sorted(by_status.items()):
    print(f"  * Status '{s}': {len(lst)} videos")

broken_statuses = [s for s in by_status if s not in ("active",)]
total_broken = sum(len(by_status[s]) for s in broken_statuses)

if total_broken > 0:
    print(f"\n❌ FAILED / INACTIVE VIDEOS ({total_broken}):")
    failed_items = []
    for s in broken_statuses:
        for r in by_status[s]:
            item = r["item"]
            failed_items.append(item)
            print(f"  - [{r['video_id']}] ({s}) {r['title']} (Source: {item.get('source_input')})")

    out_failed = REPO_ROOT / "data" / "failed_streamtape_videos.json"
    with open(out_failed, "w", encoding="utf-8") as f:
        json.dump(failed_items, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(failed_items)} failed videos to {out_failed}")
