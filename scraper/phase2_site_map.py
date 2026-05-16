"""
Phase 2: Discover imprint / contact / hours / branch pages per site.

Order (early termination):
  1. /robots.txt → Sitemap directive, /sitemap.xml, /sitemap_index.xml
     (chase nested index, depth 1). Classify URLs by multilingual keywords.
  2. If <2 critical categories (imprint+contact) → fetch homepage,
     classify same-domain nav/footer links.
  3. For each category still missing → probe known paths (≤3 per category).

Outputs: output/phase2_site_map.json  (or test_ prefix in test mode)
Checkpoint: output/phase2_checkpoint.json
"""

import logging

from .utils import (
    OUTPUT_DIR,
    append_record,
    classify_links,
    classify_sitemap_urls,
    extract_same_domain_links,
    fetch_sitemap_urls,
    load_checkpoint,
    load_records,
    probe_known_paths,
    save_checkpoint,
    smart_fetch,
)

logger = logging.getLogger(__name__)

CRITICAL_CATEGORIES = ("imprint", "contact")


def _has_critical(pages: dict) -> bool:
    return all(c in pages for c in CRITICAL_CATEGORIES)


def run(reset: bool = False, prefix: str = "") -> list[dict]:
    input_file      = OUTPUT_DIR / f"{prefix}phase1_listings.json"
    output_file     = OUTPUT_DIR / f"{prefix}phase2_site_map.json"
    checkpoint_file = OUTPUT_DIR / f"{prefix}phase2_checkpoint.json"

    if reset:
        output_file.unlink(missing_ok=True)
        checkpoint_file.unlink(missing_ok=True)

    listings = load_records(input_file)
    if not listings:
        raise FileNotFoundError(
            f"Phase 1 output not found at {input_file} — run phase 1 first"
        )

    checkpoint = load_checkpoint(checkpoint_file)
    processed_urls = set(checkpoint.get("processed", []))

    logger.info("Phase 2 start — %d listings, %d already processed",
                len(listings), len(processed_urls))

    for listing in listings:
        website_url = listing.get("website_url", "")

        if not website_url:
            key = listing["name"] + "_skipped"
            if key not in processed_urls:
                append_record(output_file, {**listing, "pages": {},
                                            "from_sitemap": False,
                                            "skip_reason": "no_website_url"})
                processed_urls.add(key)
                save_checkpoint(checkpoint_file, {"processed": list(processed_urls)})
            continue

        if website_url in processed_urls:
            logger.debug("Already processed %s, skipping", website_url)
            continue

        logger.info("Discovering site map for: %s  (%s)",
                    listing.get("name"), website_url)

        # Step 1: sitemap (cheap, no JS, ~1 fetch)
        sm_urls = fetch_sitemap_urls(website_url, max_index_depth=1)
        pages = classify_sitemap_urls(sm_urls) if sm_urls else {}
        from_sitemap = bool(pages)
        if pages:
            logger.info("  Sitemap categories: %s", list(pages.keys()))

        # Step 2: homepage nav links if critical missing
        if not _has_critical(pages):
            home = smart_fetch(website_url)
            if home is None:
                append_record(output_file, {**listing, "pages": pages,
                                            "from_sitemap": from_sitemap,
                                            "skip_reason": "fetch_failed"})
                processed_urls.add(website_url)
                save_checkpoint(checkpoint_file, {"processed": list(processed_urls)})
                continue

            final_url = getattr(home, "url", website_url) or website_url
            links = extract_same_domain_links(home, final_url)
            classified = classify_links(links)
            for cat, url in classified.items():
                pages.setdefault(cat, url)
            logger.info("  Homepage links added: %s", list(classified.keys()))

        # Step 3: targeted probe for missing categories
        if not _has_critical(pages) or len(pages) < 3:
            pages = probe_known_paths(website_url, pages, max_probes=3)
            logger.info("  After probing: %s", list(pages.keys()))

        record = {**listing, "pages": pages, "from_sitemap": from_sitemap}
        append_record(output_file, record)
        processed_urls.add(website_url)
        save_checkpoint(checkpoint_file, {"processed": list(processed_urls)})

    final = load_records(output_file)
    logger.info("Phase 2 complete — %d site maps saved to %s",
                len(final), output_file.name)
    return final
