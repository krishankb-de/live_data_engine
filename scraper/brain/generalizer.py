"""Phase 5 — Generalizer: LLM abstraction of site-specific selectors into global patterns.

Public API:
    generalize(domain, field, field_selector) → GeneralizeResult | None
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# EUR/USD rate used for cost estimation (approximate).
_EUR_PER_USD = 0.92
# gpt-4o-mini pricing (USD per million tokens).
_PRICE_IN_PER_M = 0.15
_PRICE_OUT_PER_M = 0.60

_SYSTEM_PROMPT = """You are an expert at writing Python regex patterns and CSS selectors for web scraping.

Given a site-specific extractor (CSS selector or regex) that works on ONE bookstore website,
output a generalized pattern that works across MANY similar bookstore/business directory websites
without relying on site-specific class names, IDs, or domain strings.

Return ONLY valid JSON in this exact format:
{
  "pattern": "<generalized regex or CSS selector>",
  "pattern_type": "regex" | "css",
  "language": "de" | "en" | "fr" | "any",
  "rationale": "<one sentence explaining what structural feature this captures>"
}

Rules:
- For regex: use Python re syntax; avoid site-specific strings; anchor to semantic keywords
  (e.g. Tel, Telefon, Öffnungszeiten, Lundi) or structural markers (\\d{3,}, PLZ \\d{5}).
- For CSS: prefer semantic tags (<address>, <footer>, [itemtype], [itemprop]) over class names/IDs.
- Set language to "de", "en", or "fr" only if the pattern contains language-specific literal keywords;
  otherwise use "any".
- No prose, no markdown fences, no trailing commas. Valid JSON only.
"""


@dataclass
class GeneralizeResult:
    pattern: str
    pattern_type: str   # 'regex' | 'css'
    language: str       # 'de' | 'en' | 'fr' | 'any'
    rationale: str
    llm_cost_eur: float
    tokens_in: int
    tokens_out: int


def generalize(
    domain: str,
    field: str,
    field_selector: dict,
) -> Optional[GeneralizeResult]:
    """Call OpenAI to generalize a site-specific selector into a domain-agnostic pattern.

    Args:
        domain: origin domain (e.g. "danteconnection.de")
        field: field name ("phone" | "address" | "opening_hours" | "name")
        field_selector: dict from recipe, e.g. {"page_url": "...", "css": ".tel", "regex": None}

    Returns GeneralizeResult on success, None on any failure.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.info("OPENAI_API_KEY not set — skipping generalizer call")
        return None

    if OpenAI is None:  # pragma: no cover
        logger.warning("openai package not installed — skipping generalizer call")
        return None

    css = (field_selector.get("css") or "").strip()
    existing_regex = (field_selector.get("regex") or "").strip()
    page_url = (field_selector.get("page_url") or "").strip()

    user_content = (
        f"Site domain: {domain}\n"
        f"Field to extract: {field}\n"
        f"Site-specific page URL: {page_url or '(unknown)'}\n"
        f"Existing CSS selector: {css or '(none)'}\n"
        f"Existing regex: {existing_regex or '(none)'}\n\n"
        "Generalize this into a pattern that works across other similar bookstore websites."
    )

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as exc:
        logger.error("Generalizer LLM call failed for %s/%s: %s", domain, field, exc)
        return None

    raw = (resp.choices[0].message.content or "").strip()
    tokens_in = resp.usage.prompt_tokens if resp.usage else 0
    tokens_out = resp.usage.completion_tokens if resp.usage else 0
    cost_eur = (tokens_in * _PRICE_IN_PER_M + tokens_out * _PRICE_OUT_PER_M) / 1_000_000 * _EUR_PER_USD

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Generalizer returned non-JSON for %s/%s: %.200s", domain, field, raw)
        return None

    pattern = (parsed.get("pattern") or "").strip()
    pattern_type = parsed.get("pattern_type", "regex")
    language = parsed.get("language", "any")
    rationale = (parsed.get("rationale") or "").strip()

    if not pattern:
        logger.warning("Generalizer returned empty pattern for %s/%s", domain, field)
        return None

    if pattern_type not in ("regex", "css"):
        logger.warning("Generalizer returned invalid pattern_type=%r for %s/%s", pattern_type, domain, field)
        return None

    if language not in ("de", "en", "fr", "any"):
        language = "any"

    if pattern_type == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            logger.warning("Generalizer returned invalid regex for %s/%s: %r — %s", domain, field, pattern, e)
            return None

    logger.info(
        "Generalizer: %s/%s → type=%s lang=%s cost=€%.4f",
        domain, field, pattern_type, language, cost_eur,
    )
    return GeneralizeResult(
        pattern=pattern,
        pattern_type=pattern_type,
        language=language,
        rationale=rationale,
        llm_cost_eur=cost_eur,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
