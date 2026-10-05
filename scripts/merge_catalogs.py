#!/usr/bin/env python3
"""
Merge Catalogs: Combines catalog outputs from 10 parallel GitHub Actions matrix workers.
Eliminates duplicate entries and updates data/videos.json and data/videos.md.
"""

import json
import sys
from pathlib import Path

# Add scripts directory to sys.path
scripts_dir = Path(__file__).resolve().parent
repo_root = scripts_dir.parent
sys.path.insert(0, str(scripts_dir))

from pipeline import save_catalog


def merge_worker_artifacts(artifacts_dir: Path, repo_root: Path) -> None:
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Locate all worker output JSON files
    patterns = ["**/worker_output_*.json", "worker_output_*.json"]
    worker_files = []
    for pat in patterns:
        worker_files.extend(artifacts_dir.glob(pat))

    worker_files = sorted(set(worker_files))
    print(f"[Merge] Found {len(worker_files)} worker catalog file(s) in {artifacts_dir}.")

    collected_records = []
    for wf in worker_files:
        try:
            with open(wf, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    collected_records.extend(data)
                elif isinstance(data, dict):
                    collected_records.append(data)
                print(f"[Merge] Loaded {len(data) if isinstance(data, list) else 1} record(s) from {wf.name}")
        except Exception as e:
            print(f"[Merge Warning] Could not read {wf}: {e}")

    print(f"[Merge] Total {len(collected_records)} new release(s) collected from all workers.")

    # Save to catalog in reverse order so newest is at the top, save_catalog handles deduplication
    for rec in reversed(collected_records):
        save_catalog(rec, repo_root)

    print("[Merge] Completed catalog aggregation successfully.")


if __name__ == "__main__":
    target_artifacts = Path(sys.argv[1]) if len(sys.argv) > 1 else repo_root / "worker_artifacts"
    merge_worker_artifacts(target_artifacts, repo_root)
