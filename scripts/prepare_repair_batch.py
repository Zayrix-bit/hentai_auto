import json
import sys
from pathlib import Path

# Force UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
vpath = REPO_ROOT / "data" / "videos.json"
fpath = REPO_ROOT / "data" / "failed_dropembed_videos.json"

if not fpath.exists():
    print(f"Error: {fpath} does not exist!")
    sys.exit(1)

with open(fpath, "r", encoding="utf-8") as f:
    failed_items = json.load(f)

failed_vids = {x["video_id"] for x in failed_items if "video_id" in x}
failed_sids = {x.get("source_id") for x in failed_items if x.get("source_id")}

print(f"[Repair Prep] Found {len(failed_items)} failed DropEmbed video records.")

with open(vpath, "r", encoding="utf-8") as f:
    catalog = json.load(f)

print(f"[Repair Prep] Current catalog size: {len(catalog)} videos.")

# Filter out the 66 failed records from catalog
cleaned_catalog = [x for x in catalog if x.get("video_id") not in failed_vids]

print(f"[Repair Prep] Cleaned catalog size (after removing broken entries): {len(cleaned_catalog)} videos.")

with open(vpath, "w", encoding="utf-8") as f:
    json.dump(cleaned_catalog, f, indent=2, ensure_ascii=False)

# Format repair targets for pipeline
repair_targets = []
for item in failed_items:
    repair_targets.append({
        "series": item.get("title", ""),
        "title": item.get("title", ""),
        "episode": item.get("episode", "01"),
        "part": item.get("part", ""),
        "source": item.get("source_input", ""),
        "torrent": item.get("source_input", ""),
        "magnet": "",
        "mp4_url": item.get("source_input", ""),
        "filename": item.get("file_name", ""),
        "thumbnail": item.get("thumbnail_url", item.get("poster_url", "")),
        "source_id": item.get("source_id", ""),
        "source_page": "",
    })

tpath = REPO_ROOT / "data" / "hentai_haven_targets.json"
with open(tpath, "w", encoding="utf-8") as f:
    json.dump(repair_targets, f, indent=2, ensure_ascii=False)

print(f"[Repair Prep] Successfully saved {len(repair_targets)} repair targets to {tpath}!")
