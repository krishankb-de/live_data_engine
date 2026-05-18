"""Phase 4 — Sandbox Harvest & Validator.

Public API:
    seed_from_output(fetch_missing, prefix, limit) -> int   # fixtures inserted
    validate_candidate(candidate_pattern, field, pattern_type, language) -> dict
    passes_thresholds(metrics, field) -> bool

CLI:
    python -m scraper.brain.sandbox seed [--no-fetch] [--prefix PREFIX] [--limit N]
    python -m scraper.brain.sandbox validate <candidate_id>
    python -m scraper.brain.sandbox validate-pattern <field> <pattern> [--type regex|css] [--language LANG]
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from scraper import brain
from scraper.brain.runtime import HTML_CACHE_DIR, _POST

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Golden-3 text snippets (canonical offline ground truth)
# ---------------------------------------------------------------------------

_GOLDEN: list[dict] = [
    {
        "name": "dante",
        "url": "https://www.danteconnection.de",
        "language": "en",
        "target_city": "Berlin",
        "text": (
            "Dante Connection bookstore in Berlin-Kreuzberg\n"
            "Homepage Webshop Events\n"
            "Visit us in person, call us, or send us an email!\n\n"
            "Dante Connection – Oranienstraße 165a – 10999 Berlin-Kreuzberg\n\n"
            "030 – 615 76 58\n\n"
            "info@danteconnection.de\n\n"
            "OUR OPENING HOURS\n\n"
            "Open Monday to Saturday,\n"
            "10am-7pm\n"
        ),
    },
    {
        "name": "zadig",
        "url": "http://www.zadigbuchhandlung.de",
        "language": "fr",
        "target_city": "Berlin",
        "text": (
            "La librairie\n"
            "Librairie française\n"
            "Patrick Suel\n\n"
            "Gipsstraße 12, 10119 Berlin\n"
            "tel +49 (0)30. 280 999 05\n"
            "fax +49 (0)30. 280 999 06\n"
            "Email info@zadigbuchhandlung.de\n\n"
            "Le lundi de 14 à 19 heures,\n"
            "du mardi au vendredi de 11 à 19 heures\n"
            "et le samedi de 11 à 18 heures\n"
        ),
    },
    {
        "name": "ludwig",
        "url": "https://ludwigwilde.buchhandlung.de/shop",
        "language": "de",
        "target_city": "Berlin",
        "text": (
            "Buchhandlung Ludwig Wilde\n"
            "Inhaberin: ...\n"
            "Körtesträße 24, 10967 Berlin\n"
            "Telefon: 030 / 691 94 08\n\n"
            "Öffnungszeiten:\n"
            "Mo-Fr 08:00-18:00\n"
            "Sa 10:00-16:00\n"
        ),
    },
]


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _html_path_for_url(url: str) -> Path:
    sha = hashlib.sha1(url.encode()).hexdigest()
    return HTML_CACHE_DIR / f"{sha}.html"


def _text_to_html(text: str, title: str = "") -> str:
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><pre>{escaped}</pre></body></html>"
    )


def _site_to_html(fields: dict) -> str:
    """Synthetic HTML for a site whose HTML was never cached."""
    name = fields.get("name", "")
    address = fields.get("address", "")
    phone = fields.get("phone", "")
    hours = fields.get("opening_hours")
    if isinstance(hours, dict):
        hours_str = "  ".join(f"{k}: {v}" for k, v in hours.items())
    elif isinstance(hours, str):
        hours_str = hours
    else:
        hours_str = ""
    text = "\n".join(filter(None, [name, address, phone, hours_str]))
    return _text_to_html(text, title=name)


def _read_selector(html_path: Path):
    """Parse an HTML file with scrapling.Selector. Returns Selector or None."""
    try:
        from scrapling import Selector  # type: ignore

        html = html_path.read_text(encoding="utf-8", errors="ignore")
        return Selector(html)
    except Exception as exc:
        logger.debug("sandbox: cannot parse %s: %s", html_path, exc)
        return None


def _get_text(selector) -> str:
    try:
        return selector.get_all_text(ignore_tags=("script", "style", "noscript", "head"))
    except Exception:
        try:
            return " ".join(selector.css("body::text").getall())
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Pattern application
# ---------------------------------------------------------------------------

def _apply_pattern(
    pattern_str: str,
    pattern_type: str,
    field: str,
    selector,
    text: str,
) -> Optional[Any]:
    """Run pattern + post-processor. Returns structured value or None."""
    raw: Optional[str] = None
    try:
        if pattern_type == "regex":
            m = re.search(pattern_str, text, re.UNICODE | re.IGNORECASE)
            if m:
                raw = m.group(0).strip()
        elif pattern_type == "css" and selector is not None:
            raw = selector.css(pattern_str).get()
            if raw:
                raw = raw.strip()
    except Exception as exc:
        logger.debug("sandbox: pattern runtime error: %s", exc)
        return None

    if not raw:
        return None

    post = _POST.get(field)
    try:
        if field == "address":
            from scraper.brain.runtime import _post_address

            return _post_address(raw)  # no target_city filter in sandbox
        return post(raw) if post else (raw.strip() or None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Value serialization / comparison
# ---------------------------------------------------------------------------

def _serialize(field: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _values_match(extracted: str, expected: str) -> bool:
    e = extracted.lower().strip()
    x = expected.lower().strip()
    return e == x or x in e or e in x


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def seed_from_output(
    fetch_missing: bool = True,
    prefix: str = "",
    limit: int = 0,
) -> int:
    """Seed sandbox_fixtures table.

    Fixture priority per URL:
      1. html_cache/<sha1>.html already on disk
      2. One-shot smart_fetch (when fetch_missing=True)
      3. Synthetic HTML generated from phase4_diff fields

    Returns count of new fixtures inserted.
    """
    import db_repo
    from scraper.parsers import parse_phone, parse_address, parse_opening_hours
    from scraper.utils import OUTPUT_DIR

    HTML_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    inserted = 0

    # Load existing to skip duplicates (html_path + field)
    existing: set[tuple[str, str]] = set()
    try:
        for row in db_repo.list_fixtures():
            existing.add((row["html_path"], row["field"]))
    except Exception as exc:
        logger.warning("sandbox: cannot load existing fixtures: %s", exc)

    # --- 1. Golden-3 ---
    for g in _GOLDEN:
        html_path = HTML_CACHE_DIR / f"_golden_{g['name']}.html"
        if not html_path.exists():
            html_path.write_text(_text_to_html(g["text"], g["name"]), encoding="utf-8")
            logger.info("sandbox: wrote golden HTML %s", html_path.name)

        text = g["text"]
        target_city = g.get("target_city")
        ground_truth: dict[str, Any] = {
            "phone": parse_phone(text),
            "address": parse_address(text, target_city=target_city),
            "opening_hours": parse_opening_hours(text),
            "name": None,  # name needs a page object; skip
        }

        for field in brain.FIELDS:
            key = (str(html_path), field)
            if key in existing:
                continue
            expected_str = _serialize(field, ground_truth.get(field))
            try:
                db_repo.insert_fixture(
                    source_url=g["url"],
                    html_path=str(html_path),
                    field=field,
                    expected_value=expected_str,
                    language=g["language"],
                )
                existing.add(key)
                inserted += 1
            except Exception as exc:
                logger.warning("sandbox: golden insert_fixture failed: %s", exc)

    # --- 2. phase4_diff URLs ---
    diff_path = OUTPUT_DIR / f"{prefix}phase4_diff.json"
    diff_entries: dict[str, dict] = {}
    if diff_path.exists():
        try:
            raw = json.loads(diff_path.read_text(encoding="utf-8"))
            diff_entries = raw.get("entries", {})
        except Exception as exc:
            logger.warning("sandbox: cannot load phase4_diff: %s", exc)

    urls = list(diff_entries.keys())
    if limit:
        urls = urls[:limit]

    for url in urls:
        fields_data: dict = diff_entries[url].get("fields", {})

        html_path = _html_path_for_url(url)

        if not html_path.exists() and fetch_missing:
            logger.info("sandbox: fetching %s", url)
            try:
                from scraper.utils import smart_fetch
                from scraper.brain.runtime import cache_html

                page = smart_fetch(url)
                if page is not None:
                    cache_html(url, page)
                    logger.info("sandbox: cached HTML for %s", url)
            except Exception as exc:
                logger.warning("sandbox: fetch failed for %s: %s", url, exc)

        if not html_path.exists():
            # Fall back to synthetic HTML from extracted fields
            html_path.write_text(_site_to_html(fields_data), encoding="utf-8")
            logger.debug("sandbox: synthetic HTML for %s", url)

        for field in brain.FIELDS:
            key = (str(html_path), field)
            if key in existing:
                continue
            expected_str = _serialize(field, fields_data.get(field))
            try:
                db_repo.insert_fixture(
                    source_url=url,
                    html_path=str(html_path),
                    field=field,
                    expected_value=expected_str,
                    language="de",
                )
                existing.add(key)
                inserted += 1
            except Exception as exc:
                logger.warning("sandbox: insert_fixture failed for %s/%s: %s", url, field, exc)

    logger.info("sandbox: seed complete — %d new fixtures inserted", inserted)
    return inserted


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def validate_candidate(
    candidate_pattern: str,
    field: str,
    pattern_type: str,
    language: str = "any",
) -> dict:
    """Run candidate against all sandbox fixtures for field.

    Returns:
        precision, recall, true_positives, false_positives, false_negatives,
        negative_hits, total_positive, total_negative, sample_failures
    """
    import db_repo

    lang_arg = None if language == "any" else language
    fixtures = db_repo.list_fixtures(field=field, language=lang_arg)

    tp = fp = fn = tn = 0
    sample_failures: list[dict] = []

    for fix in fixtures:
        html_path = Path(fix["html_path"])
        expected_str: Optional[str] = fix.get("expected_value")
        is_positive = expected_str is not None

        if not html_path.exists():
            logger.debug("sandbox: html missing %s — skipping fixture", html_path.name)
            continue

        sel = _read_selector(html_path)
        text = _get_text(sel) if sel else ""
        extracted = _apply_pattern(candidate_pattern, pattern_type, field, sel, text)
        matched = extracted is not None

        if is_positive:
            if matched:
                tp += 1
                if len(sample_failures) < 5:
                    ext_str = _serialize(field, extracted) or ""
                    if expected_str and not _values_match(ext_str, expected_str):
                        sample_failures.append(
                            {
                                "url": fix.get("source_url"),
                                "expected": expected_str,
                                "extracted": ext_str,
                                "type": "value_mismatch",
                            }
                        )
            else:
                fn += 1
                if len(sample_failures) < 5:
                    sample_failures.append(
                        {
                            "url": fix.get("source_url"),
                            "expected": expected_str,
                            "extracted": None,
                            "type": "false_negative",
                        }
                    )
        else:
            if matched:
                fp += 1
                if len(sample_failures) < 5:
                    sample_failures.append(
                        {
                            "url": fix.get("source_url"),
                            "expected": None,
                            "extracted": _serialize(field, extracted),
                            "type": "false_positive",
                        }
                    )
            else:
                tn += 1

    total_positive = tp + fn
    total_negative = fp + tn
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / total_positive if total_positive > 0 else 0.0

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "negative_hits": fp,
        "total_positive": total_positive,
        "total_negative": total_negative,
        "sample_failures": sample_failures,
    }


def passes_thresholds(metrics: dict, field: str) -> bool:
    """Return True if metrics satisfy per-field promotion thresholds."""
    min_precision, min_recall = brain.PROMOTION_THRESHOLDS.get(field, (0.95, 0.60))
    return (
        metrics["precision"] >= min_precision
        and metrics["recall"] >= min_recall
        and metrics["negative_hits"] == 0
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path as _Path

    # Load .env so Supabase credentials are available when run directly
    try:
        from dotenv import load_dotenv as _load_dotenv

        _load_dotenv(_Path(__file__).parent.parent.parent / ".env")
    except ImportError:
        pass

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ap = argparse.ArgumentParser(
        description="Brain sandbox: seed fixtures or validate a candidate pattern"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    # seed
    sp = sub.add_parser("seed", help="Seed sandbox_fixtures from phase3/4 output")
    sp.add_argument(
        "--no-fetch", dest="fetch", action="store_false", default=True,
        help="Skip network fetches for missing HTML cache entries",
    )
    sp.add_argument("--prefix", default="", help="Phase output file prefix (e.g. test_)")
    sp.add_argument("--limit", type=int, default=0, help="Max phase4_diff URLs to process (0=all)")

    # validate by DB candidate id
    vp = sub.add_parser("validate", help="Validate a queued candidate from the DB")
    vp.add_argument("candidate_id", type=int)

    # validate ad-hoc pattern
    vpp = sub.add_parser("validate-pattern", help="Validate an ad-hoc pattern without DB")
    vpp.add_argument("field", choices=list(brain.FIELDS))
    vpp.add_argument("pattern", help="Regex string or CSS selector")
    vpp.add_argument(
        "--type", dest="pattern_type", choices=["regex", "css"], default="regex"
    )
    vpp.add_argument("--language", default="any")

    args = ap.parse_args()

    if args.cmd == "seed":
        count = seed_from_output(
            fetch_missing=args.fetch, prefix=args.prefix, limit=args.limit
        )
        print(f"Inserted {count} new fixtures.")

    elif args.cmd == "validate":
        import db_repo

        cands = db_repo.list_candidates(status=None)
        cand = next((c for c in cands if c["id"] == args.candidate_id), None)
        if cand is None:
            print(f"Candidate {args.candidate_id} not found.", file=sys.stderr)
            sys.exit(1)
        metrics = validate_candidate(
            cand["candidate_pattern"],
            cand["field"],
            cand["pattern_type"],
            cand.get("language", "any"),
        )
        passes = passes_thresholds(metrics, cand["field"])
        print(json.dumps({**metrics, "passes_thresholds": passes}, indent=2))

    elif args.cmd == "validate-pattern":
        metrics = validate_candidate(
            args.pattern, args.field, args.pattern_type, args.language
        )
        passes = passes_thresholds(metrics, args.field)
        print(json.dumps({**metrics, "passes_thresholds": passes}, indent=2))
