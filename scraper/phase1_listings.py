"""
Phase 1: Scrape gelbeseiten.de for Berlin bookstore listings.
- Listing pages use &von=51, &von=101, ... offsets (50 cards/page)
- Website URL lives on each gelbeseiten detail page (/gsbiz/...)
Outputs: output/phase1_listings.json  (or output/test_phase1_listings.json in test mode)
Checkpoint: output/phase1_checkpoint.json
"""

import logging
from urllib.parse import urljoin

from scrapling.fetchers import DynamicFetcher

from .utils import (
    OUTPUT_DIR,
    append_record,
    load_checkpoint,
    load_records,
    save_checkpoint,
    smart_fetch,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gelbeseiten.de/suche/b%c3%bccher/berlin?umkreis=18000"
PAGE_SIZE = 50
MAX_LISTINGS = 200
TARGET_CITY = "Berlin"

EXCLUDE_DOMAINS = {
    "11880.com", "golocal.de", "dastelefonbuch.de", "dasoertliche.de",
    "apple.com", "bcrw.apple.com", "bfb.de", "play.google.com",
    "itunes.apple.com", "facebook.com", "instagram.com", "pinterest.de",
    "tiktok.com", "gelbeseiten.de",
}


def _listing_url(offset: int) -> str:
    if offset == 0:
        return BASE_URL
    return f"{BASE_URL}&von={offset + 1}"


def _is_business_website(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    return not any(domain.endswith(excl) for excl in EXCLUDE_DOMAINS)


def _get_website_from_detail(gs_url: str) -> str:
    """Visit gelbeseiten detail page and extract the business's own website URL."""
    page = smart_fetch(gs_url)
    if page is None:
        return ""
    url = page.css("a[data-wipe-realview='detailseite_webadresse']::attr(href)").get() or ""
    return url if _is_business_website(url) else ""


def run(reset: bool = False, limit: int = MAX_LISTINGS, prefix: str = "") -> list[dict]:
    output_file = OUTPUT_DIR / f"{prefix}phase1_listings.json"
    checkpoint_file = OUTPUT_DIR / f"{prefix}phase1_checkpoint.json"
    single_page_only = limit < PAGE_SIZE  # test mode: only 1 listing page

    if reset:
        output_file.unlink(missing_ok=True)
        checkpoint_file.unlink(missing_ok=True)

    checkpoint = load_checkpoint(checkpoint_file)
    last_offset = checkpoint.get("offset", -PAGE_SIZE)
    existing = load_records(output_file)
    seen_gs_urls = {r["gelbeseiten_url"] for r in existing}
    total = len(existing)

    logger.info("Phase 1 start — already have %d listings (last_offset=%d, limit=%d)", total, last_offset, limit)

    offset = last_offset + PAGE_SIZE

    while total < limit:
        url = _listing_url(offset)
        logger.info("Fetching listing page offset=%d: %s", offset, url)

        try:
            page = DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=30000)
        except Exception as e:
            logger.error("Failed to fetch offset=%d: %s", offset, e)
            break

        if not page or page.status != 200:
            logger.error("Non-200 at offset=%d: %s", offset, getattr(page, "status", "?"))
            break

        cards = page.css(".mod-Treffer")
        logger.info("Found %d cards at offset=%d", len(cards), offset)

        if not cards:
            logger.info("No cards found — end of results")
            break

        for card in cards:
            if total >= limit:
                break

            name = (card.css("h2.mod-Treffer__name::text").get() or "").strip()
            gs_url = card.xpath("./a/@href").get() or ""
            if gs_url and not gs_url.startswith("http"):
                gs_url = urljoin("https://www.gelbeseiten.de", gs_url)

            if not name or gs_url in seen_gs_urls:
                continue

            logger.info("  Fetching detail for: %s", name)
            website_url = _get_website_from_detail(gs_url) if gs_url else ""

            record = {
                "name": name,
                "website_url": website_url,
                "gelbeseiten_url": gs_url,
                "target_city": TARGET_CITY,
            }
            append_record(output_file, record)
            seen_gs_urls.add(gs_url)
            total += 1
            logger.info("  [%d] %s → %s", total, name, website_url or "(no website)")

        save_checkpoint(checkpoint_file, {"offset": offset, "count": total})

        if single_page_only or len(cards) < PAGE_SIZE:
            logger.info("Stopping after 1 page (test mode or last page)")
            break

        offset += PAGE_SIZE

    final = load_records(output_file)
    logger.info("Phase 1 complete — %d listings saved to %s", len(final), output_file.name)
    return final
