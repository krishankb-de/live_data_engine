"""End-to-end tests for Phase 3's three extraction layers + recipe fallback,
served by static HTML routes under the mock Vite server.

Routes (added in mock-source-site/vite.config.ts):
    /fixtures/jsonld.html      → JSON-LD LocalBusiness, NYC
    /fixtures/regex.html       → no JSON-LD, German-style imprint + hours, NYC
    /fixtures/wrong-city.html  → Hamburg imprint, used with target_city=Berlin
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scraper import phase4_diff, recipe_builder
from scraper.phase3_extract import extract_site

from tests.conftest import MOCK_SITE_URL

pytestmark = pytest.mark.e2e


def _entry(url: str, name: str, city: str, *, prefill_name: bool = False) -> dict:
    """Skip Phase 2: hand-build the per-site entry extract_site() consumes.

    `prefill_name=False` clears `entry['name']` so the extractor must derive it
    from the page itself — only then does `field_sources['name']` get assigned
    (production code uses `_merge` which skips fields already populated)."""
    return {
        "name": name if prefill_name else "",
        "gelbeseiten_url": "",
        "gs_uuid": f"e2e-{name}",
        "website_url": url,
        "target_city": city,
        "pages": {},
    }


# ─────────────────────────────────────────────────────────────────────
# Case 8 — Layer A: JSON-LD path
# ─────────────────────────────────────────────────────────────────────

def test_layer_a_jsonld_populates_fields_from_schema(vite_mock_site, e2e_prefix):
    url = f"{MOCK_SITE_URL}/fixtures/jsonld.html"
    entry = _entry(url, "Buchhandlung am Hackeschen Markt", "Berlin")

    cache = phase4_diff.load_cache(e2e_prefix)
    record = extract_site(entry, cache=cache)

    assert record["extraction_status"] == "complete", record
    fs = record["field_sources"]
    assert fs.get("name") == "jsonld", fs
    assert fs.get("address") == "jsonld", fs
    assert fs.get("phone") == "jsonld", fs
    assert fs.get("opening_hours") == "jsonld", fs
    assert "+4930" in record["phone"] or "+49" in record["phone"]
    assert "Berlin" in record["address"]


# ─────────────────────────────────────────────────────────────────────
# Case 9 — Layer B: regex fallback when no JSON-LD
# ─────────────────────────────────────────────────────────────────────

def test_layer_b_regex_fallback_when_no_jsonld(vite_mock_site, e2e_prefix):
    url = f"{MOCK_SITE_URL}/fixtures/regex.html"
    entry = _entry(url, "Buchhandlung Kreuzberg", "Berlin")

    cache = phase4_diff.load_cache(e2e_prefix)
    record = extract_site(entry, cache=cache)

    fs = record["field_sources"]
    # Address + phone + hours all come from regex parsers.
    assert fs.get("address") == "regex", fs
    assert fs.get("phone") == "regex", fs
    assert fs.get("opening_hours") == "regex", fs
    # No JSON-LD source should appear anywhere.
    assert "jsonld" not in fs.values()
    assert record["address"] and "10961" in record["address"]
    assert record["phone"] and "87654321" in record["phone"]
    assert record["opening_hours"]


# ─────────────────────────────────────────────────────────────────────
# Case 10 — Recipe builder caches selectors across runs (no second LLM call)
# ─────────────────────────────────────────────────────────────────────

def test_recipe_builder_calls_llm_when_field_missing_and_reuses_on_rerun(
    vite_mock_site, e2e_prefix, tmp_path, monkeypatch,
):
    """Drive a page where regex/JSON-LD leave fields missing, so phase 3 falls
    back to the LLM recipe builder. Verify (1) the LLM is called once on the
    first pass, (2) the persisted recipe is reused on the second pass and the
    LLM is NOT called again."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    recipe_path = tmp_path / "recipes.json"
    monkeypatch.setattr(recipe_builder, "RECIPES_FILE", recipe_path)

    # The garbled fixture leaves multiple fields un-parseable by regex/JSON-LD.
    url = f"{MOCK_SITE_URL}/fixtures/garbled.html"
    entry = _entry(url, "Garbled Shop", "Berlin")

    llm = MagicMock(return_value={
        "phone": {"page_url": url, "regex": r"Telefon:\s*(\S+)"},
        "address": None,
        "opening_hours": None,
        "name": None,
    })
    monkeypatch.setattr(recipe_builder, "_llm_call", llm)

    store = recipe_builder.RecipeStore(path=recipe_path)
    cache = phase4_diff.load_cache(e2e_prefix)
    extract_site(entry, cache=cache, recipe_store=store)
    assert llm.call_count == 1, "expected one LLM call on first pass"
    assert recipe_path.exists()

    # Clear the Phase 4 page-level cache so extract_site re-fetches and re-runs
    # the recipe path; otherwise it would skip via the cache short-circuit and
    # the LLM call count wouldn't tell us anything either way.
    cache2 = {"version": cache.get("version", 1), "entries": {}}
    extract_site(entry, cache=cache2, recipe_store=store)
    assert llm.call_count == 1, "second pass must reuse persisted recipe, not call LLM again"


# ─────────────────────────────────────────────────────────────────────
# Case 11 — Address rejected when target_city differs
# ─────────────────────────────────────────────────────────────────────

def test_wrong_city_address_rejected(vite_mock_site, e2e_prefix):
    url = f"{MOCK_SITE_URL}/fixtures/wrong-city.html"
    entry = _entry(url, "Beispielhandlung", "Berlin")

    cache = phase4_diff.load_cache(e2e_prefix)
    record = extract_site(entry, cache=cache)

    assert record["address"] is None, record["address"]
    # Phone may still come through; status should be partial (not complete).
    assert record["extraction_status"] in ("partial", "failed"), record


# ─────────────────────────────────────────────────────────────────────
# Case 16 — Brain enabled records pattern executions
# ─────────────────────────────────────────────────────────────────────

FAKE_NAME_PATTERN = {
    "id": 999,
    "field": "name",
    "pattern_type": "regex",
    # Match the headline on the regex-fixture page.
    "pattern": r"(Brooklyn Books)",
    "language": "any",
    "confidence_score": 0.95,
    "status": "active",
}


def test_brain_enabled_records_pattern_execution_on_hit(
    vite_mock_site, e2e_prefix, monkeypatch,
):
    """When brain fires for a field, record_pattern_execution gets called."""
    monkeypatch.setenv("BRAIN_ENABLED", "true")

    # Make sure the regex parser doesn't preempt the brain by stubbing it out
    # for `name` only. parse_name_from_page would otherwise return a value
    # from the page <title>.
    monkeypatch.setattr(
        "scraper.phase3_extract.parse_name_from_page",
        lambda page: None,
    )

    # Wire a fake DB: brain reads patterns from db_repo.list_active_patterns
    # and writes executions back via db_repo.record_pattern_execution.
    record_calls = []

    def fake_record(**kw):
        record_calls.append(kw)

    with patch(
        "db_repo.list_active_patterns",
        side_effect=lambda field, lang: (
            [FAKE_NAME_PATTERN] if field == "name" else []
        ),
    ), patch("db_repo.record_pattern_execution", side_effect=fake_record):
        # Brain's pattern cache is process-wide — invalidate so the patch
        # actually takes effect.
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

        url = f"{MOCK_SITE_URL}/fixtures/regex.html"
        entry = _entry(url, "Brooklyn Books", "New York")
        cache = phase4_diff.load_cache(e2e_prefix)
        record = extract_site(entry, cache=cache)

    assert record["field_sources"].get("name") == "brain", record["field_sources"]
    assert record["field_pattern_ids"].get("name") == 999
    assert any(c.get("pattern_id") == 999 for c in record_calls), record_calls
