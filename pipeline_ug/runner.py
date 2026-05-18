"""Single-listing doorman pre-check.

`run_listing(id)` does a cheap conditional-GET sweep over a listing's tracked
pages, persists any new ETag / Last-Modified / content-hash back to recipes.pages,
and returns a trace describing whether anything changed.

This module does NOT extract structured data. When doorman flags a page as
`changed`, it's the caller's job (see `api.tasks.run_recheck_batch_task`) to
invoke the proper extractor (`scraper.phase3_extract.extract_site`) and to
reschedule via `pipeline_ug.scheduler.update_next_check`.

Listings without a recipe still get rechecked on the homepage (`website_url`),
so the doorman is useful even before the brain has learned per-page selectors.
"""

from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

import db_repo
from pipeline_ug.doorman import (
    DoormanResult,
    default_politeness,
    doorman_fetch,
    url_for_page,
)
from pipeline_ug.http import make_client

logger = logging.getLogger(__name__)

RunOutcome = Literal[
    "ok",                     # at least one page changed
    "all_unchanged",          # every page returned 304 / identical hash
    "vanished",               # one or more pages returned 404
    "skipped_unverifiable",   # listing.is_verifiable = false
    "skipped_no_website",     # listing has no website_url
    "error",                  # transport / unexpected
]


class PageRun(BaseModel):
    page: str
    url: str
    doorman: DoormanResult


class ListingRunTrace(BaseModel):
    listing_id: int
    gs_listing_id: str
    website_url: str | None
    outcome: RunOutcome
    reason: str | None = None
    page_runs: list[PageRun] = []


def _default_pages() -> dict:
    """Fallback page-set used when no recipe exists for this domain yet."""
    return {
        "home": {
            "url_path": "/",
            "last_etag": None,
            "last_modified": None,
            "last_content_hash": None,
        }
    }


async def run_listing(listing_id: int, client: httpx.AsyncClient | None = None) -> ListingRunTrace:
    listing = db_repo.get_listing(listing_id)
    if listing is None:
        return ListingRunTrace(
            listing_id=listing_id,
            gs_listing_id="",
            website_url=None,
            outcome="error",
            reason="listing not found",
        )

    gs_id = str(listing.get("gs_listing_id") or "")
    website_url = listing.get("website_url")

    if not listing.get("is_verifiable", True):
        return ListingRunTrace(
            listing_id=listing_id,
            gs_listing_id=gs_id,
            website_url=website_url,
            outcome="skipped_unverifiable",
            reason=listing.get("unverifiable_reason") or "is_verifiable=false",
        )

    if not website_url:
        return ListingRunTrace(
            listing_id=listing_id,
            gs_listing_id=gs_id,
            website_url=None,
            outcome="skipped_no_website",
            reason="no website_url on listing",
        )

    domain = urlparse(website_url).netloc.lower()
    if not domain:
        return ListingRunTrace(
            listing_id=listing_id,
            gs_listing_id=gs_id,
            website_url=website_url,
            outcome="error",
            reason=f"could not parse domain from {website_url!r}",
        )

    recipe_row = db_repo.get_recipe_pages(domain)
    pages = (recipe_row or {}).get("pages") if recipe_row else None
    if not isinstance(pages, dict) or not pages:
        pages = _default_pages()

    # Make per-page state mutable so we can write new etags/hashes back.
    pages = {name: dict(state or {}) for name, state in pages.items()}

    own_client = client is None
    if own_client:
        cm = make_client()
        client = await cm.__aenter__()
    try:
        page_runs: list[PageRun] = []
        any_changed = False
        any_vanished = False

        for page_name, state in pages.items():
            url_path = state.get("url_path") or "/"
            full_url = url_for_page(website_url, url_path)
            dres = await doorman_fetch(
                client=client,
                url=full_url,
                last_etag=state.get("last_etag"),
                last_modified=state.get("last_modified"),
                last_content_hash=state.get("last_content_hash"),
                politeness=default_politeness,
            )
            # Persist any new conditional-GET state for next time.
            if dres.new_etag is not None:
                state["last_etag"] = dres.new_etag
            if dres.new_last_modified is not None:
                state["last_modified"] = dres.new_last_modified
            if dres.new_content_hash is not None:
                state["last_content_hash"] = dres.new_content_hash
            pages[page_name] = state

            if dres.outcome == "changed":
                any_changed = True
            elif dres.outcome == "vanished":
                any_vanished = True

            page_runs.append(PageRun(page=page_name, url=full_url, doorman=dres))
    finally:
        if own_client:
            await cm.__aexit__(None, None, None)  # type: ignore[has-type]

    # Save updated per-page doorman state back to Supabase.
    try:
        db_repo.save_recipe_pages(domain, pages)
    except Exception as exc:  # noqa: BLE001 — caller logs the trace
        return ListingRunTrace(
            listing_id=listing_id,
            gs_listing_id=gs_id,
            website_url=website_url,
            outcome="error",
            reason=f"save_recipe_pages failed: {exc}",
            page_runs=page_runs,
        )

    if any_vanished:
        outcome: RunOutcome = "vanished"
        reason = "one or more pages returned 404"
    elif any_changed:
        outcome = "ok"
        reason = None
    elif all(pr.doorman.outcome == "error" for pr in page_runs):
        outcome = "error"
        reason = "; ".join(pr.doorman.error or "" for pr in page_runs if pr.doorman.error)
    else:
        outcome = "all_unchanged"
        reason = "every page returned 304 or identical content hash"

    return ListingRunTrace(
        listing_id=listing_id,
        gs_listing_id=gs_id,
        website_url=website_url,
        outcome=outcome,
        reason=reason,
        page_runs=page_runs,
    )


