"""
Phase 3: Layered field extraction with a 5-page-per-site cap.

Visit order (stops as soon as all 4 fields populated):
  1. Homepage
  2. Classified imprint
  3. Classified contact
  4. Classified hours
  5. Classified branch

On each page, layers run in cost order:
  A. JSON-LD / schema.org (free, near-zero error)
  B. Multilingual regex (DE/EN/FR) — address validates against target_city
  C. Contact-block proximity (only when A+B incomplete on this page)

Phase-4 hash cache short-circuits unchanged pages on rerun.

Outputs: output/phase3_extracted.json  (or test_ prefix)
Checkpoint: output/phase3_checkpoint.json
"""

import logging
from typing import Optional

from . import phase4_diff
from .parsers import (
    parse_address,
    parse_jsonld,
    parse_name_from_page,
    parse_opening_hours,
    parse_phone,
)
from .utils import (
    OUTPUT_DIR,
    append_record,
    contact_block,
    get_page_text,
    load_checkpoint,
    load_records,
    save_checkpoint,
    smart_fetch,
)

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("name", "address", "phone", "opening_hours")
MAX_PAGES_PER_SITE = 5


def _all_present(record: dict) -> bool:
    return all(record.get(f) for f in REQUIRED_FIELDS)


def _merge(record: dict, updates: dict) -> bool:
    """Fill only-missing fields. Returns True if anything changed."""
    changed = False
    for k, v in updates.items():
        if v and not record.get(k):
            record[k] = v
            changed = True
    return changed


def _extract_layers(page, target_city: Optional[str]) -> dict:
    """Run A → B → C on a single page; return found fields."""
    out: dict = {}

    # Layer A: JSON-LD
    jl = parse_jsonld(page) or {}
    out.update({k: v for k, v in jl.items() if v})

    text = get_page_text(page) or ""
    if not text:
        return out

    # Layer B: regex on full page
    if "name" not in out:
        n = parse_name_from_page(page)
        if n:
            out["name"] = n
    if "phone" not in out:
        p = parse_phone(text)
        if p:
            out["phone"] = p
    if "address" not in out:
        a = parse_address(text, target_city=target_city)
        if a:
            out["address"] = a
    if "opening_hours" not in out:
        h = parse_opening_hours(text)
        if h:
            out["opening_hours"] = h

    # Layer C: contact-block proximity (only if anything still missing)
    if not all(out.get(f) for f in ("address", "phone", "opening_hours")):
        block = contact_block(text)
        if block:
            if "phone" not in out:
                p = parse_phone(block)
                if p:
                    out["phone"] = p
            if "address" not in out:
                a = parse_address(block, target_city=target_city)
                if a:
                    out["address"] = a
            if "opening_hours" not in out:
                h = parse_opening_hours(block)
                if h:
                    out["opening_hours"] = h

    return out


def extract_site(entry: dict, cache: dict) -> dict:
    """
    Extract one site. Pure function — caller writes record + persists cache.
    Returns the completed extraction record.
    """
    website_url = entry.get("website_url") or ""
    target_city = entry.get("target_city")
    pages = entry.get("pages", {})

    extracted = {
        "name": entry.get("name"),
        "gelbeseiten_url": entry.get("gelbeseiten_url"),
        "website_url": website_url,
        "target_city": target_city,
        "address": None,
        "phone": None,
        "opening_hours": None,
    }
    sources: list[str] = []
    change_detected = False

    visit_order = [
        ("homepage", website_url),
        ("imprint",  pages.get("imprint")),
        ("contact",  pages.get("contact")),
        ("hours",    pages.get("hours")),
        ("branch",   pages.get("branch")),
    ]
    visited: set[str] = set()
    fetched = 0

    for label, url in visit_order:
        if _all_present(extracted) or fetched >= MAX_PAGES_PER_SITE:
            break
        if not url or url in visited:
            continue
        visited.add(url)

        page = smart_fetch(url)
        fetched += 1
        if page is None:
            logger.warning("  Failed fetch %s (%s)", label, url)
            continue

        changed, cached_fields = phase4_diff.check(cache, url, page)
        if not changed and cached_fields:
            logger.info("  [%s] %s — unchanged, reusing cache", label, url)
            if _merge(extracted, cached_fields):
                sources.append(f"{label}:cache")
            continue
        change_detected = change_detected or changed

        found = _extract_layers(page, target_city)
        if _merge(extracted, found):
            tag = "jsonld" if "name" in found and parse_jsonld(page) else "regex"
            sources.append(f"{label}:{tag}")

        # Persist what this URL contributed
        phase4_diff.record(cache, url, page, {
            k: extracted.get(k) for k in REQUIRED_FIELDS
        })

    status = (
        "complete" if _all_present(extracted)
        else "partial" if any(extracted.get(f) for f in ("address", "phone", "opening_hours"))
        else "failed"
    )
    extracted["data_sources"] = sources
    extracted["extraction_status"] = status
    extracted["change_detected"] = change_detected
    return extracted


def run(reset: bool = False, prefix: str = "") -> list[dict]:
    input_file      = OUTPUT_DIR / f"{prefix}phase2_site_map.json"
    output_file     = OUTPUT_DIR / f"{prefix}phase3_extracted.json"
    checkpoint_file = OUTPUT_DIR / f"{prefix}phase3_checkpoint.json"

    if reset:
        output_file.unlink(missing_ok=True)
        checkpoint_file.unlink(missing_ok=True)

    site_maps = load_records(input_file)
    if not site_maps:
        raise FileNotFoundError(
            f"Phase 2 output not found at {input_file} — run phase 2 first"
        )

    checkpoint = load_checkpoint(checkpoint_file)
    processed_urls = set(checkpoint.get("processed", []))
    cache = phase4_diff.load_cache(prefix)

    logger.info("Phase 3 start — %d sites, %d already processed, cache=%d urls",
                len(site_maps), len(processed_urls),
                len(cache.get("entries", {})))

    for entry in site_maps:
        website_url = entry.get("website_url") or entry.get("name", "NO_URL")
        skip_reason = entry.get("skip_reason")

        if website_url in processed_urls:
            continue

        if skip_reason:
            append_record(output_file, {
                "name": entry.get("name"),
                "gelbeseiten_url": entry.get("gelbeseiten_url"),
                "website_url": website_url,
                "target_city": entry.get("target_city"),
                "address": None, "phone": None, "opening_hours": None,
                "data_sources": [], "extraction_status": "skipped",
                "skip_reason": skip_reason,
            })
            processed_urls.add(website_url)
            save_checkpoint(checkpoint_file, {"processed": list(processed_urls)})
            continue

        logger.info("Extracting: %s  (%s)", entry.get("name"), website_url)
        record = extract_site(entry, cache)
        logger.info("  → status=%s  sources=%s",
                    record["extraction_status"], record["data_sources"])

        append_record(output_file, record)
        processed_urls.add(website_url)
        save_checkpoint(checkpoint_file, {"processed": list(processed_urls)})
        phase4_diff.save_cache(cache, prefix)

    final = load_records(output_file)
    logger.info("Phase 3 complete — %d records saved to %s",
                len(final), output_file.name)
    return final
