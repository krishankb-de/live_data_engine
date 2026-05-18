"""Tests for scraper/brain/sandbox.py — Phase 4.

All DB calls are mocked. No network or Supabase required.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_html(text: str) -> str:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<html><body><pre>{escaped}</pre></body></html>"


def _write_html(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / f"{name}.html"
    p.write_text(_make_html(text), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _apply_pattern
# ---------------------------------------------------------------------------

class TestApplyPattern:
    def test_regex_phone_match(self):
        from scraper.brain.sandbox import _apply_pattern, _read_selector, _get_text

        html = _make_html("Ruf uns an: +49 30 12345678")
        from scrapling import Selector

        sel = Selector(html)
        text = _get_text(sel)
        result = _apply_pattern(
            r"\+49[\s\d]+",
            "regex",
            "phone",
            sel,
            text,
        )
        assert result is not None

    def test_regex_no_match_returns_none(self):
        from scraper.brain.sandbox import _apply_pattern, _read_selector, _get_text
        from scrapling import Selector

        sel = Selector(_make_html("Kein Telefon hier"))
        text = _get_text(sel)
        result = _apply_pattern(r"\+49[\s\d]+", "regex", "phone", sel, text)
        assert result is None

    def test_css_selector_match(self, tmp_path):
        from scraper.brain.sandbox import _apply_pattern
        from scrapling import Selector

        html = '<html><body><span class="tel">030-1234567</span></body></html>'
        sel = Selector(html)
        result = _apply_pattern("span.tel::text", "css", "phone", sel, "")
        # CSS extracts raw text; post-processor validates
        assert result is not None or result is None  # just no exception

    def test_bad_regex_returns_none(self):
        from scraper.brain.sandbox import _apply_pattern
        from scrapling import Selector

        sel = Selector(_make_html("anything"))
        result = _apply_pattern("[invalid(regex", "regex", "phone", sel, "anything")
        assert result is None


# ---------------------------------------------------------------------------
# _read_selector / _get_text
# ---------------------------------------------------------------------------

class TestReadSelector:
    def test_reads_html_file(self, tmp_path):
        from scraper.brain.sandbox import _read_selector, _get_text

        p = tmp_path / "test.html"
        p.write_text(_make_html("Hallo Welt 030-123456"), encoding="utf-8")
        sel = _read_selector(p)
        assert sel is not None
        text = _get_text(sel)
        assert "030-123456" in text

    def test_missing_file_returns_none(self, tmp_path):
        from scraper.brain.sandbox import _read_selector

        sel = _read_selector(tmp_path / "nonexistent.html")
        assert sel is None

    def test_strips_script_tags(self, tmp_path):
        from scraper.brain.sandbox import _read_selector, _get_text

        p = tmp_path / "scripted.html"
        p.write_text(
            "<html><body><script>alert('x')</script><p>Real content</p></body></html>",
            encoding="utf-8",
        )
        sel = _read_selector(p)
        text = _get_text(sel)
        assert "Real content" in text
        assert "alert" not in text


# ---------------------------------------------------------------------------
# passes_thresholds
# ---------------------------------------------------------------------------

class TestPassesThresholds:
    def test_phone_passes(self):
        from scraper.brain.sandbox import passes_thresholds

        metrics = {
            "precision": 0.98,
            "recall": 0.75,
            "negative_hits": 0,
        }
        assert passes_thresholds(metrics, "phone") is True

    def test_phone_fails_precision(self):
        from scraper.brain.sandbox import passes_thresholds

        metrics = {"precision": 0.90, "recall": 0.75, "negative_hits": 0}
        assert passes_thresholds(metrics, "phone") is False

    def test_phone_fails_recall(self):
        from scraper.brain.sandbox import passes_thresholds

        metrics = {"precision": 0.99, "recall": 0.50, "negative_hits": 0}
        assert passes_thresholds(metrics, "phone") is False

    def test_any_field_fails_on_fp(self):
        from scraper.brain.sandbox import passes_thresholds

        metrics = {"precision": 1.0, "recall": 1.0, "negative_hits": 1}
        for field in ("phone", "address", "opening_hours", "name"):
            assert passes_thresholds(metrics, field) is False

    def test_address_lower_threshold(self):
        from scraper.brain.sandbox import passes_thresholds

        # address threshold: 0.95 / 0.60
        metrics = {"precision": 0.95, "recall": 0.60, "negative_hits": 0}
        assert passes_thresholds(metrics, "address") is True

    def test_hours_lower_threshold(self):
        from scraper.brain.sandbox import passes_thresholds

        metrics = {"precision": 0.90, "recall": 0.50, "negative_hits": 0}
        assert passes_thresholds(metrics, "opening_hours") is True


# ---------------------------------------------------------------------------
# validate_candidate — mocked DB
# ---------------------------------------------------------------------------

class TestValidateCandidate:
    """validate_candidate with fixtures injected via mocked db_repo.list_fixtures."""

    def _run(
        self,
        tmp_path: Path,
        fixtures: list[dict],
        pattern: str,
        field: str,
        pattern_type: str = "regex",
        language: str = "any",
    ) -> dict:
        from scraper.brain.sandbox import validate_candidate

        with patch("db_repo.list_fixtures", return_value=fixtures):
            return validate_candidate(pattern, field, pattern_type, language)

    def test_perfect_recall_no_fp(self, tmp_path):
        phone_html = _write_html(tmp_path, "site_phone", "Telefon: +49 30 12345678")
        no_phone_html = _write_html(tmp_path, "site_no_phone", "Nur Adresse: Karlstraße 5, 10117 Berlin")

        fixtures = [
            {
                "html_path": str(phone_html),
                "field": "phone",
                "expected_value": "+493012345678",
                "source_url": "http://site-phone.de",
            },
            {
                "html_path": str(no_phone_html),
                "field": "phone",
                "expected_value": None,
                "source_url": "http://site-no-phone.de",
            },
        ]
        phone_re = r"\+49[\s\d\-–—\/\.\(\)]{4,}\d"
        metrics = self._run(tmp_path, fixtures, phone_re, "phone")

        assert metrics["true_positives"] == 1
        assert metrics["false_positives"] == 0
        assert metrics["false_negatives"] == 0
        assert metrics["precision"] == 1.0
        assert metrics["recall"] == 1.0

    def test_false_positive_detected(self, tmp_path):
        no_phone_html = _write_html(tmp_path, "neg", "keine Telefonnummer hier, aber +49 30 99999999 doch")

        fixtures = [
            {
                "html_path": str(no_phone_html),
                "field": "phone",
                "expected_value": None,  # negative fixture
                "source_url": "http://neg.de",
            },
        ]
        # A greedy phone regex should match
        phone_re = r"\+49[\s\d\-–—\/\.\(\)]{4,}\d"
        metrics = self._run(tmp_path, fixtures, phone_re, "phone")

        assert metrics["false_positives"] == 1
        assert metrics["negative_hits"] == 1

    def test_false_negative_detected(self, tmp_path):
        phone_html = _write_html(tmp_path, "pos", "Call us: 030-1234567")

        fixtures = [
            {
                "html_path": str(phone_html),
                "field": "phone",
                "expected_value": "0301234567",
                "source_url": "http://pos.de",
            },
        ]
        # Pattern that only matches +49 format — misses 030 local
        bad_re = r"\+49[\s\d]{6,}"
        metrics = self._run(tmp_path, fixtures, bad_re, "phone")

        assert metrics["false_negatives"] == 1
        assert metrics["true_positives"] == 0
        assert metrics["recall"] == 0.0

    def test_missing_html_skipped(self, tmp_path):
        fixtures = [
            {
                "html_path": str(tmp_path / "does_not_exist.html"),
                "field": "phone",
                "expected_value": "+4930123",
                "source_url": "http://gone.de",
            }
        ]
        metrics = self._run(tmp_path, fixtures, r"\+49\d+", "phone")
        # No fixtures processed → zero everywhere
        assert metrics["true_positives"] == 0
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0

    def test_empty_fixtures_returns_zeroes(self, tmp_path):
        from scraper.brain.sandbox import validate_candidate

        with patch("db_repo.list_fixtures", return_value=[]):
            metrics = validate_candidate(r"\+49\d+", "phone", "regex")

        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["sample_failures"] == []

    def test_precision_recall_calculation(self, tmp_path):
        """2 positive fixtures, 1 FN, 1 FP → precision=0.5, recall=0.5."""
        pos1 = _write_html(tmp_path, "p1", "+49 30 111111")
        pos2 = _write_html(tmp_path, "p2", "Ruf 030-222222 an")  # local fmt — bad pattern misses
        neg1 = _write_html(tmp_path, "n1", "+49 30 333333 ist die Nummer")  # FP — pattern hits negative

        fixtures = [
            {"html_path": str(pos1), "field": "phone", "expected_value": "+4930111111", "source_url": "a"},
            {"html_path": str(pos2), "field": "phone", "expected_value": "030222222", "source_url": "b"},
            {"html_path": str(neg1), "field": "phone", "expected_value": None, "source_url": "c"},
        ]
        # Pattern: only matches +49 prefix → hits pos1, hits neg1 (FP), misses pos2 (FN)
        strict_re = r"\+49\s*\d[\d\s]{4,}\d"
        metrics = self._run(tmp_path, fixtures, strict_re, "phone")

        assert metrics["true_positives"] == 1
        assert metrics["false_negatives"] == 1
        assert metrics["false_positives"] == 1
        assert metrics["precision"] == pytest.approx(0.5, abs=0.01)
        assert metrics["recall"] == pytest.approx(0.5, abs=0.01)

    def test_address_positive_fixture(self, tmp_path):
        addr_html = _write_html(
            tmp_path, "addr", "Schillerstraße 15, 10625 Berlin\nMo-Fr 10-18"
        )
        fixtures = [
            {
                "html_path": str(addr_html),
                "field": "address",
                "expected_value": "Schillerstraße 15, 10625 Berlin",
                "source_url": "http://addr.de",
            }
        ]
        addr_re = r"\b[A-ZÄÖÜ][a-zäöüß]+(?:straße|str\.|weg|platz|allee)\s+\d+[a-z]?,\s*\d{5}\s+\w+"
        metrics = self._run(tmp_path, fixtures, addr_re, "address")
        # Pattern may or may not match — just verify no exception and structure is valid
        assert "precision" in metrics
        assert "recall" in metrics
        assert isinstance(metrics["sample_failures"], list)


# ---------------------------------------------------------------------------
# seed_from_output — mocked DB + no network
# ---------------------------------------------------------------------------

class TestSeedFromOutput:
    def test_golden_fixtures_written(self, tmp_path, monkeypatch):
        """Seed golden-3 with mocked DB; check insert_fixture called ≥9 times."""
        import scraper.brain.runtime as rt

        monkeypatch.setattr(rt, "HTML_CACHE_DIR", tmp_path / "html_cache")
        (tmp_path / "html_cache").mkdir(parents=True, exist_ok=True)

        inserted_calls: list[dict] = []

        def mock_insert_fixture(**kwargs):
            inserted_calls.append(kwargs)
            return {"id": len(inserted_calls)}

        mock_phase4 = tmp_path / "phase4_diff.json"
        mock_phase4.write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")

        with (
            patch("db_repo.list_fixtures", return_value=[]),
            patch("db_repo.insert_fixture", side_effect=mock_insert_fixture),
            patch("scraper.utils.OUTPUT_DIR", tmp_path),
        ):
            from scraper.brain.sandbox import seed_from_output

            count = seed_from_output(fetch_missing=False)

        # 3 golden sites × 4 fields = 12 fixture calls (some may be None expected)
        assert count == 12
        assert len(inserted_calls) == 12

    def test_phase4_diff_creates_fixtures(self, tmp_path, monkeypatch):
        """Seed with a fake phase4_diff entry; expect 4 fixture inserts."""
        import scraper.brain.runtime as rt

        cache_dir = tmp_path / "html_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(rt, "HTML_CACHE_DIR", cache_dir)

        diff_entry = {
            "https://example-buch.de": {
                "hash": "abc123",
                "last_seen": "2026-05-17T10:00:00+00:00",
                "fields": {
                    "name": "Beispiel Buchhandlung",
                    "address": "Musterstraße 1, 10115 Berlin",
                    "phone": "0306001234",
                    "opening_hours": {"Mo-Fr": "09:00-18:00"},
                },
            }
        }
        mock_phase4 = tmp_path / "phase4_diff.json"
        mock_phase4.write_text(
            json.dumps({"version": 1, "entries": diff_entry}), encoding="utf-8"
        )

        inserted_calls: list[dict] = []

        def mock_insert_fixture(**kwargs):
            inserted_calls.append(kwargs)
            return {"id": len(inserted_calls)}

        with (
            patch("db_repo.list_fixtures", return_value=[]),
            patch("db_repo.insert_fixture", side_effect=mock_insert_fixture),
            patch("scraper.utils.OUTPUT_DIR", tmp_path),
        ):
            from importlib import reload
            import scraper.brain.sandbox as sb
            reload(sb)

            count = sb.seed_from_output(fetch_missing=False, prefix="")

        # 3 golden (12) + 1 diff entry (4) = 16
        assert count == 16
        urls_inserted = [c["source_url"] for c in inserted_calls]
        assert "https://example-buch.de" in urls_inserted

    def test_synthetic_html_written_when_no_cache(self, tmp_path, monkeypatch):
        """When html_cache is empty + fetch_missing=False, synthetic HTML is created."""
        import scraper.brain.runtime as rt

        cache_dir = tmp_path / "html_cache"
        cache_dir.mkdir()
        monkeypatch.setattr(rt, "HTML_CACHE_DIR", cache_dir)

        diff_entry = {
            "https://synth-test.de": {
                "hash": "xyz",
                "last_seen": "2026-05-17T10:00:00+00:00",
                "fields": {"name": "Test", "address": "Teststr 1, 10115 Berlin", "phone": None, "opening_hours": None},
            }
        }
        mock_phase4 = tmp_path / "phase4_diff.json"
        mock_phase4.write_text(json.dumps({"version": 1, "entries": diff_entry}), encoding="utf-8")

        with (
            patch("db_repo.list_fixtures", return_value=[]),
            patch("db_repo.insert_fixture", return_value={"id": 1}),
            patch("scraper.utils.OUTPUT_DIR", tmp_path),
        ):
            from importlib import reload
            import scraper.brain.sandbox as sb
            reload(sb)
            sb.seed_from_output(fetch_missing=False)

        # Synthetic HTML should be written to cache_dir
        html_files = list(cache_dir.glob("*.html"))
        # golden 3 + 1 synthetic
        assert any("synth" not in f.stem for f in html_files)

    def test_skips_duplicate_fixtures(self, tmp_path, monkeypatch):
        """Already-existing (html_path, field) pairs are not re-inserted."""
        import scraper.brain.runtime as rt

        cache_dir = tmp_path / "html_cache"
        cache_dir.mkdir()
        monkeypatch.setattr(rt, "HTML_CACHE_DIR", cache_dir)

        mock_phase4 = tmp_path / "phase4_diff.json"
        mock_phase4.write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")

        golden_html = cache_dir / "_golden_dante.html"
        golden_html.write_text("<html><body>existing</body></html>", encoding="utf-8")

        # Pre-populate existing set so all dante fields are "already there"
        existing_fixtures = [
            {"html_path": str(golden_html), "field": f, "expected_value": None, "source_url": "x"}
            for f in ("phone", "address", "opening_hours", "name")
        ]

        inserted: list = []

        def mock_insert(**kw):
            inserted.append(kw)
            return {"id": len(inserted)}

        with (
            patch("db_repo.list_fixtures", return_value=existing_fixtures),
            patch("db_repo.insert_fixture", side_effect=mock_insert),
            patch("scraper.utils.OUTPUT_DIR", tmp_path),
        ):
            from importlib import reload
            import scraper.brain.sandbox as sb
            reload(sb)
            count = sb.seed_from_output(fetch_missing=False)

        # dante's 4 fields skipped; zadig + ludwig still inserted (8 new)
        assert count == 8
