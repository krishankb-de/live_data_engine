"""LLM-powered field-selector recipes (Phase 3 fallback).

Flow per site:
  1. JSON-LD + regex run first (existing code in phase3_extract._extract_layers).
  2. If ANY required field is still missing AFTER all pages visited, this module
     is called once. It either:
       (a) returns the cached recipe for the domain and applies it, or
       (b) calls the LLM once to build a fresh recipe covering ALL missing fields.
  3. State machine on recipes.status:
       active → recipe is current (default after build)
       stale  → recipe failed once; rebuilt once
       failed → give up; flagged for human review, no further LLM calls
  4. Hard cap: 2 LLM calls per domain (initial build + 1 rebuild on staleness).

Recipe shape (matches recipes.field_selectors JSONB column in supabase/schema.sql):
  {
    "phone":         {"page_url": "https://x.de/kontakt",  "css": ".footer .tel"},
    "address":       {"page_url": "https://x.de/impressum","css": "address.adr"},
    "opening_hours": {"page_url": "https://x.de/zeiten",   "css": "ul.opening li"},
    "name":          null,
  }
A null/missing entry means: LLM looked, field is absent on this site (→ negative_cache).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("name", "address", "phone", "opening_hours")
MAX_HTML_CHARS_PER_PAGE = 8000
RECIPES_FILE = Path(__file__).parent.parent / "output" / "recipes.json"


# ---------------------------------------------------------------------------
# Recipe data model
# ---------------------------------------------------------------------------

@dataclass
class Recipe:
    domain: str
    field_selectors: dict[str, Optional[dict]] = field(default_factory=dict)
    negative_cache: list[str] = field(default_factory=list)
    generalized_fields: list[str] = field(default_factory=list)
    status: str = "active"            # active | stale | failed
    recipe_version: int = 1
    last_hash: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "field_selectors": self.field_selectors,
            "negative_cache": self.negative_cache,
            "generalized_fields": self.generalized_fields,
            "status": self.status,
            "recipe_version": self.recipe_version,
            "last_hash": self.last_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        return cls(
            domain=d["domain"],
            field_selectors=d.get("field_selectors") or {},
            negative_cache=d.get("negative_cache") or [],
            generalized_fields=d.get("generalized_fields") or [],
            status=d.get("status", "active"),
            recipe_version=d.get("recipe_version", 1),
            last_hash=d.get("last_hash"),
        )


# ---------------------------------------------------------------------------
# Storage interface — JSON for dev, Supabase later
# ---------------------------------------------------------------------------

class RecipeStore:
    """File-backed recipe store. Swap implementation with Supabase later
    without touching callers."""

    def __init__(self, path: Path = RECIPES_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        with open(self.path) as f:
            return json.load(f)

    def _save_all(self, data: dict[str, dict]) -> None:
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get(self, domain: str) -> Optional[Recipe]:
        data = self._load_all()
        if domain not in data:
            return None
        return Recipe.from_dict(data[domain])

    def upsert(self, recipe: Recipe) -> None:
        data = self._load_all()
        data[recipe.domain] = recipe.to_dict()
        self._save_all(data)


# ---------------------------------------------------------------------------
# Selector application — pure, deterministic
# ---------------------------------------------------------------------------

def _element_text(el) -> str:
    text = " ".join(el.css("::text").getall())
    return _WHITESPACE_RE.sub(" ", text).strip()


def _page_text(page: Any) -> str:
    """Page-wide text for regex-only selectors."""
    from .utils import get_page_text
    try:
        return get_page_text(page) or ""
    except Exception:
        return ""


def apply_recipe(
    recipe: Recipe,
    fetched_pages: dict[str, Any],
    missing_fields: list[str],
) -> dict[str, str]:
    """Run recipe.field_selectors against already-fetched scrapling pages.

    A selector entry may have `css`, `regex`, or both:
      - css only      → first matching element's text
      - regex only    → re.search against page text
      - css + regex   → re.search inside first matching element's text
    """
    out: dict[str, str] = {}
    for field_name in missing_fields:
        sel = recipe.field_selectors.get(field_name)
        if not sel or not sel.get("page_url"):
            continue
        css = sel.get("css")
        regex = sel.get("regex")
        if not css and not regex:
            continue
        page = fetched_pages.get(sel["page_url"])
        if page is None:
            continue

        try:
            # 1. Get the text to search in
            if css:
                text = ""
                for el in page.css(css):
                    t = _element_text(el)
                    if t:
                        text = t
                        break
                if not text:
                    continue
            else:
                text = _page_text(page)
                if not text:
                    continue

            # 2. Apply regex if present, else use full text
            if regex:
                try:
                    m = re.search(regex, text, re.IGNORECASE | re.UNICODE)
                except re.error as e:
                    logger.debug("Bad regex in recipe for %s: %s", field_name, e)
                    continue
                if not m:
                    continue
                value = (m.group(1) if m.groups() else m.group(0)).strip()
            else:
                value = text

            if value:
                out[field_name] = value
        except Exception as e:
            logger.debug("Recipe apply failed for %s (css=%s regex=%s): %s",
                         field_name, css, regex, e)
    return out


def _sanitize_selector(sel: Optional[dict]) -> Optional[dict]:
    """Drop a regex that won't compile; drop the whole entry if nothing usable remains."""
    if not sel:
        return sel
    cleaned: dict = {"page_url": sel.get("page_url")}
    if sel.get("css"):
        cleaned["css"] = sel["css"]
    if sel.get("regex"):
        try:
            re.compile(sel["regex"])
            cleaned["regex"] = sel["regex"]
        except re.error as e:
            logger.warning("Dropping invalid recipe regex %r: %s", sel["regex"], e)
    if not cleaned.get("page_url") or (not cleaned.get("css") and not cleaned.get("regex")):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# LLM call — single shot, all missing fields at once
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You generate extraction rules (CSS selectors and/or regex) to pull specific fields from HTML.

You will receive:
- A list of fields we want (e.g. phone, address, opening_hours, name)
- HTML snippets from several pages of the same website, each tagged with its URL

Return ONLY JSON of the form:
{
  "selectors": {
    "<field>": {
        "page_url": "<one of the URLs you were given>",
        "css":   "<css selector>",   // optional
        "regex": "<python regex>"    // optional; use group(1) if a capture group is given
    },
    "<field>": null   // use null if the field is truly absent across ALL given pages
  }
}

Choosing the tool:
- Prefer **css alone** when a tight wrapper element contains exactly the value.
- Use **css + regex** when the wrapper exists but contains extra text around the value
  (e.g. element text is "Tel: +49 30 1234567 (Mon-Fri)" — css narrows to that element,
   regex extracts just the phone). Put the desired part in capture group 1.
- Use **regex alone** only when no element cleanly wraps the value (rare). It will be
  matched against the full page text.

Rules:
- At least one of css/regex must be present (or the whole entry must be null).
- Do NOT invent URLs; use exactly the page_url strings provided.
- Regex must be valid Python `re` syntax; escape backslashes properly in JSON.
- If you cannot find the field on ANY page, return null. Do not guess.
- No prose, no markdown, no code fences. JSON only.
"""

_TAG_STRIP_RE = re.compile(r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")


def _condense_html(html: str, limit: int = MAX_HTML_CHARS_PER_PAGE) -> str:
    """Strip scripts/styles/comments, collapse whitespace, truncate."""
    if not html:
        return ""
    html = _TAG_STRIP_RE.sub("", html)
    html = _COMMENT_RE.sub("", html)
    html = _WHITESPACE_RE.sub(" ", html)
    if len(html) > limit:
        html = html[:limit] + "…[truncated]"
    return html


def _page_html(page: Any) -> str:
    """Best-effort HTML extraction from a scrapling page object."""
    for attr in ("html_content", "text", "body"):
        val = getattr(page, attr, None)
        if val:
            if isinstance(val, bytes):
                val = val.decode("utf-8", errors="ignore")
            if isinstance(val, str):
                return val
    return str(page)


def _llm_call(missing_fields: list[str], pages_html: dict[str, str]) -> dict:
    """Single OpenAI call. Returns parsed JSON dict {field: {page_url, css} | None}."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.info("OPENAI_API_KEY not set — skipping LLM recipe call")
        return {}
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed — skipping LLM recipe call")
        return {}

    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    user_content = "Fields to find: " + ", ".join(missing_fields) + "\n\n"
    for url, html in pages_html.items():
        user_content += f"=== page_url: {url} ===\n{html}\n\n"

    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        return json.loads(raw).get("selectors", {})
    except json.JSONDecodeError:
        logger.error("LLM returned non-JSON: %s", raw[:200])
        return {}


# ---------------------------------------------------------------------------
# Build / rebuild orchestration
# ---------------------------------------------------------------------------

def build_recipe(
    domain: str,
    fetched_pages: dict[str, Any],
    missing_fields: list[str],
    content_hash: Optional[str] = None,
    prior: Optional[Recipe] = None,
) -> Recipe:
    """Call LLM once, return a Recipe. Caller is responsible for storing it."""
    pages_html = {
        url: _condense_html(_page_html(page))
        for url, page in fetched_pages.items()
    }
    pages_html = {u: h for u, h in pages_html.items() if h}

    if not pages_html:
        logger.warning("[%s] no HTML available; skipping LLM call", domain)
        status = "failed" if (prior and prior.status == "stale") else "active"
        return prior or Recipe(domain=domain, status=status)

    logger.info("[%s] LLM recipe build — missing=%s pages=%d",
                domain, missing_fields, len(pages_html))
    selectors = _llm_call(missing_fields, pages_html)

    field_selectors: dict[str, Optional[dict]] = {}
    negative_cache: list[str] = []
    for f in missing_fields:
        v = selectors.get(f)
        if v is None:
            negative_cache.append(f)
            field_selectors[f] = None
            continue
        cleaned = _sanitize_selector({
            "page_url": v.get("page_url"),
            "css": v.get("css"),
            "regex": v.get("regex"),
        })
        if cleaned is None:
            # LLM returned an unusable entry (no page_url, no css, no valid regex) →
            # treat as negative so we don't retry forever.
            negative_cache.append(f)
            field_selectors[f] = None
        else:
            field_selectors[f] = cleaned

    # Preserve any previously-good selectors for fields not in this round
    if prior:
        for f, sel in prior.field_selectors.items():
            field_selectors.setdefault(f, sel)
        for f in prior.negative_cache:
            if f not in field_selectors and f not in negative_cache:
                negative_cache.append(f)

    next_version = (prior.recipe_version + 1) if prior else 1
    return Recipe(
        domain=domain,
        field_selectors=field_selectors,
        negative_cache=negative_cache,
        status="active",
        recipe_version=next_version,
        last_hash=content_hash,
    )


def should_rebuild(
    recipe: Recipe,
    current_hash: Optional[str],
    still_missing: list[str],
) -> bool:
    """Decide whether to spend a (second) LLM call rebuilding a recipe."""
    if not still_missing:
        return False
    if recipe.status == "failed":
        return False
    # Content changed AND extraction degraded → site likely changed.
    if recipe.last_hash and current_hash and recipe.last_hash != current_hash:
        return True
    # Active recipe that's never been retried + missing fields → one shot.
    if recipe.status == "active":
        return True
    return False


def _enqueue_generalize(
    domain: str,
    recipe: "Recipe",
    confirmed_fields: dict,
    store: Optional["RecipeStore"] = None,
) -> None:
    """Best-effort: enqueue generalize_recipe_task for each freshly-extracted field.

    Non-blocking — any import/connection failure is silently logged.
    Only runs when BRAIN_ENABLED=true. Skips fields already in recipe.generalized_fields
    so repeated runs don't duplicate LLM calls.
    """
    try:
        from scraper.brain import is_enabled
        if not is_enabled():
            return
    except Exception:
        return

    try:
        from api.tasks import generalize_recipe_task
    except Exception as e:
        logger.debug("Celery tasks unavailable — skipping generalize enqueue: %s", e)
        return

    for field_name in confirmed_fields:
        if field_name in recipe.generalized_fields:
            logger.debug("[%s] %s already generalized — skipping", domain, field_name)
            continue
        sel = recipe.field_selectors.get(field_name)
        if not sel:
            continue
        success = False
        try:
            generalize_recipe_task.delay(domain, field_name, sel)
            logger.debug("[%s] enqueued generalize task for field=%s", domain, field_name)
            success = True
        except Exception as e:
            # Broker unavailable — run synchronously so data still reaches Supabase.
            logger.debug("[%s] broker unavailable (%s), running generalize inline for %s", domain, e, field_name)
            try:
                generalize_recipe_task.apply(args=(domain, field_name, sel))
                success = True
            except Exception as e2:
                logger.warning("[%s] inline generalize failed for %s: %s", domain, field_name, e2)
        if success and store is not None:
            recipe.generalized_fields.append(field_name)
            store.upsert(recipe)


def fill_missing(
    domain: str,
    fetched_pages: dict[str, Any],
    missing_fields: list[str],
    store: RecipeStore,
    content_hash: Optional[str] = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Top-level entry. Returns (filled_values, source_per_field).

    source_per_field maps each filled field to either 'recipe' or 'llm'.
    Phase 3 already knows which fields it filled via regex/jsonld; it only
    asks us for the rest.
    """
    if not missing_fields:
        return {}, {}

    recipe = store.get(domain)

    # 1. Try existing recipe first (no LLM cost)
    if recipe and recipe.status != "failed":
        # Skip fields the recipe already confirmed absent
        try_fields = [f for f in missing_fields if f not in recipe.negative_cache]
        if try_fields:
            from_recipe = apply_recipe(recipe, fetched_pages, try_fields)
            if from_recipe:
                logger.info("[%s] recipe hit: %s", domain, list(from_recipe.keys()))
            still_missing = [f for f in try_fields if f not in from_recipe]
        else:
            from_recipe = {}
            still_missing = []

        if not should_rebuild(recipe, content_hash, still_missing):
            return from_recipe, {f: "recipe" for f in from_recipe}

        # 2. Rebuild — promote state machine
        recipe.status = "stale" if recipe.status == "active" else "failed"
        store.upsert(recipe)
        if recipe.status == "failed":
            logger.warning("[%s] recipe FAILED after rebuild — needs human review", domain)
            return from_recipe, {f: "recipe" for f in from_recipe}

        rebuilt = build_recipe(
            domain, fetched_pages, still_missing,
            content_hash=content_hash, prior=recipe,
        )
        rebuilt.status = "stale"  # already rebuilt once; next run → failed, no more LLM calls
        store.upsert(rebuilt)
        from_rebuild = apply_recipe(rebuilt, fetched_pages, still_missing)
        if from_rebuild:
            _enqueue_generalize(domain, rebuilt, from_rebuild, store=store)
        combined = {**from_recipe, **from_rebuild}
        sources = {**{f: "recipe" for f in from_recipe},
                   **{f: "llm" for f in from_rebuild}}
        return combined, sources

    # 3. No recipe yet — first LLM build
    if recipe is None:
        recipe = build_recipe(
            domain, fetched_pages, missing_fields, content_hash=content_hash,
        )
        store.upsert(recipe)
        from_llm = apply_recipe(recipe, fetched_pages, missing_fields)
        if from_llm:
            _enqueue_generalize(domain, recipe, from_llm, store=store)
        return from_llm, {f: "llm" for f in from_llm}

    # 4. Recipe is failed → don't retry
    logger.info("[%s] skipping recipe (status=failed)", domain)
    return {}, {}
