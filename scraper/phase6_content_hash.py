"""
Phase 6 — record-level content-hash change detection.

Reads `{prefix}phase3_extracted.json`, computes a SHA-256 over each
record's canonical business payload (name, address, phone,
opening_hours), and writes:

  - {prefix}phase6_content_hash.json     latest snapshot, keyed by website_url
  - {prefix}phase6_diff_history.jsonl    append-only run log (one JSON line per run)

Records with extraction_status == "skipped" or no website_url are omitted.

Standalone:
    python main.py --phase 6           # full output
    python main.py --phase 6 --test    # test_*  prefix
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .content_hash import canonical_payload, compute_hash
from .utils import OUTPUT_DIR, load_checkpoint, load_records, save_checkpoint

logger = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1


def snapshot_path(prefix: str = "") -> Path:
    return OUTPUT_DIR / f"{prefix}phase6_content_hash.json"


def history_path(prefix: str = "") -> Path:
    return OUTPUT_DIR / f"{prefix}phase6_diff_history.jsonl"


def load_snapshot(prefix: str = "") -> dict:
    raw = load_checkpoint(snapshot_path(prefix))
    if not raw or raw.get("version") != SNAPSHOT_VERSION:
        return {"version": SNAPSHOT_VERSION, "entries": {}}
    return raw


def save_snapshot(snapshot: dict, prefix: str = "") -> None:
    save_checkpoint(snapshot_path(prefix), snapshot)


def build_entries(records: list[dict]) -> dict:
    """Build {website_url: {hash, payload, last_seen}} from phase 3 records."""
    now = datetime.now(timezone.utc).isoformat()
    entries: dict = {}
    for r in records:
        if r.get("extraction_status") == "skipped":
            continue
        url = r.get("website_url") or ""
        if not url:
            continue
        entries[url] = {
            "hash": compute_hash(r),
            "payload": canonical_payload(r),
            "last_seen": now,
        }
    return entries


def diff_states(prev: dict, curr: dict) -> dict:
    """Compare two `{url: {hash: ...}}` mappings. Returns bucketed url lists."""
    prev_urls = set(prev.keys())
    curr_urls = set(curr.keys())

    added = sorted(curr_urls - prev_urls)
    removed = sorted(prev_urls - curr_urls)
    common = curr_urls & prev_urls
    changed = sorted(u for u in common if prev[u].get("hash") != curr[u].get("hash"))
    unchanged = sorted(u for u in common if prev[u].get("hash") == curr[u].get("hash"))
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }


def append_history(prefix: str, run_record: dict) -> None:
    path = history_path(prefix)
    with open(path, "a") as f:
        f.write(json.dumps(run_record, ensure_ascii=False) + "\n")


def run(prefix: str = "", reset: bool = False) -> dict:
    p3_path = OUTPUT_DIR / f"{prefix}phase3_extracted.json"
    records = load_records(p3_path)
    if not records:
        raise FileNotFoundError(
            f"Phase 3 output not found at {p3_path} — run phase 3 first"
        )

    prev_snapshot = {"version": SNAPSHOT_VERSION, "entries": {}} if reset else load_snapshot(prefix)
    prev_entries = prev_snapshot.get("entries", {})

    curr_entries = build_entries(records)
    diff = diff_states(prev_entries, curr_entries)

    snapshot = {"version": SNAPSHOT_VERSION, "entries": curr_entries}
    save_snapshot(snapshot, prefix)

    run_record = {
        "run_ts": datetime.now(timezone.utc).isoformat(),
        "counts": {k: len(v) for k, v in diff.items()},
        "changes": {k: diff[k] for k in ("added", "removed", "changed")},
    }
    append_history(prefix, run_record)

    logger.info(
        "Phase 6 complete — total=%d  added=%d  removed=%d  changed=%d  unchanged=%d",
        len(curr_entries),
        len(diff["added"]),
        len(diff["removed"]),
        len(diff["changed"]),
        len(diff["unchanged"]),
    )
    return {"snapshot": snapshot, "diff": diff, "run_record": run_record}
