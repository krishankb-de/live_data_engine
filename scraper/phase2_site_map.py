"""
Phase 2: Discover imprint / contact / hours / branch pages per site — async + httpx.

Order (early termination per-site):
  1. /robots.txt → Sitemap directive, /sitemap.xml, /sitemap_index.xml
     (chase nested index, depth 1). Classify URLs by multilingual keywords.
  2. If <2 critical categories (imprint+contact) → fetch homepage,
     classify same-domain nav/footer links.
  3. For each category still missing → probe known paths (≤3 per category).

Parallelism: sites processed concurrently via asyncio.Semaphore.
Fetches: httpx + tenacity exp-backoff retries.

Outputs: output/phase2_site_map.json  (test_ prefix in test mode)
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

from .utils import (
    KEYWORD_MAP,
    KNOWN_PATHS,
    OUTPUT_DIR,
    load_records,
)

logger = logging.getLogger(__name__)

CRITICAL_CATEGORIES = ("imprint", "contact")
SITE_CONCURRENCY = 8       # parallel sites
INNER_CONCURRENCY = 6      # parallel sub-requests per site (probes etc.)
HTTP_TIMEOUT = 12.0
RETRY_ATTEMPTS = 3

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_SITEMAP_INDEX_RE = re.compile(r"<sitemap>\s*<loc>([^<]+)</loc>", re.IGNORECASE)
_SITEMAP_URL_RE = re.compile(r"<url>\s*<loc>([^<]+)</loc>", re.IGNORECASE)
_ROBOTS_SITEMAP_RE = re.compile(r"^\s*Sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_RETRY_EXC = (httpx.HTTPError, httpx.TimeoutException)


# ---------------------------------------------------------------------------
# Async fetch helpers
# ---------------------------------------------------------------------------

async def _fetch_text(client: httpx.AsyncClient, url: str) -> tuple[int, str, str]:
    """Fetch → (status, final_url, text). Tenacity-retried. Returns (0,'','') on fail."""
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(_RETRY_EXC),
            reraise=True,
        ):
            with attempt:
                r = await client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
                return r.status_code, str(r.url), r.text
    except Exception as e:
        logger.debug("Fetch failed %s — %s", url, e)
        return 0, "", ""


async def _head_ok(client: httpx.AsyncClient, url: str) -> bool:
    try:
        r = await client.head(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        if r.status_code == 200:
            return True
        if r.status_code in (405, 501):  # HEAD not allowed → fall back to GET
            r = await client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
            return r.status_code == 200
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sitemap discovery (async)
# ---------------------------------------------------------------------------

async def _fetch_sitemap_urls(client: httpx.AsyncClient, base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    candidates: list[str] = []
    _, _, robots_txt = await _fetch_text(client, origin + "/robots.txt")
    if robots_txt:
        candidates.extend(_ROBOTS_SITEMAP_RE.findall(robots_txt))
    candidates.extend([origin + "/sitemap.xml", origin + "/sitemap_index.xml"])

    seen: set[str] = set()
    urls: list[str] = []
    queue: list[tuple[str, int]] = [(c, 1) for c in candidates]  # (url, depth_remaining)

    while queue:
        sm_url, depth = queue.pop(0)
        if sm_url in seen:
            continue
        seen.add(sm_url)
        status, _, body = await _fetch_text(client, sm_url)
        if status != 200 or not body:
            continue
        for nested in _SITEMAP_INDEX_RE.findall(body):
            if nested not in seen and depth > 0:
                queue.append((nested, depth - 1))
        urls.extend(_SITEMAP_URL_RE.findall(body))

    return urls


def _classify_urls(urls: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for url in urls:
        ul = url.lower()
        for cat, kws in KEYWORD_MAP.items():
            if cat in result:
                continue
            if any(kw in ul for kw in kws):
                result[cat] = url
    return result


# ---------------------------------------------------------------------------
# Homepage link extraction + classify
# ---------------------------------------------------------------------------

def _extract_same_domain_links(html_text: str, base_url: str) -> list[dict]:
    if not html_text:
        return []
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return []
    base_netloc = urlparse(base_url).netloc
    out: list[dict] = []
    for a in tree.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        if not href.startswith("http"):
            href = urljoin(base_url, href)
        if urlparse(href).netloc != base_netloc:
            continue
        text = (a.text_content() or "").strip()
        out.append({"href": href, "text": text})
    return out


def _classify_links(links: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for link in links:
        href = (link.get("href") or "").lower()
        text = (link.get("text") or "").lower()
        combined = href + " " + text
        for cat, kws in KEYWORD_MAP.items():
            if cat in result:
                continue
            if any(kw in combined for kw in kws):
                result[cat] = link["href"]
    return result


# ---------------------------------------------------------------------------
# Path probing (async, parallel per category)
# ---------------------------------------------------------------------------

async def _probe_known_paths(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    existing: dict,
    max_probes: int = 3,
) -> dict:
    pages = dict(existing)
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    async def _probe(cat: str) -> tuple[str, str]:
        for path in KNOWN_PATHS[cat][:max_probes]:
            async with sem:
                if await _head_ok(client, origin + path):
                    return cat, origin + path
        return cat, ""

    todo = [c for c in KNOWN_PATHS if c not in pages]
    results = await asyncio.gather(*[_probe(c) for c in todo])
    for cat, url in results:
        if url:
            pages[cat] = url
    return pages


# ---------------------------------------------------------------------------
# Per-site pipeline
# ---------------------------------------------------------------------------

def _has_critical(pages: dict) -> bool:
    return all(c in pages for c in CRITICAL_CATEGORIES)


async def _process_site(
    client: httpx.AsyncClient,
    site_sem: asyncio.Semaphore,
    listing: dict,
) -> dict:
    website_url = listing.get("website_url") or ""

    if not website_url:
        return {**listing, "pages": {}, "from_sitemap": False,
                "skip_reason": "no_website_url"}

    async with site_sem:
        inner_sem = asyncio.Semaphore(INNER_CONCURRENCY)

        # Step 1: sitemap
        sm_urls = await _fetch_sitemap_urls(client, website_url)
        pages = _classify_urls(sm_urls) if sm_urls else {}
        from_sitemap = bool(pages)

        # Step 2: homepage if critical missing
        if not _has_critical(pages):
            status, final_url, home_html = await _fetch_text(client, website_url)
            if status != 200 or not home_html:
                return {**listing, "pages": pages, "from_sitemap": from_sitemap,
                        "skip_reason": "fetch_failed"}
            links = _extract_same_domain_links(home_html, final_url or website_url)
            for cat, url in _classify_links(links).items():
                pages.setdefault(cat, url)

        # Step 3: probe missing
        if not _has_critical(pages) or len(pages) < 3:
            pages = await _probe_known_paths(client, inner_sem, website_url, pages, max_probes=3)

    logger.info("[%s] categories=%s from_sitemap=%s",
                listing.get("name"), list(pages.keys()), from_sitemap)
    return {**listing, "pages": pages, "from_sitemap": from_sitemap}


async def _run_async(listings: list[dict], existing: list[dict]) -> list[dict]:
    existing_by_url = {
        r.get("website_url") or f"__noweb__{r.get('gs_uuid') or r.get('name')}": r
        for r in existing
    }

    site_sem = asyncio.Semaphore(SITE_CONCURRENCY)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.5"}

    todo: list[dict] = []
    keep: list[dict] = []
    for listing in listings:
        key = listing.get("website_url") or f"__noweb__{listing.get('gs_uuid') or listing.get('name')}"
        if key in existing_by_url:
            keep.append(existing_by_url[key])
        else:
            todo.append(listing)

    logger.info("Processing %d sites (%d cached)", len(todo), len(keep))

    async with httpx.AsyncClient(headers=headers, http2=False) as client:
        results = await asyncio.gather(*[_process_site(client, site_sem, l) for l in todo])

    return keep + list(results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(reset: bool = False, prefix: str = "") -> list[dict]:
    input_file  = OUTPUT_DIR / f"{prefix}phase1_listings.json"
    output_file = OUTPUT_DIR / f"{prefix}phase2_site_map.json"

    if reset:
        output_file.unlink(missing_ok=True)

    listings = load_records(input_file)
    if not listings:
        raise FileNotFoundError(
            f"Phase 1 output not found at {input_file} — run phase 1 first"
        )

    existing = load_records(output_file)
    logger.info("Phase 2 start — %d listings, %d existing records",
                len(listings), len(existing))

    records = asyncio.run(_run_async(listings, existing))

    with open(output_file, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    with_pages = sum(1 for r in records if r.get("pages"))
    logger.info("Phase 2 complete — %d records (%d with pages) → %s",
                len(records), with_pages, output_file.name)
    return records
