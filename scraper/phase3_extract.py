"""
Phase 3: Layered field extraction with a 5-page-per-site cap.

Refactored to process sites in parallel via a thread pool. Per-site logic
is unchanged: visit pages in priority order with early termination once
all required fields are filled.

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
"""

import asyncio
import json
import logging
import threading
from typing import Any, Optional
from urllib.parse import urlparse

from . import phase4_diff, recipe_builder
from .parsers import (
    parse_address,
    parse_jsonld,
    parse_name_from_page,
    parse_opening_hours,
    parse_phone,
)
from .utils import (
    OUTPUT_DIR,
    contact_block,
    get_page_text,
    load_records,
    normalized_text_hash,
    smart_fetch,
)

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("name", "address", "phone", "opening_hours")
MAX_PAGES_PER_SITE = 5
SITE_CONCURRENCY = 6        # parallel sites (kept modest — smart_fetch may spin up headless)


def _all_present(record: dict) -> bool:
    return all(record.get(f) for f in REQUIRED_FIELDS)


def _merge(record: dict, updates: dict) -> bool:
    changed = False
    for k, v in updates.items():
        if v and not record.get(k):
            record[k] = v
            changed = True
    return changed


def _extract_layers(page, target_city: Optional[str]) -> tuple[dict, dict]:
    out: dict = {}
    src: dict = {}

    jl = parse_jsonld(page) or {}
    for k, v in jl.items():
        if v:
            out[k] = v
            src[k] = "jsonld"

    text = get_page_text(page) or ""
    if not text:
        return out, src

    if "name" not in out:
        n = parse_name_from_page(page)
        if n:
            out["name"] = n
            src["name"] = "regex"
    if "phone" not in out:
        p = parse_phone(text)
        if p:
            out["phone"] = p
            src["phone"] = "regex"
    if "address" not in out:
        a = parse_address(text, target_city=target_city)
        if a:
            out["address"] = a
            src["address"] = "regex"
    if "opening_hours" not in out:
        h = parse_opening_hours(text)
        if h:
            out["opening_hours"] = h
            src["opening_hours"] = "regex"

    if not all(out.get(f) for f in ("address", "phone", "opening_hours")):
        block = contact_block(text)
        if block:
            if "phone" not in out:
                p = parse_phone(block)
                if p:
                    out["phone"] = p
                    src["phone"] = "regex"
            if "address" not in out:
                a = parse_address(block, target_city=target_city)
                if a:
                    out["address"] = a
                    src["address"] = "regex"
            if "opening_hours" not in out:
                h = parse_opening_hours(block)
                if h:
                    out["opening_hours"] = h
                    src["opening_hours"] = "regex"

    return out, src


def extract_site(
    entry: dict,
    cache: dict,
    cache_lock: Optional[threading.Lock] = None,
    recipe_store: Optional[recipe_builder.RecipeStore] = None,
) -> dict:
    """Extract one site. Pure-ish: mutates `cache` under `cache_lock` when provided."""
    website_url = entry.get("website_url") or ""
    target_city = entry.get("target_city")
    pages = entry.get("pages", {})

    extracted = {
        "name": entry.get("name"),
        "gelbeseiten_url": entry.get("gelbeseiten_url"),
        "website_url": website_url,
        "target_city": target_city,
        "gs_uuid": entry.get("gs_uuid"),
        "address": None,
        "phone": None,
        "opening_hours": None,
    }
    sources: list[str] = []
    field_source: dict[str, str] = {}
    fetched_pages: dict[str, Any] = {}
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
        fetched_pages[url] = page

        if cache_lock:
            with cache_lock:
                changed, cached_fields = phase4_diff.check(cache, url, page)
        else:
            changed, cached_fields = phase4_diff.check(cache, url, page)

        if not changed and cached_fields:
            logger.info("  [%s] %s — unchanged, reusing cache", label, url)
            for k, v in cached_fields.items():
                if v and not extracted.get(k):
                    extracted[k] = v
                    field_source[k] = "cache"
            if field_source:
                sources.append(f"{label}:cache")
            continue
        change_detected = change_detected or changed

        found, found_src = _extract_layers(page, target_city)
        before = {k: extracted.get(k) for k in REQUIRED_FIELDS}
        if _merge(extracted, found):
            for k in REQUIRED_FIELDS:
                if extracted.get(k) and not before.get(k):
                    field_source[k] = found_src.get(k, "regex")
            jl_used = any(found_src.get(k) == "jsonld" for k in REQUIRED_FIELDS)
            sources.append(f"{label}:{'jsonld' if jl_used else 'regex'}")

        record_fields = {k: extracted.get(k) for k in REQUIRED_FIELDS}
        if cache_lock:
            with cache_lock:
                phase4_diff.record(cache, url, page, record_fields)
        else:
            phase4_diff.record(cache, url, page, record_fields)

    missing_fields = [f for f in REQUIRED_FIELDS if not extracted.get(f)]
    if missing_fields and recipe_store is not None and fetched_pages and website_url:
        domain = urlparse(website_url).netloc
        homepage = fetched_pages.get(website_url) or next(iter(fetched_pages.values()))
        content_hash = normalized_text_hash(homepage) if homepage else None
        try:
            filled, recipe_sources = recipe_builder.fill_missing(
                domain=domain,
                fetched_pages=fetched_pages,
                missing_fields=missing_fields,
                store=recipe_store,
                content_hash=content_hash,
            )
        except Exception as e:
            logger.warning("Recipe fallback unavailable for %s: %s", domain, e)
            filled, recipe_sources = {}, {}
        for k, v in filled.items():
            extracted[k] = v
            field_source[k] = recipe_sources.get(k, "recipe")
        if filled:
            sources.append(f"recipe:{'+'.join(sorted(filled.keys()))}")

    status = (
        "complete" if _all_present(extracted)
        else "partial" if any(extracted.get(f) for f in ("address", "phone", "opening_hours"))
        else "failed"
    )

    summary = " ".join(f"{f}={field_source.get(f, 'miss')}" for f in REQUIRED_FIELDS)
    filled_count = sum(1 for f in REQUIRED_FIELDS if extracted.get(f))
    logger.info(
        "[%s] %s  (%d/%d, status=%s)",
        entry.get("name", "?"), summary, filled_count, len(REQUIRED_FIELDS), status,
    )

    extracted["data_sources"] = sources
    extracted["field_sources"] = field_source
    extracted["extraction_status"] = status
    extracted["change_detected"] = change_detected
    return extracted


def _skipped_record(entry: dict, reason: str) -> dict:
    return {
        "name": entry.get("name"),
        "gelbeseiten_url": entry.get("gelbeseiten_url"),
        "website_url": entry.get("website_url"),
        "target_city": entry.get("target_city"),
        "gs_uuid": entry.get("gs_uuid"),
        "address": None, "phone": None, "opening_hours": None,
        "data_sources": [], "extraction_status": "skipped",
        "skip_reason": reason,
    }


async def _run_async(
    entries: list[dict],
    cache: dict,
    recipe_store: recipe_builder.RecipeStore,
    prefix: str,
) -> list[dict]:
    cache_lock = threading.Lock()
    save_lock = threading.Lock()
    sem = asyncio.Semaphore(SITE_CONCURRENCY)
    loop = asyncio.get_running_loop()

    async def _one(entry: dict) -> dict:
        skip_reason = entry.get("skip_reason")
        if skip_reason:
            return _skipped_record(entry, skip_reason)

        async with sem:
            logger.info("Extracting: %s  (%s)", entry.get("name"), entry.get("website_url"))
            record = await loop.run_in_executor(
                None, extract_site, entry, cache, cache_lock, recipe_store
            )
            with save_lock:
                phase4_diff.save_cache(cache, prefix)
            return record

    return list(await asyncio.gather(*[_one(e) for e in entries]))


def run(reset: bool = False, prefix: str = "") -> list[dict]:
    input_file  = OUTPUT_DIR / f"{prefix}phase2_site_map.json"
    output_file = OUTPUT_DIR / f"{prefix}phase3_extracted.json"

    if reset:
        output_file.unlink(missing_ok=True)

    site_maps = load_records(input_file)
    if not site_maps:
        raise FileNotFoundError(
            f"Phase 2 output not found at {input_file} — run phase 2 first"
        )

    existing = load_records(output_file)
    # Re-process anything that didn't reach "complete" so LLM recipe / new sources
    # can rescue partial/failed records on rerun.
    existing_complete = {
        r.get("website_url") or f"__nw__{r.get('name')}": r
        for r in existing if r.get("extraction_status") == "complete"
    }
    cache = phase4_diff.load_cache(prefix)
    recipe_store = recipe_builder.RecipeStore()

    todo: list[dict] = []
    keep: list[dict] = []
    for entry in site_maps:
        key = entry.get("website_url") or f"__nw__{entry.get('name')}"
        if key in existing_complete:
            keep.append(existing_complete[key])
        else:
            todo.append(entry)

    logger.info(
        "Phase 3 start — %d sites (%d cached, %d todo), diff-cache=%d urls",
        len(site_maps), len(keep), len(todo), len(cache.get("entries", {})),
    )

    new_records = asyncio.run(_run_async(todo, cache, recipe_store, prefix))
    records = keep + new_records

    with open(output_file, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    complete = sum(1 for r in records if r.get("extraction_status") == "complete")
    partial  = sum(1 for r in records if r.get("extraction_status") == "partial")
    failed   = sum(1 for r in records if r.get("extraction_status") == "failed")
    skipped  = sum(1 for r in records if r.get("extraction_status") == "skipped")
    logger.info(
        "Phase 3 complete — %d records (complete=%d, partial=%d, failed=%d, skipped=%d) → %s",
        len(records), complete, partial, failed, skipped, output_file.name,
    )
    return records
