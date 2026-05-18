"""Phase 6 — Repairer: LLM-based repair of decayed patterns.

Public API:
    repair(pattern_id, field, pattern, pattern_type, failing_snippets) → GeneralizeResult | None
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment,misc]

from scraper.brain.generalizer import (
    GeneralizeResult,
    _EUR_PER_USD,
    _PRICE_IN_PER_M,
    _PRICE_OUT_PER_M,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert at tightening Python regex patterns for web scraping.

Given a broken regex that produces false positives (matches things it should not match),
fix it so it stops matching the negative examples while still capturing the positive cases.

Return ONLY valid JSON in this exact format:
{
  "pattern": "<improved regex or CSS selector>",
  "pattern_type": "regex" | "css",
  "language": "de" | "en" | "fr" | "any",
  "rationale": "<one sentence explaining what you changed and why>"
}

Rules:
- Prefer semantic anchors (keywords, structural markers) over looser wildcards.
- Do NOT hardcode domain-specific class names or IDs.
- Set language to "de", "en", or "fr" only when the pattern contains language-specific literal keywords.
- No prose, no markdown fences. Valid JSON only.
"""


def repair(
    pattern_id: int,
    field: str,
    pattern: str,
    pattern_type: str,
    failing_snippets: list[str],
) -> Optional[GeneralizeResult]:
    """Ask OpenAI to tighten a decayed pattern.

    Args:
        pattern_id: ID of the pattern being repaired (for logging).
        field: field name ("phone" | "address" | "opening_hours" | "name")
        pattern: the current (broken) regex or CSS selector
        pattern_type: "regex" | "css"
        failing_snippets: up to 3 HTML/text snippets that the pattern wrongly matched

    Returns GeneralizeResult on success, None on any failure.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.info("OPENAI_API_KEY not set — skipping repair call for pattern %d", pattern_id)
        return None

    if OpenAI is None:  # pragma: no cover
        logger.warning("openai package not installed — skipping repair call for pattern %d", pattern_id)
        return None

    snippets_text = "\n".join(
        f"Negative {i+1}: {snip[:300]}" for i, snip in enumerate(failing_snippets)
    ) if failing_snippets else "(no failing snippets recorded)"

    user_content = (
        f"Field to extract: {field}\n"
        f"Pattern type: {pattern_type}\n"
        f"Current (broken) pattern:\n  {pattern}\n\n"
        f"Negative examples (snippets that should NOT match but do):\n{snippets_text}\n\n"
        "Tighten this pattern so it stops matching the negatives while still extracting the field correctly."
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
        logger.error("Repairer LLM call failed for pattern %d/%s: %s", pattern_id, field, exc)
        return None

    raw = (resp.choices[0].message.content or "").strip()
    tokens_in = resp.usage.prompt_tokens if resp.usage else 0
    tokens_out = resp.usage.completion_tokens if resp.usage else 0
    cost_eur = (tokens_in * _PRICE_IN_PER_M + tokens_out * _PRICE_OUT_PER_M) / 1_000_000 * _EUR_PER_USD

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("Repairer returned non-JSON for pattern %d: %.200s", pattern_id, raw)
        return None

    new_pattern = (parsed.get("pattern") or "").strip()
    new_pattern_type = parsed.get("pattern_type", pattern_type)
    language = parsed.get("language", "any")
    rationale = (parsed.get("rationale") or "").strip()

    if not new_pattern:
        logger.warning("Repairer returned empty pattern for pattern %d", pattern_id)
        return None

    if new_pattern_type not in ("regex", "css"):
        logger.warning("Repairer returned invalid pattern_type=%r for pattern %d", new_pattern_type, pattern_id)
        return None

    if language not in ("de", "en", "fr", "any"):
        language = "any"

    if new_pattern_type == "regex":
        try:
            re.compile(new_pattern)
        except re.error as e:
            logger.warning("Repairer returned invalid regex for pattern %d: %r — %s", pattern_id, new_pattern, e)
            return None

    logger.info(
        "Repairer: pattern %d/%s → type=%s lang=%s cost=€%.4f",
        pattern_id, field, new_pattern_type, language, cost_eur,
    )
    return GeneralizeResult(
        pattern=new_pattern,
        pattern_type=new_pattern_type,
        language=language,
        rationale=rationale,
        llm_cost_eur=cost_eur,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
