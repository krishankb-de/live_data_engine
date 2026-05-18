"""End-to-end resilience tests — confirm the pipeline survives unreachable
hosts and malformed HTML without crashing."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from scraper import phase4_diff
from scraper.phase2_site_map import run as run_phase2
from scraper.phase3_extract import extract_site
from scraper.phase3_extract import run as run_phase3

from tests.conftest import MOCK_SITE_URL, ROOT, seed_phase1

pytestmark = pytest.mark.e2e


def _output(prefix: str, suffix: str) -> Path:
    return ROOT / "output" / f"{prefix}{suffix}"


def _free_port() -> int:
    """Return a port that is, at this instant, unbound — so a fetch against
    it should fail fast with ConnectionRefused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ─────────────────────────────────────────────────────────────────────
# Case 12 — One unreachable site does not kill the pipeline
# ─────────────────────────────────────────────────────────────────────

def test_unreachable_site_marks_failed_but_others_complete(
    vite_mock_site, e2e_prefix,
):
    dead_url = f"http://127.0.0.1:{_free_port()}"
    listings = [
        {
            "name": "Attorneyster Buchhandlung",
            "gelbeseiten_url": "",
            "gs_uuid": "e2e-good",
            "website_url": f"{MOCK_SITE_URL}/site",
            "is_verifiable": True,
            "target_city": "Berlin",
        },
        {
            "name": "Ghost Shop",
            "gelbeseiten_url": "",
            "gs_uuid": "e2e-dead",
            "website_url": dead_url,
            "is_verifiable": True,
            "target_city": "Berlin",
        },
    ]

    seed_phase1(e2e_prefix, listings)
    run_phase2(prefix=e2e_prefix)
    records = run_phase3(prefix=e2e_prefix)

    by_name = {r["name"]: r for r in records}
    assert by_name["Attorneyster Buchhandlung"]["extraction_status"] == "complete"
    assert by_name["Ghost Shop"]["extraction_status"] in ("failed", "partial", "skipped")


# ─────────────────────────────────────────────────────────────────────
# Case 13 — Malformed HTML must not raise
# ─────────────────────────────────────────────────────────────────────

def test_malformed_html_does_not_crash_parsers(vite_mock_site, e2e_prefix):
    url = f"{MOCK_SITE_URL}/fixtures/garbled.html"
    entry = {
        "name": "Garbled Shop",
        "gelbeseiten_url": "",
        "gs_uuid": "e2e-garbled",
        "website_url": url,
        "target_city": "New York",
        "pages": {},
    }

    cache = phase4_diff.load_cache(e2e_prefix)
    # The contract: no exception escapes, regardless of status.
    record = extract_site(entry, cache=cache)
    assert record["extraction_status"] in ("complete", "partial", "failed")
    assert "website_url" in record
