"""Shared utilities: fetching, checkpoints, link helpers, multilingual classifier."""

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def load_checkpoint(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_checkpoint(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def append_record(path: Path, record: dict) -> None:
    records = []
    if path.exists():
        with open(path) as f:
            records = json.load(f)
    records.append(record)
    with open(path, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def load_records(path: Path) -> list:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


# ---------------------------------------------------------------------------
# Smart fetch
# ---------------------------------------------------------------------------

def smart_fetch(url: str, retries: int = 3, delay: float = 2.0, min_content: int = 300):
    """Static fetch first; fall back to DynamicFetcher when needed."""
    from scrapling.fetchers import Fetcher, DynamicFetcher

    try:
        page = Fetcher.get(url, timeout=15, stealthy_headers=True)
        if page and page.status == 200:
            text = get_page_text(page)
            if len(text) >= min_content:
                return page
            logger.warning("Thin static content (%d chars) for %s, switching to dynamic", len(text), url)
        else:
            logger.warning("Non-200 from %s (%s), switching to dynamic", url, getattr(page, "status", "?"))
    except Exception as e:
        logger.warning("Static fetch failed for %s: %s", url, e)

    for attempt in range(1, retries + 1):
        try:
            time.sleep(delay)
            page = DynamicFetcher.fetch(url, headless=True, network_idle=True, timeout=20000)
            if page and page.status == 200:
                return page
            logger.warning("Dynamic attempt %d/%d non-200 for %s", attempt, retries, url)
        except Exception as e:
            logger.warning("Dynamic attempt %d/%d failed for %s: %s", attempt, retries, url, e)

    logger.error("All %d attempts failed for %s", retries, url)
    return None


# ---------------------------------------------------------------------------
# Multilingual link classifier
# ---------------------------------------------------------------------------

KEYWORD_MAP = {
    "imprint": [
        "impressum", "imprint", "legal", "rechtliches", "legal-notice",
        "mentions-legales", "mentions_legales", "mentions legales",
    ],
    "hours": [
        "öffnungszeiten", "oeffnungszeiten", "opening-hours", "opening_hours",
        "hours", "zeiten", "horaires",
    ],
    "contact": [
        "kontakt", "contact", "anfahrt", "erreichen", "contact-us", "contactez",
    ],
    "branch": [
        "filiale", "filialen", "branch", "standort", "standorte",
        "locations", "succursale", "magasins",
    ],
}

KNOWN_PATHS: dict[str, list[str]] = {
    "imprint": [
        "/impressum", "/impressum/", "/impressum.html", "/impressum.php",
        "/imprint", "/legal", "/legal-notice", "/mentions-legales",
        "/mentions-legales/", "/ueber-uns/impressum",
    ],
    "hours": [
        "/oeffnungszeiten", "/oeffnungszeiten/", "/opening-hours",
        "/hours", "/horaires", "/horaires/",
    ],
    "contact": [
        "/kontakt", "/kontakt/", "/contact", "/contact/", "/contact-us",
        "/kontakt.html", "/anfahrt",
    ],
    "branch": [
        "/filialen", "/filialen/", "/standorte", "/standort",
        "/branch", "/locations", "/succursale", "/magasins",
    ],
}


def classify_links(links: list[dict]) -> dict[str, str]:
    """Match anchor text + href against keyword sets; first match wins."""
    result: dict[str, str] = {}
    for link in links:
        href = (link.get("href") or "").lower()
        text = (link.get("text") or "").lower()
        combined = href + " " + text
        for category, keywords in KEYWORD_MAP.items():
            if category in result:
                continue
            if any(kw in combined for kw in keywords):
                result[category] = link.get("href", "")
    return result


def probe_known_paths(base_url: str, existing_pages: dict, max_probes: int = 3) -> dict:
    """For each category still missing, probe up to max_probes known paths."""
    from scrapling.fetchers import Fetcher

    pages = dict(existing_pages)
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    for category, paths in KNOWN_PATHS.items():
        if category in pages:
            continue
        probes = 0
        for path in paths:
            if probes >= max_probes:
                break
            candidate = origin + path
            probes += 1
            try:
                page = Fetcher.get(candidate, timeout=8, stealthy_headers=True)
                if page and page.status == 200:
                    logger.info("  Probe found %s → %s", category, candidate)
                    pages[category] = candidate
                    break
            except Exception:
                continue
    return pages


# ---------------------------------------------------------------------------
# Sitemap helpers
# ---------------------------------------------------------------------------

_SITEMAP_INDEX_RE = re.compile(r"<sitemap>\s*<loc>([^<]+)</loc>", re.IGNORECASE)
_SITEMAP_URL_RE = re.compile(r"<url>\s*<loc>([^<]+)</loc>", re.IGNORECASE)
_ROBOTS_SITEMAP_RE = re.compile(r"^\s*Sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def fetch_sitemap_urls(base_url: str, max_index_depth: int = 1) -> list[str]:
    """Try /robots.txt → Sitemap directive, then /sitemap.xml, then /sitemap_index.xml.
    Chase nested sitemap indexes up to max_index_depth."""
    from scrapling.fetchers import Fetcher

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    candidates: list[str] = []
    try:
        r = Fetcher.get(origin + "/robots.txt", timeout=8, stealthy_headers=True)
        if r and r.status == 200:
            txt = get_page_text(r)
            candidates.extend(_ROBOTS_SITEMAP_RE.findall(txt or ""))
    except Exception:
        pass

    candidates.extend([origin + "/sitemap.xml", origin + "/sitemap_index.xml"])

    seen: set[str] = set()
    urls: list[str] = []
    queue = list(candidates)
    depth_remaining = {u: max_index_depth + 1 for u in candidates}

    while queue:
        sm_url = queue.pop(0)
        if sm_url in seen:
            continue
        seen.add(sm_url)
        try:
            r = Fetcher.get(sm_url, timeout=8, stealthy_headers=True)
            if not r or r.status != 200:
                continue
            body = getattr(r, "body", None) or get_page_text(r)
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="ignore")
            # Sitemap index → nested sitemaps
            for nested in _SITEMAP_INDEX_RE.findall(body):
                if nested not in seen and depth_remaining.get(sm_url, 0) > 0:
                    queue.append(nested)
                    depth_remaining[nested] = depth_remaining[sm_url] - 1
            # Actual URLs
            urls.extend(_SITEMAP_URL_RE.findall(body))
        except Exception:
            continue

    return urls


def classify_sitemap_urls(urls: list[str]) -> dict[str, str]:
    """Filter sitemap URLs by keyword; first match per category wins."""
    result: dict[str, str] = {}
    for url in urls:
        url_lower = url.lower()
        for category, keywords in KEYWORD_MAP.items():
            if category in result:
                continue
            if any(kw in url_lower for kw in keywords):
                result[category] = url
    return result


# ---------------------------------------------------------------------------
# Page text + link helpers
# ---------------------------------------------------------------------------

def get_page_text(page) -> str:
    try:
        text = page.get_all_text(ignore_tags=("script", "style", "noscript", "head"))
        if text and text.strip():
            return text
    except Exception:
        pass
    try:
        return " ".join(page.css("body::text").getall())
    except Exception:
        return ""


def normalized_text_hash(page) -> str:
    """SHA-256 of lower-cased, whitespace-collapsed body text. For Phase 4."""
    raw = get_page_text(page)
    norm = re.sub(r"\s+", " ", raw.lower()).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def extract_same_domain_links(page, base_url: str) -> list[dict]:
    links = []
    base_netloc = urlparse(base_url).netloc
    try:
        for a in page.css("a"):
            href = (a.attrib.get("href") or "").strip()
            text = (a.css("::text").get() or "").strip()
            if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                continue
            if not href.startswith("http"):
                href = urljoin(base_url, href)
            if urlparse(href).netloc == base_netloc:
                links.append({"href": href, "text": text})
    except Exception as e:
        logger.debug("Link extraction error: %s", e)
    return links


def _is_asset_url(url: str) -> bool:
    asset_exts = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
                  ".css", ".js", ".woff", ".woff2", ".ttf", ".pdf",
                  ".zip", ".xml", ".json", ".ico")
    path = urlparse(url).path.lower().split("?")[0]
    return any(path.endswith(ext) for ext in asset_exts)


# ---------------------------------------------------------------------------
# Contact-block slicer (Layer C)
# ---------------------------------------------------------------------------

_CONTACT_HEADING_RE = re.compile(
    r'\b(Kontakt|Contact|Adresse|Address|Anschrift|Coordonn(?:é|e)es|Standort|Impressum)\b',
    re.IGNORECASE,
)


def contact_block(text: str, window: int = 400) -> Optional[str]:
    m = _CONTACT_HEADING_RE.search(text)
    if not m:
        return None
    return text[m.start(): m.start() + window]
