"""Doorman: conditional-GET + change detection per page.

The "did anything change?" gate that sits between the scheduler and the
expensive extractor.

Behaviour:
  - Use stored ETag / Last-Modified for `If-None-Match` / `If-Modified-Since`.
  - If server replies 304 → page UNCHANGED, no body returned, scheduler can skip extractor.
  - If 200 → page possibly changed. Also compute a content hash so subsequent
    runs can detect "200 was returned but body identical to last time" (servers
    that don't send ETags will still let us short-circuit on hash).
  - If 404 → page VANISHED. Recipe likely needs a re-learn at the site level.
  - On 429 / 503 / Retry-After → back the domain off via the politeness gate.
"""

from __future__ import annotations

import hashlib
import re
import ssl
from typing import Literal

import httpx
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings
from pipeline_ug.politeness import DomainPolitenessGate

Outcome = Literal[
    "unchanged",   # 304 or 200 with identical hash
    "changed",     # 200 with new content
    "vanished",    # 404 (sustained, ideally)
    "error",       # transport / 5xx / etc
    "cooled_down", # politeness gate had this domain cooled — skipped
]


class DoormanResult(BaseModel):
    page_url: str
    outcome: Outcome
    http_status: int
    body: str | None = None         # None when unchanged/vanished/error
    new_etag: str | None = None
    new_last_modified: str | None = None
    new_content_hash: str | None = None
    error: str | None = None


_NOISE_RE = re.compile(
    r"<(script|style|noscript|head)[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WS_RE = re.compile(r"\s+")

# Footer locators — tried in order. We prefer the HTML5 <footer> tag, then
# fall back to common CMS conventions (`id="footer"` / `class="…footer…"`).
_FOOTER_TAG_RE = re.compile(
    r"<footer\b[^>]*>(.*?)</footer>",
    re.DOTALL | re.IGNORECASE,
)
# Match only the opening tag of a footer-named div; we grab everything from
# that point onward (up to _FOOTER_FALLBACK_BYTES) rather than trying to
# close the div tag — the lazy `.*?</div>` would stop at the first *nested*
# </div>, silently discarding the rest of the footer's content.
_FOOTER_DIV_START_RE = re.compile(
    r'<div\b[^>]*(?:id|class)\s*=\s*["\'][^"\']*\bfooter\b[^"\']*["\'][^>]*>',
    re.DOTALL | re.IGNORECASE,
)
_FOOTER_FALLBACK_BYTES = 4096

# Sanity-check failsafe. A 200 OK does NOT mean we got the real page —
# Cloudflare challenges, captchas, and generic CMS error stubs all return 200.
# If the response doesn't look like a real page, we'd rather treat it as a
# transient error and back off than feed garbage into extraction.
#
# Strong markers: unambiguous WAF/bot-challenge phrases → always reject.
# Weak markers: generic phrases that also appear in legitimate page copy
#   (e.g. "Access denied to this section") → only reject when the page has
#   no footer / copyright text, i.e. it really is a bare challenge stub.
_STRONG_CHALLENGE_MARKERS = (
    "cloudflare",
    "just a moment",
    "checking your browser",
    "please verify you are human",
    "are you a robot",
)
_WEAK_CHALLENGE_MARKERS = (
    "captcha",
    "access denied",
    "request blocked",
)
_FOOTER_MARKERS = (
    "<footer",
    "©",
    "copyright",
    "all rights reserved",
    "impressum",  # German legal-footer marker — relevant for this dataset
)
_MIN_PAGE_BYTES = 500


def looks_legit(body: str) -> tuple[bool, str | None]:
    """Footer / sanity check. Returns (ok, reason_if_not)."""
    if not body or len(body) < _MIN_PAGE_BYTES:
        return False, f"body too small ({len(body) if body else 0} bytes)"
    low = body.lower()
    # Strong markers: reject immediately regardless of footer content.
    for marker in _STRONG_CHALLENGE_MARKERS:
        if marker in low:
            return False, f"challenge marker present: {marker!r}"
    has_footer = any(marker in low for marker in _FOOTER_MARKERS)
    if not has_footer:
        # No footer → either a bare challenge stub or a broken page.
        # Check weak markers here; on real pages they won't appear without footer.
        for marker in _WEAK_CHALLENGE_MARKERS:
            if marker in low:
                return False, f"challenge marker present: {marker!r}"
        return False, "no footer / copyright markers found"
    return True, None


def extract_footer(body: str) -> tuple[str, str]:
    """Return (footer_block, locator) where locator names how we found it.

    locator ∈ {"footer_tag", "footer_div", "tail_fallback"} — exposed for
    debugging and so callers can audit-log when the fallback fires.

    Order of attempts:
      1. <footer>…</footer> — HTML5 semantic tag
      2. <div class="…footer…" | id="footer">…  — common CMS pattern;
         we capture everything from the opening tag onward (capped at
         _FOOTER_FALLBACK_BYTES) rather than closing on </div>, because a
         lazy .*?</div> would stop at the first *nested* closing tag and
         silently drop the rest of the footer content.
      3. Last `_FOOTER_FALLBACK_BYTES` of the page — copyright + contact info
         almost always lives near the bottom even when not marked up
    """
    m = _FOOTER_TAG_RE.search(body)
    if m:
        return m.group(1), "footer_tag"
    m = _FOOTER_DIV_START_RE.search(body)
    if m:
        return body[m.end():][:_FOOTER_FALLBACK_BYTES], "footer_div"
    return body[-_FOOTER_FALLBACK_BYTES:], "tail_fallback"


def content_hash(body: str) -> str:
    """Hash JUST the footer block of a page.

    Business listings (phone, address, opening hours, Impressum) almost always
    live in the footer. Hashing the whole page lights up the doorman on every
    blog post or banner change; hashing the footer focuses change-detection on
    the fields we actually care about — better signal-to-noise.

    Pipeline: strip scripts/styles/comments → locate footer → collapse
    whitespace → sha256-truncated-32.

    Backwards-compat note: pre-existing `last_content_hash` values in
    recipes.pages were computed over the whole page. The first recheck per
    domain after this change will look "changed" and trigger one extract; the
    new footer-only hash is stored afterwards and stabilises from then on.
    """
    cleaned = _NOISE_RE.sub("", body)
    cleaned = _COMMENT_RE.sub("", cleaned)
    footer, _locator = extract_footer(cleaned)
    normalized = _WS_RE.sub(" ", footer).strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()[:32]


async def doorman_fetch(
    client: httpx.AsyncClient,
    url: str,
    last_etag: str | None,
    last_modified: str | None,
    last_content_hash: str | None,
    politeness: DomainPolitenessGate,
) -> DoormanResult:
    # Short-circuit if domain is currently cooled-down
    cool_now, remaining = politeness.is_cool(url)
    if not cool_now:
        return DoormanResult(
            page_url=url,
            outcome="cooled_down",
            http_status=0,
            error=f"domain cooled for another {remaining:.0f}s",
        )

    await politeness.acquire(url)

    headers: dict[str, str] = {}
    if last_etag:
        headers["If-None-Match"] = last_etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    @retry(
        reraise=True,
        retry=retry_if_exception_type((httpx.HTTPError, ssl.SSLError, OSError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
    )
    async def _do_get():
        return await client.get(url, headers=headers)

    try:
        r = await _do_get()
    except (httpx.HTTPError, ssl.SSLError, OSError) as exc:
        return DoormanResult(
            page_url=url, outcome="error", http_status=0, error=f"{type(exc).__name__}: {exc}"
        )

    # 304 — server confirms nothing changed.
    # RFC 7232: server MAY include an updated ETag in the 304 response (common
    # on CDN reconfigs); prefer it over the caller's stored value so we track
    # the freshest validator.
    if r.status_code == 304:
        return DoormanResult(
            page_url=url,
            outcome="unchanged",
            http_status=304,
            new_etag=r.headers.get("etag") or last_etag,
            new_last_modified=r.headers.get("last-modified") or last_modified,
            new_content_hash=last_content_hash,
        )

    # 200 — possibly changed; verify via content hash
    if r.status_code == 200:
        body = r.text

        # Failsafe: a 200 isn't enough — verify the response looks like a real
        # page (has footer/copyright markers, no challenge stubs). If it
        # doesn't, cool the domain and let the caller retry later.
        legit, why = looks_legit(body)
        if not legit:
            politeness.back_off(url, 30.0)
            return DoormanResult(
                page_url=url,
                outcome="error",
                http_status=200,
                error=f"page failed sanity check: {why}",
            )

        h = content_hash(body)
        if last_content_hash and h == last_content_hash:
            return DoormanResult(
                page_url=url,
                outcome="unchanged",
                http_status=200,
                new_etag=r.headers.get("etag"),
                new_last_modified=r.headers.get("last-modified"),
                new_content_hash=h,
            )
        return DoormanResult(
            page_url=url,
            outcome="changed",
            http_status=200,
            body=body,
            new_etag=r.headers.get("etag"),
            new_last_modified=r.headers.get("last-modified"),
            new_content_hash=h,
        )

    # 404 — page is gone
    if r.status_code == 404:
        return DoormanResult(
            page_url=url, outcome="vanished", http_status=404, error="404"
        )

    # 429 / 5xx — back off and tell caller
    if r.status_code in (429, 503):
        retry_after = r.headers.get("retry-after")
        try:
            secs = float(retry_after) if retry_after else 30.0
        except ValueError:
            secs = 30.0
        politeness.back_off(url, max(secs, 30.0))
        return DoormanResult(
            page_url=url,
            outcome="error",
            http_status=r.status_code,
            error=f"rate-limited (Retry-After={retry_after}); cooled {secs}s",
        )

    return DoormanResult(
        page_url=url,
        outcome="error",
        http_status=r.status_code,
        error=f"unexpected status {r.status_code}",
    )


def url_for_page(base_url: str, url_path: str) -> str:
    """Stitch a recipe's url_path onto the listing's website_url.

    If url_path is already an absolute URL it is returned unchanged — recipes
    occasionally store the full URL discovered during site-map discovery.
    """
    if not base_url:
        return url_path
    if url_path.startswith("http://") or url_path.startswith("https://"):
        return url_path
    base = base_url.rstrip("/")
    path = url_path if url_path.startswith("/") else "/" + url_path
    return base + path


# Default politeness gate used by callers that don't bring their own.
default_politeness = DomainPolitenessGate(settings.per_domain_rate_limit_seconds)
