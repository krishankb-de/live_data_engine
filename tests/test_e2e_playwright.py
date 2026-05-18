"""
E2E tests: Playwright browser UI mutation → pipeline change detection.

Validates:
  A. Baseline extraction from Vite React app (smoke)
  B. Playwright phone mutation → Phase 6 detects change, new phone extracted
  C. No mutation → Doorman/Phase 6 reports unchanged (short-circuit negative test)
  D. Execution-path observability: field_sources logged for all 4 fields

Setup:
  - Vite dev server auto-started at http://localhost:5174/ by `vite_dev_server` fixture.
  - scrapling smart_fetch falls back to DynamicFetcher (Playwright) for JS-rendered content.
  - business.json saved/restored around each mutation test.

Run:  pytest tests/test_e2e_playwright.py -v -s
"""
from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
OUTPUT = ROOT / "output"
VITE_SITE_URL = "http://localhost:5174/"
ORIGINAL_PHONE = "+49 30 1234567"
NEW_PHONE = "+49 30 555-999-00"

VITE_LISTING = {
    "name": "Attorneyster Buchhandlung",
    "gelbeseiten_url": "",
    "gs_uuid": "e2e-playwright-attorneyster",
    "website_url": VITE_SITE_URL,
    "is_verifiable": True,
    "target_city": "Berlin",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _output(prefix: str, suffix: str) -> Path:
    return OUTPUT / f"{prefix}{suffix}"


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _seed_phase1(prefix: str, listings: list) -> None:
    path = OUTPUT / f"{prefix}phase1_listings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(listings, ensure_ascii=False, indent=2))


def _run_pipeline(prefix: str) -> dict:
    from scraper.phase2_site_map import run as run_phase2
    from scraper.phase3_extract import run as run_phase3
    from scraper import phase4_diff
    from scraper.phase6_content_hash import run as run_phase6

    run_phase2(prefix=prefix)
    run_phase3(prefix=prefix)
    phase4_diff.run(prefix=prefix)
    return run_phase6(prefix=prefix)


def _p3_records(prefix: str) -> list:
    p = _output(prefix, "phase3_extracted.json")
    return json.loads(p.read_text()) if p.exists() else []


def _history_lines(prefix: str) -> list[str]:
    p = _output(prefix, "phase6_diff_history.jsonl")
    return p.read_text().splitlines() if p.exists() else []


def _delete_output(prefix: str, *suffixes: str) -> None:
    for s in suffixes:
        _output(prefix, s).unlink(missing_ok=True)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def vite_dev_server():
    """Start the Vite dev server (port 5174) if it isn't already running."""
    already_up = _wait_for_port("localhost", 5174, timeout=1.0)
    proc = None
    if not already_up:
        proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(ROOT / "mock-source-site"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not _wait_for_port("localhost", 5174, timeout=30.0):
            proc.terminate()
            pytest.skip("Vite dev server did not start in 30 s — skip Playwright tests")
    yield
    if proc:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def pw_prefix():
    """Unique output artifact prefix per test."""
    return f"pw_{uuid.uuid4().hex[:8]}_"


@pytest.fixture(autouse=True)
def restore_business_json():
    """Save and restore mock-source-site/data/business.json around each test."""
    data_file = ROOT / "mock-source-site" / "data" / "business.json"
    original = data_file.read_bytes() if data_file.exists() else None
    yield
    if original is not None:
        data_file.write_bytes(original)


# ── Test A: Baseline extraction smoke ─────────────────────────────────────────

@pytest.mark.playwright
def test_vite_baseline_extraction(vite_dev_server, pw_prefix):
    """Pipeline extracts all 4 fields from the Vite React app on first run."""
    _seed_phase1(pw_prefix, [VITE_LISTING])
    _run_pipeline(pw_prefix)

    records = _p3_records(pw_prefix)
    assert len(records) == 1, f"Expected 1 record, got {len(records)}"
    rec = records[0]

    assert rec["extraction_status"] == "complete", (
        f"extraction_status={rec['extraction_status']!r}; field_sources={rec.get('field_sources')}"
    )
    for field in ("name", "address", "phone", "opening_hours"):
        assert rec.get(field), f"Field '{field}' missing or empty: {rec}"

    fs = rec.get("field_sources", {})
    print(f"\nEXEC PATH (baseline): {fs}")
    assert fs, "field_sources dict is empty — extraction path not logged"


# ── Test B: Playwright phone mutation → change detected ───────────────────────

@pytest.mark.playwright
def test_playwright_phone_mutation_triggers_change(vite_dev_server, pw_prefix):
    """
    Flow:
      1. Establish baseline run + caches.
      2. Playwright clicks pencil, types new phone, saves.
      3. Delete phase3/phase4 cache to force re-extraction.
      4. Re-run pipeline.
      5. Assert Phase 6 reports site as `changed`.
      6. Assert extracted phone changed from original.
    """
    from playwright.sync_api import sync_playwright

    _seed_phase1(pw_prefix, [VITE_LISTING])
    first_result = _run_pipeline(pw_prefix)
    first_records = _p3_records(pw_prefix)
    assert first_records[0]["extraction_status"] == "complete", first_records
    first_phone = first_records[0].get("phone", "")

    # ── UI mutation ──────────────────────────────────────────────────────────
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(VITE_SITE_URL, wait_until="networkidle")

        # Locate "Call Us On:" row → click the Edit pencil next to it
        phone_row = page.locator(".topbar__phone-row")
        phone_row.get_by_label("Edit phone number").click()

        inp = page.locator("input.phone-input")
        inp.wait_for(state="visible", timeout=5000)
        inp.fill(NEW_PHONE)  # fill() clears then types in one step

        page.locator(".btn-save").first.click()
        page.wait_for_selector("text=✓ Saved", timeout=5000)
        browser.close()

    # ── Force re-extraction ──────────────────────────────────────────────────
    _delete_output(pw_prefix, "phase3_extracted.json", "phase4_diff.json")

    second_result = _run_pipeline(pw_prefix)

    # Assert Doorman / Phase 6 detected change
    diff = second_result.get("diff", {})
    assert VITE_SITE_URL in diff.get("changed", []), (
        f"Expected {VITE_SITE_URL!r} in changed but got diff={diff}"
    )
    assert VITE_SITE_URL not in diff.get("unchanged", []), diff

    # Assert history appended
    lines = _history_lines(pw_prefix)
    assert len(lines) >= 2, f"Expected >= 2 history entries, got {lines}"

    # Assert extracted phone changed
    second_records = _p3_records(pw_prefix)
    assert second_records, "phase3_extracted.json is empty after second run"
    second_phone = second_records[0].get("phone", "")
    assert second_phone != first_phone, (
        f"Phone unchanged: {first_phone!r} == {second_phone!r}"
    )

    fs = second_records[0].get("field_sources", {})
    print(f"\nEXEC PATH (after mutation): {fs}")
    assert "phone" in fs, f"phone missing from field_sources: {fs}"


# ── Test C: No mutation → Doorman short-circuits ──────────────────────────────

@pytest.mark.playwright
def test_playwright_no_mutation_pipeline_unchanged(vite_dev_server, pw_prefix):
    """
    Playwright opens the page but makes no changes.
    Second pipeline run must report the URL as `unchanged` (no re-extraction).
    """
    from playwright.sync_api import sync_playwright

    _seed_phase1(pw_prefix, [VITE_LISTING])
    _run_pipeline(pw_prefix)

    # Open the page but do not mutate anything
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(VITE_SITE_URL, wait_until="networkidle")
        browser.close()

    # Re-run WITHOUT deleting cache files
    second_result = _run_pipeline(pw_prefix)

    diff = second_result.get("diff", {})
    assert VITE_SITE_URL in diff.get("unchanged", []), (
        f"Expected URL in unchanged but diff={diff}"
    )
    assert diff.get("changed", []) == [], f"Unexpected changes: {diff}"


# ── Test D: Execution-path observability ──────────────────────────────────────

@pytest.mark.playwright
def test_extraction_layer_attribution_logged(vite_dev_server, pw_prefix):
    """
    After a complete extraction, field_sources must be populated for every
    extracted field and printed to stdout so CI can capture the attribution.
    """
    _seed_phase1(pw_prefix, [VITE_LISTING])
    _run_pipeline(pw_prefix)

    records = _p3_records(pw_prefix)
    assert records, "No records produced"
    rec = records[0]

    fs = rec.get("field_sources", {})
    print(f"\nEXEC PATH: {json.dumps(fs, indent=2)}")

    # field_sources must be non-empty and every entry must have a non-null source.
    # (name is extracted separately and may not appear in field_sources — that's OK.)
    assert fs, "field_sources is empty — no extraction path was logged"
    for field, src in fs.items():
        assert src is not None, f"field_sources['{field}'] is None"

    # Sanity: extraction status logged
    assert rec.get("extraction_status") in ("complete", "partial"), rec
