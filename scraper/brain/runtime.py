"""Brain runtime: execute learned global_patterns against text/page (Phase 3+).

Public API:
    extract_with_brain(field, text, page, language, target_city) → (value, pattern_id) | None
    cache_html(url, page) → Path | None
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from scraper import brain

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Process-level pattern cache  {(field, language) -> ([patterns], timestamp)}
# ---------------------------------------------------------------------------
_CACHE: dict[tuple[str, str], tuple[list[dict], float]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 300.0  # 5 min


def _get_patterns(field: str, language: str) -> list[dict]:
    """Load active patterns from DB, cached per process for TTL seconds."""
    import db_repo  # late import so tests can mock without full stack

    key = (field, language)
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and (now - entry[1]) < _CACHE_TTL:
            return entry[0]

    try:
        # Pass None when language=="any" to skip the language filter in DB.
        lang_arg: Optional[str] = None if language == "any" else language
        patterns = db_repo.list_active_patterns(field, lang_arg)
    except Exception:
        logger.debug("brain: DB unavailable for field=%s — trying local store", field)
        try:
            from scraper.brain.local_store import list_active_patterns as _local_list
            lang_arg2: Optional[str] = None if language == "any" else language
            patterns = _local_list(field, lang_arg2)
        except Exception:
            patterns = []

    with _CACHE_LOCK:
        _CACHE[key] = (patterns, time.monotonic())
    return patterns


def invalidate_cache(field: Optional[str] = None) -> None:
    """Force next call to reload patterns. Call this in tests after seeding DB."""
    with _CACHE_LOCK:
        if field is None:
            _CACHE.clear()
        else:
            for k in list(_CACHE.keys()):
                if k[0] == field:
                    del _CACHE[k]


# ---------------------------------------------------------------------------
# Per-field post-processors / validators
# ---------------------------------------------------------------------------

def _post_phone(raw: str, **_) -> Optional[str]:
    digits = re.sub(r"[^\d+]", "", raw)
    if len(digits) < 7:
        return None
    digits = re.sub(r"^\+490", "+49", digits)
    digits = re.sub(r"^\+330", "+33", digits)
    return digits


def _post_address(raw: str, target_city: Optional[str] = None, **_) -> Optional[str]:
    cleaned = raw.strip().strip(",").strip()
    if len(cleaned) < 5:
        return None
    if target_city and target_city.lower() not in cleaned.lower():
        return None
    return cleaned


def _post_opening_hours(raw: str, **_) -> Optional[dict]:
    from scraper.parsers.hours import parse_opening_hours
    return parse_opening_hours(raw)


def _post_name(raw: str, **_) -> Optional[str]:
    cleaned = raw.strip()
    return cleaned if len(cleaned) > 1 else None


_POST: dict[str, Any] = {
    "phone": _post_phone,
    "address": _post_address,
    "opening_hours": _post_opening_hours,
    "name": _post_name,
}


# ---------------------------------------------------------------------------
# Reinforcement helpers (Phase 6)
# ---------------------------------------------------------------------------

def _bump_success_async(pattern_id: int) -> None:
    """Increment success counter + confidence for a pattern hit. Best-effort."""
    try:
        import db_repo
        db_repo.bump_pattern_success(pattern_id)
    except Exception as exc:
        logger.debug("brain: bump_success failed for pattern %d: %s", pattern_id, exc)


def _bump_failure_async(pattern_id: int, current_conf: float) -> None:
    """Decrement confidence for a validator-rejected hit; trigger repair if stale."""
    try:
        import db_repo
        new_conf = db_repo.bump_pattern_failure(pattern_id)
        if new_conf is None:
            new_conf = max(0.0, current_conf - brain.CONFIDENCE_FAILURE_DELTA)
        if new_conf < brain.STALE_THRESHOLD:
            try:
                db_repo.set_pattern_status(pattern_id, "stale")
                logger.info("brain: pattern %d decayed to stale (conf=%.3f)", pattern_id, new_conf)
                _trigger_repair(pattern_id)
            except Exception as e:
                logger.debug("brain: stale flip failed for pattern %d: %s", pattern_id, e)
    except Exception as exc:
        logger.debug("brain: bump_failure failed for pattern %d: %s", pattern_id, exc)


def _trigger_repair(pattern_id: int) -> None:
    """Enqueue a Celery repair task for the given stale pattern. Best-effort."""
    try:
        from api.tasks import repair_pattern_task  # late import: avoids circular dep
        repair_pattern_task.delay(pattern_id)
        logger.info("brain: queued repair_pattern_task for pattern %d", pattern_id)
    except Exception as exc:
        logger.debug("brain: trigger_repair failed for pattern %d: %s", pattern_id, exc)


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def extract_with_brain(
    field: str,
    text: Optional[str] = None,
    page=None,
    language: str = "any",
    target_city: Optional[str] = None,
) -> Optional[tuple[Any, int]]:
    """Try learned global_patterns for `field`.

    Returns (structured_value, pattern_id) on the first valid hit, else None.
    - Regex patterns need `text`.
    - CSS patterns need `page`.
    Per-field post-processor is applied before accepting the raw match.
    On hit: success counter bumped. On validator fail: failure counter bumped;
    pattern flipped to stale + repair queued if confidence drops below STALE_THRESHOLD.
    """
    if not brain.is_enabled():
        return None

    patterns = _get_patterns(field, language)
    if not patterns:
        return None

    post = _POST.get(field)

    for pat in patterns:
        pattern_id: int = pat["id"]
        pattern_type: str = pat["pattern_type"]
        pattern_str: str = pat["pattern"]
        current_conf: float = float(pat.get("confidence_score", 0.5))

        raw: Optional[str] = None

        try:
            if pattern_type == "regex" and text:
                m = re.search(pattern_str, text, re.UNICODE | re.IGNORECASE)
                if m:
                    raw = m.group(0).strip()
            elif pattern_type == "css" and page is not None:
                raw = page.css(pattern_str).get()
                if raw:
                    raw = raw.strip()
        except Exception as exc:
            logger.debug("brain: pattern %d runtime error: %s", pattern_id, exc)
            continue

        if not raw:
            continue

        try:
            value = (
                _post_address(raw, target_city=target_city)
                if field == "address"
                else post(raw) if post
                else (raw.strip() or None)
            )
        except Exception as exc:
            logger.debug("brain: validator error pattern %d: %s", pattern_id, exc)
            _bump_failure_async(pattern_id, current_conf)
            continue

        if value is not None:
            logger.debug("brain: hit pattern %d — %s=%r", pattern_id, field, value)
            _bump_success_async(pattern_id)
            return (value, pattern_id)

        # Raw matched but post-processor rejected it — penalise the pattern.
        _bump_failure_async(pattern_id, current_conf)

    return None


# ---------------------------------------------------------------------------
# HTML page cache  (written by phase3, read by phase4 sandbox seed)
# ---------------------------------------------------------------------------

HTML_CACHE_DIR = Path(__file__).parent.parent.parent / "output" / "html_cache"


def cache_html(url: str, page) -> Optional[Path]:
    """Persist raw page HTML to output/html_cache/<sha1(url)>.html.

    Returns the written path, or None on any failure.
    """
    try:
        html = getattr(page, "html", None) or getattr(page, "content", None)
        if not html:
            return None
        HTML_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sha = hashlib.sha1(url.encode()).hexdigest()
        path = HTML_CACHE_DIR / f"{sha}.html"
        if isinstance(html, bytes):
            path.write_bytes(html)
        else:
            path.write_text(str(html), encoding="utf-8")
        return path
    except Exception as exc:
        logger.debug("brain: html cache write failed for %s: %s", url, exc)
        return None
