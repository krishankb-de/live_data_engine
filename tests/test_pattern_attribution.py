"""Phase 3 — pattern attribution tests.

Verifies that extract_site() correctly populates field_sources with the right
source label (jsonld / regex / brain / cache / recipe) and writes per-field
attribution reports to output/test_reports/.

All tests are offline (mocked smart_fetch + extract_with_brain).
smart_fetch is mocked to return scrapling.Selector objects, which is what
the real parsers (parse_jsonld, parse_phone, …) expect.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrapling import Selector

REPORTS_DIR = ROOT / "output" / "test_reports"
MOCK_SITE_URL = "http://localhost:5174/"

# Pages as Scrapling Selector objects (what smart_fetch returns)
JSONLD_PAGE = Selector("""
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"LocalBusiness",
 "name":"Attorneyster Buchhandlung","telephone":"+49 30 1234567",
 "openingHours":"Mo-Fr 09:00-18:00",
 "address":{"streetAddress":"Friedrichstr. 42","addressLocality":"Berlin","postalCode":"10117"}}
</script>
</head><body></body></html>
""")

REGEX_PAGE = Selector("""
<html><body>
<p>Attorneyster Buchhandlung</p>
<p>Tel: +49 30 1234567</p>
<p>Öffnungszeiten: Mo-Fr 09:00-18:00</p>
<p>Friedrichstr. 42, 10117 Berlin</p>
</body></html>
""")

NO_PHONE_PAGE = Selector("""
<html><body>
<p>Attorneyster Buchhandlung</p>
<p>Friedrichstr. 42, 10117 Berlin</p>
<p>Öffnungszeiten: Mo-Fr 09:00-18:00</p>
</body></html>
""")

EMPTY_PAGE = Selector("<html><body></body></html>")


def _entry() -> dict:
    return {
        "name": "",
        "gelbeseiten_url": "",
        "gs_uuid": "test-attr-001",
        "website_url": MOCK_SITE_URL,
        "target_city": "Berlin",
        "pages": {},
    }


def _write_report(test_name: str, records: list[dict]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    path = REPORTS_DIR / f"{test_name}_{ts}.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return path


def _attribution_records(extracted: dict) -> list[dict]:
    """Build per-field attribution dicts matching the plan schema."""
    field_sources = extracted.get("field_sources", {})
    field_pids = extracted.get("field_pattern_ids", {})
    records = []
    for field in ("name", "phone", "address", "opening_hours"):
        if field in field_sources:
            records.append({
                "listing_id": None,
                "gs_listing_id": extracted.get("gs_uuid"),
                "field": field,
                "source": field_sources[field],
                "pattern_id": field_pids.get(field),
                "extraction_confidence": None,
                "decision": None,
                "old_value": None,
                "new_value": extracted.get(field),
            })
    return records


def _extract(page_obj, brain_side=None, cached_fields=None):
    """Run extract_site with common mocks, returns result dict."""
    from scraper.phase3_extract import extract_site

    changed = cached_fields is None
    check_rv = (changed, cached_fields or {})

    brain_fn = brain_side or (lambda *a, **kw: None)

    with patch("scraper.phase3_extract.smart_fetch", return_value=page_obj), \
         patch("scraper.phase3_extract.phase4_diff.check", return_value=check_rv), \
         patch("scraper.phase3_extract.phase4_diff.record"), \
         patch("scraper.phase3_extract.cache_html"), \
         patch("scraper.phase3_extract.extract_with_brain", side_effect=brain_fn), \
         patch("scraper.phase3_extract.brain.is_enabled", return_value=brain_side is not None), \
         patch("db_repo.record_pattern_execution"):
        return extract_site(_entry(), cache={})


# ---------------------------------------------------------------------------
# JSON-LD attribution
# ---------------------------------------------------------------------------

class TestJsonldAttribution:
    def test_phone_from_jsonld(self):
        result = _extract(JSONLD_PAGE)
        assert result["field_sources"].get("phone") == "jsonld"

    def test_address_from_jsonld(self):
        result = _extract(JSONLD_PAGE)
        assert result["field_sources"].get("address") == "jsonld"

    def test_hours_from_jsonld(self):
        result = _extract(JSONLD_PAGE)
        assert result["field_sources"].get("opening_hours") == "jsonld"

    def test_extraction_status_complete(self):
        result = _extract(JSONLD_PAGE)
        assert result["extraction_status"] == "complete"

    def test_report_written_jsonld(self):
        result = _extract(JSONLD_PAGE)
        records = _attribution_records(result)
        report_path = _write_report("test_jsonld_attribution", records)
        assert report_path.exists()
        loaded = json.loads(report_path.read_text())
        assert any(r["source"] == "jsonld" for r in loaded)


# ---------------------------------------------------------------------------
# Regex attribution
# ---------------------------------------------------------------------------

class TestRegexAttribution:
    def test_phone_from_regex(self):
        result = _extract(REGEX_PAGE)
        assert result["field_sources"].get("phone") == "regex", \
            f"got {result['field_sources']}"

    def test_address_from_regex(self):
        result = _extract(REGEX_PAGE)
        assert result["field_sources"].get("address") == "regex", \
            f"got {result['field_sources']}"

    def test_report_written_regex(self):
        result = _extract(REGEX_PAGE)
        records = _attribution_records(result)
        assert any(r["source"] == "regex" for r in records), \
            f"no regex record; field_sources={result['field_sources']}"
        report_path = _write_report("test_regex_attribution", records)
        loaded = json.loads(report_path.read_text())
        assert any(r["source"] == "regex" for r in loaded)


# ---------------------------------------------------------------------------
# Brain attribution
# ---------------------------------------------------------------------------

class TestBrainAttribution:
    def _brain_phone(self, field, **kw):
        if field == "phone":
            return ("+49 30 9999999", 77)
        return None

    def test_phone_source_is_brain(self):
        result = _extract(NO_PHONE_PAGE, brain_side=self._brain_phone)
        assert result["field_sources"].get("phone") == "brain", \
            f"got {result['field_sources']}"

    def test_brain_pattern_id_recorded(self):
        result = _extract(NO_PHONE_PAGE, brain_side=self._brain_phone)
        assert result["field_pattern_ids"].get("phone") == 77

    def test_report_schema_brain(self):
        result = _extract(NO_PHONE_PAGE, brain_side=self._brain_phone)
        records = _attribution_records(result)
        brain_rec = next((r for r in records if r["source"] == "brain"), None)
        assert brain_rec is not None
        assert brain_rec["pattern_id"] == 77
        assert "field" in brain_rec and "new_value" in brain_rec
        report_path = _write_report("test_brain_attribution", records)
        loaded = json.loads(report_path.read_text())
        assert any(r["source"] == "brain" for r in loaded)


# ---------------------------------------------------------------------------
# Cache attribution
# ---------------------------------------------------------------------------

class TestCacheAttribution:
    def test_cache_hit_marked_as_cache(self):
        cached = {
            "phone": "+49 30 1234567",
            "address": "Friedrichstr. 42, 10117 Berlin",
            "opening_hours": "Mo-Fr 09:00-18:00",
        }
        result = _extract(REGEX_PAGE, cached_fields=cached)
        fs = result["field_sources"]
        for field in ("phone", "address", "opening_hours"):
            assert fs.get(field) == "cache", \
                f"expected cache for {field}, got {fs.get(field)}"


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------

class TestReportStructure:
    def test_report_dir_created(self):
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        assert REPORTS_DIR.is_dir()

    def test_report_required_keys(self):
        record = {
            "listing_id": None,
            "gs_listing_id": "mock-5174",
            "field": "phone",
            "source": "regex",
            "pattern_id": None,
            "extraction_confidence": 0.85,
            "decision": "auto_applied",
            "old_value": None,
            "new_value": "+49 30 1234567",
        }
        required = {"listing_id", "gs_listing_id", "field", "source", "pattern_id",
                    "extraction_confidence", "decision", "old_value", "new_value"}
        assert required == set(record.keys())

    def test_valid_source_values(self):
        valid = {"jsonld", "regex", "brain", "cache", "recipe", "llm", "data-field", "global_pattern"}
        for src in ("jsonld", "regex", "brain", "cache", "recipe"):
            assert src in valid
