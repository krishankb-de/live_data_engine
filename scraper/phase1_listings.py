"""
Phase 1: Scrape gelbeseiten.de for Berlin bookstore listings — async + httpx.

Refactored from sequential headless-browser to:
  - Raw httpx (HTML only, no headless)
  - asyncio + Semaphore for bounded concurrency
  - tenacity exp-backoff retries
  - JSON-LD LocalBusiness parsing (derives website_url + is_verifiable)
  - UUID from /gsbiz/<uuid> as the listing key

Outputs: output/phase1_listings.json  (or output/test_phase1_listings.json)
"""

import asyncio
import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html as lxml_html
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .utils import OUTPUT_DIR, load_records

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gelbeseiten.de/suche/b%c3%bccher/berlin?umkreis=18000"
PAGE_SIZE = 50
MAX_LISTINGS = 200
TARGET_CITY = "Berlin"
CONCURRENCY = 10
HTTP_TIMEOUT = 20.0
RETRY_ATTEMPTS = 4

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

EXCLUDE_DOMAINS = {
    "11880.com", "golocal.de", "dastelefonbuch.de", "dasoertliche.de",
    "apple.com", "bcrw.apple.com", "bfb.de", "play.google.com",
    "itunes.apple.com", "facebook.com", "instagram.com", "pinterest.de",
    "tiktok.com", "gelbeseiten.de",
}

_UUID_RE = re.compile(r"/gsbiz/([0-9a-f-]{36})", re.IGNORECASE)
_RETRY_EXC = (httpx.HTTPError, httpx.TimeoutException)


def _listing_url(offset: int) -> str:
    return BASE_URL if offset == 0 else f"{BASE_URL}&von={offset + 1}"


def _gs_uuid(gs_url: str) -> str:
    m = _UUID_RE.search(gs_url or "")
    return m.group(1).lower() if m else ""


def _is_business_website(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    domain = urlparse(url).netloc.lstrip("www.")
    return not any(domain.endswith(excl) for excl in EXCLUDE_DOMAINS)


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    """Fetch URL with tenacity retries; returns HTML text or empty string on terminal failure."""
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type(_RETRY_EXC),
            reraise=True,
        ):
            with attempt:
                r = await client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
                r.raise_for_status()
                return r.text
    except Exception as e:
        logger.warning("Fetch failed after retries: %s — %s", url, e)
        return ""
    return ""


def _parse_cards(html_text: str) -> list[dict]:
    """Extract (name, gs_url, uuid) from a listing page."""
    if not html_text:
        return []
    tree = lxml_html.fromstring(html_text)
    seen_uuids: set[str] = set()
    out: list[dict] = []
    for card in tree.cssselect(".mod-Treffer"):
        name_el = card.cssselect("h2.mod-Treffer__name")
        name = name_el[0].text_content().strip() if name_el else ""
        href_list = card.xpath("./a/@href")
        gs_url = href_list[0] if href_list else ""
        if gs_url and not gs_url.startswith("http"):
            gs_url = urljoin("https://www.gelbeseiten.de", gs_url)
        uuid = _gs_uuid(gs_url)
        if not name or not uuid or uuid in seen_uuids:
            continue
        seen_uuids.add(uuid)
        out.append({"name": name, "gelbeseiten_url": gs_url, "gs_uuid": uuid})
    return out


def _parse_local_business(html_text: str) -> dict:
    """Extract JSON-LD LocalBusiness from a detail page. Empty dict if absent/malformed."""
    if not html_text:
        return {}
    tree = lxml_html.fromstring(html_text)
    for raw in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(raw)
        except Exception:
            continue
        candidates = data if isinstance(data, list) else [data]
        for d in candidates:
            if not isinstance(d, dict):
                continue
            t = d.get("@type")
            types = t if isinstance(t, list) else [t]
            if any("LocalBusiness" in str(x) or "Store" in str(x) for x in types):
                return d
    return {}


def _enrich_from_jsonld(record: dict, ld: dict) -> dict:
    """Fill website_url + is_verifiable using JSON-LD; mutate-and-return."""
    same_as = ld.get("sameAs") or ""
    if isinstance(same_as, list):
        same_as = next((u for u in same_as if isinstance(u, str) and u.startswith("http")), "")
    website = same_as if _is_business_website(same_as) else ""

    addr = ld.get("address") or {}
    has_address = isinstance(addr, dict) and bool(addr.get("streetAddress") or addr.get("addressLocality"))
    is_verifiable = bool(ld.get("name")) and has_address

    record["website_url"] = website
    record["is_verifiable"] = is_verifiable
    return record


async def _process_detail(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, card: dict
) -> dict:
    async with sem:
        html_text = await _fetch(client, card["gelbeseiten_url"])
    ld = _parse_local_business(html_text)
    record = {
        "name": card["name"],
        "website_url": "",
        "gelbeseiten_url": card["gelbeseiten_url"],
        "target_city": TARGET_CITY,
        "gs_uuid": card["gs_uuid"],
        "is_verifiable": False,
    }
    return _enrich_from_jsonld(record, ld)


async def _discover_cards(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, limit: int, single_page: bool
) -> list[dict]:
    """Fetch all listing pages concurrently, dedupe by uuid, cap at limit."""
    offsets = [0] if single_page else list(range(0, limit + PAGE_SIZE, PAGE_SIZE))

    async def _one(off: int) -> list[dict]:
        async with sem:
            html_text = await _fetch(client, _listing_url(off))
        cards = _parse_cards(html_text)
        logger.info("Listing offset=%d → %d cards", off, len(cards))
        return cards

    pages = await asyncio.gather(*[_one(o) for o in offsets])
    seen: set[str] = set()
    merged: list[dict] = []
    for batch in pages:
        for c in batch:
            if c["gs_uuid"] in seen:
                continue
            seen.add(c["gs_uuid"])
            merged.append(c)
            if len(merged) >= limit:
                return merged
    return merged


async def _run_async(limit: int, single_page: bool, existing: list[dict]) -> list[dict]:
    existing_by_uuid = {r.get("gs_uuid"): r for r in existing if r.get("gs_uuid")}
    sem = asyncio.Semaphore(CONCURRENCY)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}

    async with httpx.AsyncClient(headers=headers, http2=False) as client:
        cards = await _discover_cards(client, sem, limit, single_page)
        logger.info("Discovered %d unique listings", len(cards))

        todo = [c for c in cards if c["gs_uuid"] not in existing_by_uuid]
        logger.info("Fetching %d detail pages (%d cached)", len(todo), len(cards) - len(todo))

        results = await asyncio.gather(*[_process_detail(client, sem, c) for c in todo])

    records: list[dict] = []
    for c in cards:
        if c["gs_uuid"] in existing_by_uuid:
            records.append(existing_by_uuid[c["gs_uuid"]])
    records.extend(results)
    return records


def run(reset: bool = False, limit: int = MAX_LISTINGS, prefix: str = "") -> list[dict]:
    output_file = OUTPUT_DIR / f"{prefix}phase1_listings.json"
    single_page = limit < PAGE_SIZE

    if reset:
        output_file.unlink(missing_ok=True)

    existing = load_records(output_file)
    logger.info("Phase 1 start — %d existing records, limit=%d", len(existing), limit)

    records = asyncio.run(_run_async(limit=limit, single_page=single_page, existing=existing))

    with open(output_file, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    verifiable = sum(1 for r in records if r.get("is_verifiable"))
    with_site  = sum(1 for r in records if r.get("website_url"))
    logger.info(
        "Phase 1 complete — %d listings (%d verifiable, %d with website) → %s",
        len(records), verifiable, with_site, output_file.name,
    )
    return records
