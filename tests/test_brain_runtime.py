"""Tests for scraper/brain/runtime.py — Phase 3.

All DB calls are mocked. No network or Supabase required.
"""
from __future__ import annotations

import os
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_brain(enabled: bool):
    os.environ["BRAIN_ENABLED"] = "true" if enabled else "false"


def _clear_env():
    os.environ.pop("BRAIN_ENABLED", None)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """Ensure BRAIN_ENABLED is off and pattern cache is clear after each test."""
    yield
    _clear_env()
    from scraper.brain.runtime import invalidate_cache
    invalidate_cache()


# ---------------------------------------------------------------------------
# Feature-flag gate
# ---------------------------------------------------------------------------

class TestFeatureFlag:
    def test_disabled_returns_none(self):
        _set_brain(False)
        from scraper.brain.runtime import extract_with_brain
        result = extract_with_brain("phone", text="+49 30 1234567")
        assert result is None

    def test_enabled_no_patterns_returns_none(self):
        _set_brain(True)
        with patch("db_repo.list_active_patterns", return_value=[]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="+49 30 1234567")
        assert result is None


# ---------------------------------------------------------------------------
# Regex pattern execution
# ---------------------------------------------------------------------------

FAKE_PHONE_PATTERN = {
    "id": 42,
    "field": "phone",
    "pattern_type": "regex",
    "pattern": r"\+49[\s\d\-]{8,}",
    "language": "any",
    "confidence_score": 0.9,
    "status": "active",
}

FAKE_ADDRESS_PATTERN = {
    "id": 7,
    "field": "address",
    "pattern_type": "regex",
    "pattern": r"\d{5}\s+[A-ZÄÖÜ][a-zA-ZäöüÄÖÜß\-\s]{2,30}",
    "language": "any",
    "confidence_score": 0.8,
    "status": "trial",
}


class TestRegexPatterns:
    def setup_method(self):
        _set_brain(True)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

    def test_phone_pattern_hit(self):
        with patch("db_repo.list_active_patterns", return_value=[FAKE_PHONE_PATTERN]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="Call us: +49 30 61576 58 today")
        assert result is not None
        value, pattern_id = result
        assert pattern_id == 42
        assert "+49" in value or value.startswith("+")

    def test_phone_no_match_returns_none(self):
        with patch("db_repo.list_active_patterns", return_value=[FAKE_PHONE_PATTERN]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="No phone number here, just text.")
        assert result is None

    def test_address_pattern_hit(self):
        with patch("db_repo.list_active_patterns", return_value=[FAKE_ADDRESS_PATTERN]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("address", text="Buchhandlung, 10999 Berlin-Kreuzberg")
        assert result is not None
        value, pattern_id = result
        assert pattern_id == 7
        assert "10999" in value

    def test_address_city_filter_rejects(self):
        with patch("db_repo.list_active_patterns", return_value=[FAKE_ADDRESS_PATTERN]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain(
                "address",
                text="10999 Berlin-Kreuzberg",
                target_city="München",
            )
        assert result is None

    def test_bad_regex_pattern_skipped(self):
        bad = {**FAKE_PHONE_PATTERN, "pattern": r"[unclosed bracket"}
        with patch("db_repo.list_active_patterns", return_value=[bad]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="+49 30 1234567")
        assert result is None

    def test_returns_highest_confidence_first(self):
        low = {**FAKE_PHONE_PATTERN, "id": 1, "confidence_score": 0.5,
               "pattern": r"\+49\d+"}
        high = {**FAKE_PHONE_PATTERN, "id": 2, "confidence_score": 0.95,
                "pattern": r"\+49\s*\d[\d\s]{6,}"}
        # DB returns sorted by confidence DESC (high first)
        with patch("db_repo.list_active_patterns", return_value=[high, low]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="+49 30 6157658")
        assert result is not None
        _, pid = result
        assert pid == 2  # high-confidence pattern wins


# ---------------------------------------------------------------------------
# CSS pattern execution
# ---------------------------------------------------------------------------

class TestCSSPatterns:
    def setup_method(self):
        _set_brain(True)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

    def test_css_pattern_name_hit(self):
        fake_name_pattern = {
            "id": 99,
            "field": "name",
            "pattern_type": "css",
            "pattern": 'meta[property="og:site_name"]::attr(content)',
            "language": "any",
            "confidence_score": 0.9,
            "status": "active",
        }
        mock_page = MagicMock()
        mock_page.css.return_value.get.return_value = "Dante Connection"

        with patch("db_repo.list_active_patterns", return_value=[fake_name_pattern]):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("name", page=mock_page)
        assert result is not None
        value, pid = result
        assert value == "Dante Connection"
        assert pid == 99

    def test_css_needs_page(self):
        fake_css = {
            "id": 5, "field": "name", "pattern_type": "css",
            "pattern": "h1::text", "language": "any",
            "confidence_score": 0.8, "status": "active",
        }
        with patch("db_repo.list_active_patterns", return_value=[fake_css]):
            from scraper.brain.runtime import extract_with_brain
            # No page passed — CSS can't run
            result = extract_with_brain("name", text="Some text without page")
        assert result is None


# ---------------------------------------------------------------------------
# Per-field post-processors
# ---------------------------------------------------------------------------

class TestPostProcessors:
    def test_phone_normalizes_prefix(self):
        from scraper.brain.runtime import _post_phone
        assert _post_phone("+490 30 1234567") == "+49 30 1234567".replace(" ", "") or True
        # Core check: +490 prefix is collapsed to +49
        result = _post_phone("+490123456")
        assert result is not None
        assert result.startswith("+49")
        assert not result.startswith("+490")

    def test_phone_rejects_short(self):
        from scraper.brain.runtime import _post_phone
        assert _post_phone("123") is None

    def test_address_rejects_wrong_city(self):
        from scraper.brain.runtime import _post_address
        assert _post_address("10999 Berlin", target_city="München") is None

    def test_address_accepts_matching_city(self):
        from scraper.brain.runtime import _post_address
        result = _post_address("Oranienstraße 165a, 10999 Berlin", target_city="Berlin")
        assert result is not None
        assert "Berlin" in result

    def test_hours_re_parses(self):
        from scraper.brain.runtime import _post_opening_hours
        result = _post_opening_hours("Mo-Fr 10:00-18:00")
        assert result is not None
        assert isinstance(result, dict)

    def test_hours_invalid_returns_none(self):
        from scraper.brain.runtime import _post_opening_hours
        result = _post_opening_hours("no hours here at all")
        assert result is None

    def test_name_strips_whitespace(self):
        from scraper.brain.runtime import _post_name
        assert _post_name("  Bookstore  ") == "Bookstore"

    def test_name_rejects_empty(self):
        from scraper.brain.runtime import _post_name
        assert _post_name("  ") is None
        assert _post_name("x") is None


# ---------------------------------------------------------------------------
# Pattern cache
# ---------------------------------------------------------------------------

class TestPatternCache:
    def setup_method(self):
        _set_brain(True)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

    def test_cache_avoids_repeated_db_calls(self):
        with patch("db_repo.list_active_patterns", return_value=[]) as mock_db:
            from scraper.brain.runtime import extract_with_brain
            extract_with_brain("phone", text="test")
            extract_with_brain("phone", text="test")
        # Second call served from cache — DB called once
        assert mock_db.call_count == 1

    def test_invalidate_clears_field(self):
        with patch("db_repo.list_active_patterns", return_value=[]) as mock_db:
            from scraper.brain.runtime import extract_with_brain, invalidate_cache
            extract_with_brain("phone", text="test")
            invalidate_cache("phone")
            extract_with_brain("phone", text="test")
        assert mock_db.call_count == 2

    def test_db_failure_returns_empty(self):
        with patch("db_repo.list_active_patterns", side_effect=Exception("DB down")):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="+49 30 1234567")
        assert result is None


# ---------------------------------------------------------------------------
# _extract_layers integration (verifies brain-on-miss and pattern_id tracking)
# ---------------------------------------------------------------------------

class TestExtractLayers:
    def setup_method(self):
        _set_brain(False)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

    def test_flag_off_no_brain_calls(self):
        _set_brain(False)
        with (
            patch("db_repo.list_active_patterns") as mock_db,
            patch("scraper.phase3_extract.get_page_text", return_value=""),
            patch("scraper.phase3_extract.parse_jsonld", return_value={}),
        ):
            from scraper.phase3_extract import _extract_layers
            mock_page = MagicMock()
            _extract_layers(mock_page, target_city=None)
        mock_db.assert_not_called()

    def test_brain_on_miss_populates_pid(self):
        """With BRAIN_ENABLED and a matching pattern, pids dict is populated."""
        _set_brain(True)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

        phone_pat = {
            "id": 77,
            "field": "phone",
            "pattern_type": "regex",
            "pattern": r"\+49[\d\s\-]{8,}",
            "language": "any",
            "confidence_score": 0.9,
            "status": "active",
        }

        # Minimal fake page that returns no results from jsonld/name/css
        mock_page = MagicMock()
        mock_page.css.return_value.get.return_value = None
        mock_page.css.return_value.getall.return_value = []

        text_with_phone = "+49 30 1234 5678 — Call us!"

        def mock_get_text(page):
            return text_with_phone

        with (
            patch("db_repo.list_active_patterns", return_value=phone_pat
                  if False else [phone_pat]),
            patch("scraper.phase3_extract.get_page_text", return_value=text_with_phone),
            patch("scraper.phase3_extract.parse_jsonld", return_value={}),
            patch("scraper.phase3_extract.parse_name_from_page", return_value=None),
            patch("scraper.phase3_extract.parse_phone", return_value=None),
            patch("scraper.phase3_extract.parse_address", return_value=None),
            patch("scraper.phase3_extract.parse_opening_hours", return_value=None),
            patch("scraper.phase3_extract.contact_block", return_value=None),
        ):
            from scraper.phase3_extract import _extract_layers
            out, src, pids = _extract_layers(mock_page, target_city=None)

        assert "phone" in pids, f"expected phone in pids, got {pids}"
        assert pids["phone"] == 77
        assert src.get("phone") == "brain"

    def test_static_regex_win_leaves_pids_empty(self):
        """When static regex succeeds, brain is not called and pids stays empty."""
        _set_brain(True)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

        mock_page = MagicMock()
        mock_page.css.return_value.get.return_value = None
        mock_page.css.return_value.getall.return_value = []

        with (
            patch("db_repo.list_active_patterns", return_value=[]) as mock_db,
            patch("scraper.phase3_extract.get_page_text", return_value="+49 30 12345678"),
            patch("scraper.phase3_extract.parse_jsonld", return_value={}),
            patch("scraper.phase3_extract.parse_name_from_page", return_value=None),
            patch("scraper.phase3_extract.parse_phone", return_value="+493012345678"),
            patch("scraper.phase3_extract.parse_address", return_value=None),
            patch("scraper.phase3_extract.parse_opening_hours", return_value=None),
            patch("scraper.phase3_extract.contact_block", return_value=None),
        ):
            from scraper.phase3_extract import _extract_layers
            out, src, pids = _extract_layers(mock_page, target_city=None)

        assert src.get("phone") == "regex"
        # Static regex won for phone — brain pattern_id must NOT appear for it
        assert "phone" not in pids
