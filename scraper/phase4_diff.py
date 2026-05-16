"""
Phase 4 — content-hash cache for diff-based change detection.

Used inline by Phase 3 (short-circuit if hash matches & within TTL)
and runnable standalone (rebuilds cache from Phase 3 output for inspection).

Schema:
{
  "<url>": {
    "hash": "<sha256>",
    "last_seen": "<iso-8601>",
    "fields": {"name": "...", "address": "...", "phone": "...", "opening_hours": {...}}
  }, ...
}

TTL: 30 days → entries older are treated as stale and force re-extract.
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .utils import OUTPUT_DIR, load_checkpoint, save_checkpoint, normalized_text_hash

logger = logging.getLogger(__name__)

TTL_DAYS = 30
CACHE_VERSION = 1


def cache_path(prefix: str = "") -> Path:
    return OUTPUT_DIR / f"{prefix}phase4_diff.json"


def load_cache(prefix: str = "") -> dict:
    raw = load_checkpoint(cache_path(prefix))
    if not raw:
        return {"version": CACHE_VERSION, "entries": {}}
    if raw.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "entries": {}}
    return raw


def save_cache(cache: dict, prefix: str = "") -> None:
    save_checkpoint(cache_path(prefix), cache)


def _is_fresh(entry: dict) -> bool:
    try:
        seen = datetime.fromisoformat(entry["last_seen"])
    except (KeyError, ValueError):
        return False
    return datetime.now(timezone.utc) - seen < timedelta(days=TTL_DAYS)


def check(cache: dict, url: str, page) -> tuple[bool, Optional[dict]]:
    """
    Return (changed, cached_fields).
      - changed=False, fields=<dict>  → caller may skip extraction, reuse fields
      - changed=True, fields=None      → caller must extract
    """
    entry = cache.get("entries", {}).get(url)
    if not entry or not _is_fresh(entry):
        return True, None
    new_hash = normalized_text_hash(page)
    if new_hash == entry.get("hash"):
        return False, entry.get("fields")
    return True, None


def record(cache: dict, url: str, page, fields: dict) -> None:
    """Persist hash + extracted fields for this url."""
    cache.setdefault("entries", {})
    cache["entries"][url] = {
        "hash": normalized_text_hash(page),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "fields": fields,
    }


def run(reset: bool = False, prefix: str = "") -> dict:
    """Standalone — rebuild cache summary from current Phase 3 output."""
    from .utils import load_records

    p3 = OUTPUT_DIR / f"{prefix}phase3_extracted.json"
    cache = {"version": CACHE_VERSION, "entries": {}} if reset else load_cache(prefix)

    records = load_records(p3)
    now = datetime.now(timezone.utc).isoformat()
    for r in records:
        url = r.get("website_url") or ""
        if not url:
            continue
        cache.setdefault("entries", {}).setdefault(url, {})
        cache["entries"][url]["last_seen"] = cache["entries"][url].get("last_seen") or now
        cache["entries"][url]["fields"] = {
            k: r.get(k) for k in ("name", "address", "phone", "opening_hours")
        }
    save_cache(cache, prefix)
    logger.info("Phase 4 complete — cache has %d entries", len(cache.get("entries", {})))
    return cache
