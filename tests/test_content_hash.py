"""
Tests for scraper.content_hash and scraper.phase6_content_hash.

Pure-function tests for canonicalization + hashing, plus a fixture-driven
test against the real Phase 3 output.
"""

import json
from pathlib import Path

import pytest

from scraper.content_hash import (
    TRACKED_FIELDS,
    canonical_json,
    canonical_payload,
    compute_hash,
)


# ── Pure function: canonical_payload ───────────────────────────────────

class TestCanonicalPayload:
    def test_picks_only_tracked_fields(self):
        rec = {
            "name": "A",
            "address": "B",
            "phone": "C",
            "opening_hours": {"Mo": "1"},
            "website_url": "https://x",
            "target_city": "Berlin",
            "data_sources": ["x"],
            "extraction_status": "complete",
        }
        out = canonical_payload(rec)
        assert set(out.keys()) == set(TRACKED_FIELDS)

    def test_missing_becomes_none(self):
        out = canonical_payload({})
        assert out == {"name": None, "address": None, "phone": None, "opening_hours": None}

    def test_string_whitespace_normalized(self):
        out = canonical_payload({"name": "  Foo   Bar  "})
        assert out["name"] == "Foo Bar"

    def test_unicode_preserved(self):
        out = canonical_payload({"address": "Körtestraße 24"})
        assert out["address"] == "Körtestraße 24"

    def test_nested_dict_normalized(self):
        out = canonical_payload({"opening_hours": {"Mo-Fr": "  09:00-18:00  "}})
        assert out["opening_hours"] == {"Mo-Fr": "09:00-18:00"}


# ── Pure function: canonical_json ──────────────────────────────────────

class TestCanonicalJson:
    def test_keys_sorted(self):
        s = canonical_json({"b": 1, "a": 2})
        assert s == '{"a":2,"b":1}'

    def test_nested_keys_sorted(self):
        s = canonical_json({"opening_hours": {"Sa": "1", "Mo": "2"}})
        assert s == '{"opening_hours":{"Mo":"2","Sa":"1"}}'

    def test_no_whitespace(self):
        s = canonical_json({"a": "b", "c": "d"})
        assert " " not in s

    def test_unicode_not_escaped(self):
        s = canonical_json({"name": "Körtestraße"})
        assert "Körtestraße" in s


# ── Pure function: compute_hash ────────────────────────────────────────

class TestComputeHash:
    def test_is_hex_sha256(self):
        h = compute_hash({"name": "A"})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        rec = {"name": "A", "phone": "1", "address": "X", "opening_hours": {"Mo": "9-5"}}
        assert compute_hash(rec) == compute_hash(rec)

    def test_top_level_key_order_irrelevant(self):
        a = {"name": "A", "phone": "1"}
        b = {"phone": "1", "name": "A"}
        assert compute_hash(a) == compute_hash(b)

    def test_nested_key_order_irrelevant(self):
        a = {"opening_hours": {"Mo-Fr": "9-18", "Sa": "9-13"}}
        b = {"opening_hours": {"Sa": "9-13", "Mo-Fr": "9-18"}}
        assert compute_hash(a) == compute_hash(b)

    def test_whitespace_normalized(self):
        a = {"name": "  Foo  Bar  "}
        b = {"name": "Foo Bar"}
        assert compute_hash(a) == compute_hash(b)

    def test_unicode_stable(self):
        rec = {"address": "Körtestraße 24, 10967 Berlin"}
        h1 = compute_hash(rec)
        # round-trip through json to verify utf-8 stability
        rec2 = json.loads(json.dumps(rec, ensure_ascii=False))
        assert compute_hash(rec2) == h1

    def test_untracked_field_changes_ignored(self):
        base = {"name": "A", "phone": "1", "address": "X", "opening_hours": {"Mo": "9"}}
        a = dict(base, target_city="Berlin", data_sources=["x"], extraction_status="complete")
        b = dict(base, target_city="Hamburg", data_sources=["y"], extraction_status="partial")
        assert compute_hash(a) == compute_hash(b)

    def test_tracked_field_change_detected(self):
        a = {"name": "A", "phone": "1", "address": "X", "opening_hours": {"Mo": "9"}}
        b = {"name": "A", "phone": "2", "address": "X", "opening_hours": {"Mo": "9"}}
        assert compute_hash(a) != compute_hash(b)

    def test_missing_vs_none_identical(self):
        assert compute_hash({"phone": None}) == compute_hash({})

    def test_none_vs_empty_string_differ(self):
        assert compute_hash({"phone": None}) != compute_hash({"phone": ""})


# ── Diff bucket logic (phase6) ─────────────────────────────────────────

class TestDiff:
    def test_added_removed_changed_unchanged(self):
        from scraper.phase6_content_hash import diff_states

        prev = {
            "u1": {"hash": "h1"},
            "u2": {"hash": "h2"},
            "u3": {"hash": "h3"},
        }
        curr = {
            "u1": {"hash": "h1"},      # unchanged
            "u2": {"hash": "h2x"},     # changed
            "u4": {"hash": "h4"},      # added
            # u3 removed
        }
        d = diff_states(prev, curr)
        assert d["unchanged"] == ["u1"]
        assert d["changed"] == ["u2"]
        assert d["added"] == ["u4"]
        assert d["removed"] == ["u3"]


# ── Fixture-driven test against real phase 3 output ────────────────────

FIXTURE = Path(__file__).parent.parent / "output" / "test_phase3_extracted.json"


@pytest.mark.skipif(not FIXTURE.exists(), reason="phase 3 fixture missing")
class TestFixture:
    def test_every_complete_record_gets_hex_hash(self):
        records = json.loads(FIXTURE.read_text())
        complete = [r for r in records if r.get("extraction_status") != "skipped"]
        assert complete, "fixture has no non-skipped records"
        for r in complete:
            h = compute_hash(r)
            assert len(h) == 64
            assert all(c in "0123456789abcdef" for c in h)

    def test_two_runs_against_same_record_match(self):
        records = json.loads(FIXTURE.read_text())
        for r in records:
            assert compute_hash(r) == compute_hash(r)

    def test_golden_hashes_locked(self):
        """Regression locks — captured from first canonical run.
        If a hash changes, either the fixture was edited (expected) or
        canonicalization logic drifted (investigate)."""
        GOLDEN = {
            "https://ludwigwilde.buchhandlung.de/shop":
                "8fcf6fb4aed756950a9858c9e04fb5609bf1576dc6944d6525afd3706487ed67",
            "http://www.antiquariat-in-berlin.de":
                "b2b1723bff586b19afd4937700465db8f57f186aa8da167f0c26e24ded4c5198",
            "https://www.buchhandlung-walther-koenig.de/":
                "cac1ff29bfb6a092a1d00f730042ec4dcc5a18d1671ba60b59188232997ae328",
        }
        records = json.loads(FIXTURE.read_text())
        by_url = {r.get("website_url"): r for r in records}
        for url, want in GOLDEN.items():
            assert url in by_url, f"fixture missing {url}"
            assert compute_hash(by_url[url]) == want, f"hash drift for {url}"
