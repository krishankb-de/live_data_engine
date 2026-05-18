"""
E2E Supabase DB validation tests.

Validates that the pipeline correctly persists data to Supabase:
  E. Full pipeline run → listing + observations + versions land in DB
  F. Version old_value / new_value correctness + reference integrity
  G. Brain pattern reinforcement counter bumped on success (if brain fires)

All tests require --online flag and valid SUPABASE_URL / SUPABASE_SECRET_KEY.

Run:  pytest tests/test_e2e_db_supabase.py --online -v -s
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.online

VITE_SITE_URL = "http://localhost:5174/"
_TS = int(time.time())


# ── env + client helpers ──────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _load_env():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")


def _delete_by_gs_id(gs_id: str) -> None:
    from scraper.supabase_client import get_client
    get_client().table("listings").delete().eq("gs_listing_id", gs_id).execute()


def _delete_versions_for_listing(listing_id: int) -> None:
    from scraper.supabase_client import get_client
    get_client().table("versions").delete().eq("listing_id", listing_id).execute()


def _delete_observations_for_listing(listing_id: int) -> None:
    from scraper.supabase_client import get_client
    get_client().table("field_observations").delete().eq("listing_id", listing_id).execute()


def _seed_phase1(prefix: str, listings: list) -> None:
    path = ROOT / "output" / f"{prefix}phase1_listings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(listings, ensure_ascii=False, indent=2))


def _run_phases_23(prefix: str) -> list:
    from scraper.phase2_site_map import run as run_phase2
    from scraper.phase3_extract import run as run_phase3
    run_phase2(prefix=prefix)
    return run_phase3(prefix=prefix)


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    import socket
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def vite_running():
    """Skip the whole module if Vite dev server isn't available."""
    if not _wait_for_port("localhost", 5174, timeout=3.0):
        pytest.skip(
            "Vite dev server not running at localhost:5174 — "
            "start with: cd mock-source-site && npm run dev"
        )


# ── Test E: Full pipeline → listing + observations in DB ─────────────────────

class TestPipelineToDB:
    """Runs the pipeline against the Vite app and verifies Supabase rows."""

    _gs_id = f"e2e-db-{_TS}"

    @pytest.fixture(autouse=True)
    def _setup_cleanup(self, vite_running):
        import db_repo
        row = db_repo.upsert_listing({
            "gs_listing_id": self._gs_id,
            "name": "Attorneyster DB Test",
            "website_url": VITE_SITE_URL,
            "is_paid": False,
            "is_verifiable": True,
            "target_city": "Berlin",
        })
        listing_id = row.get("id") or db_repo.get_listing_by_gs_id(self._gs_id)["id"]
        type(self)._listing_id = listing_id

        prefix = f"dbtest_{uuid.uuid4().hex[:8]}_"
        type(self)._prefix = prefix
        _seed_phase1(prefix, [{
            "name": "Attorneyster DB Test",
            "gelbeseiten_url": "",
            "gs_uuid": self._gs_id,
            "website_url": VITE_SITE_URL,
            "is_verifiable": True,
            "target_city": "Berlin",
        }])
        yield
        _delete_versions_for_listing(listing_id)
        _delete_observations_for_listing(listing_id)
        _delete_by_gs_id(self._gs_id)

    def test_listing_exists_in_supabase_after_upsert(self):
        import db_repo
        row = db_repo.get_listing_by_gs_id(self._gs_id)
        assert row is not None, f"Listing {self._gs_id!r} not found in Supabase"
        assert row["website_url"] == VITE_SITE_URL

    def test_pipeline_produces_extracted_record(self):
        records = _run_phases_23(self._prefix)
        assert records, "Phase 3 produced no records"
        rec = records[0]
        type(self)._p3_record = rec
        assert rec.get("extraction_status") in ("complete", "partial"), rec

    def test_sync_creates_field_observations(self):
        from main import _sync_extracted_to_db
        import db_repo

        records = _run_phases_23(self._prefix)
        _sync_extracted_to_db(records)

        obs = db_repo.latest_observations(self._listing_id)
        assert obs, f"No observations for listing {self._listing_id}"
        fields_observed = {o["field"] for o in obs}
        # At least phone or address must be observed
        assert fields_observed & {"phone", "address", "name"}, (
            f"Expected at least one of phone/address/name; got {fields_observed}"
        )

    def test_sync_creates_version_for_new_field(self):
        """If any field changed from old_value (None for brand-new listing), a version is created."""
        from main import _sync_extracted_to_db
        import db_repo

        records = _run_phases_23(self._prefix)
        _sync_extracted_to_db(records)

        versions = db_repo.list_versions_for_listing(self._listing_id)
        assert versions, f"No versions created for listing {self._listing_id}"

    def test_field_sources_logged_in_phase3_output(self):
        records = _run_phases_23(self._prefix)
        rec = records[0]
        fs = rec.get("field_sources", {})
        print(f"\nEXEC PATH: {json.dumps(fs, indent=2)}")
        assert fs, f"field_sources is empty: {rec}"


# ── Test F: Version old_value / new_value + reference integrity ───────────────

class TestVersionReferenceIntegrity:
    """Verifies versions have correct old/new values and point to the right listing."""

    _gs_id = f"e2e-ver-{_TS}"

    @pytest.fixture(autouse=True)
    def _setup_cleanup(self, vite_running):
        import db_repo
        # Seed listing with a known old phone so we can assert old_value
        row = db_repo.upsert_listing({
            "gs_listing_id": self._gs_id,
            "name": "Attorneyster Ver Test",
            "website_url": VITE_SITE_URL,
            "phone": "+49 30 OLD-PHONE",
            "is_paid": False,
            "is_verifiable": True,
            "target_city": "Berlin",
        })
        listing_id = row.get("id") or db_repo.get_listing_by_gs_id(self._gs_id)["id"]
        type(self)._listing_id = listing_id

        prefix = f"ver_{uuid.uuid4().hex[:8]}_"
        type(self)._prefix = prefix
        _seed_phase1(prefix, [{
            "name": "Attorneyster Ver Test",
            "gelbeseiten_url": "",
            "gs_uuid": self._gs_id,
            "website_url": VITE_SITE_URL,
            "is_verifiable": True,
            "target_city": "Berlin",
        }])
        yield
        _delete_versions_for_listing(listing_id)
        _delete_observations_for_listing(listing_id)
        _delete_by_gs_id(self._gs_id)

    def test_version_listing_id_matches_parent(self):
        from main import _sync_extracted_to_db
        import db_repo

        records = _run_phases_23(self._prefix)
        _sync_extracted_to_db(records)

        versions = db_repo.list_versions_for_listing(self._listing_id)
        for v in versions:
            assert v["listing_id"] == self._listing_id, (
                f"Version {v['id']} has listing_id={v['listing_id']!r}, "
                f"expected {self._listing_id!r}"
            )

    def test_version_new_value_matches_extracted_field(self):
        from main import _sync_extracted_to_db
        import db_repo

        records = _run_phases_23(self._prefix)
        rec = records[0]
        _sync_extracted_to_db(records)

        versions = db_repo.list_versions_for_listing(self._listing_id)
        ver_fields = {v["field"]: v for v in versions}

        for field in ("phone", "address", "name", "opening_hours"):
            extracted = rec.get(field)
            if extracted is None:
                continue
            if isinstance(extracted, dict):
                extracted = json.dumps(extracted, ensure_ascii=False, sort_keys=True)
            if field in ver_fields:
                assert ver_fields[field]["new_value"] == str(extracted), (
                    f"Version new_value mismatch for '{field}': "
                    f"{ver_fields[field]['new_value']!r} != {str(extracted)!r}"
                )

    def test_version_decision_matches_confidence_thresholds(self):
        """
        Confidence mapping in _sync_extracted_to_db:
          jsonld/recipe → 0.90 → auto_applied  (>= 0.85)
          regex/brain   → 0.75 → needs_review  (0.50–0.84)
        Assert each version's decision is consistent with its source.
        """
        from main import _sync_extracted_to_db
        import db_repo

        records = _run_phases_23(self._prefix)
        rec = records[0]
        fs = rec.get("field_sources", {})
        _sync_extracted_to_db(records)

        versions = db_repo.list_versions_for_listing(self._listing_id)
        for v in versions:
            field_source = fs.get(v["field"], "")
            if field_source in ("jsonld", "recipe"):
                assert v["decision"] == "auto_applied", (
                    f"Field '{v['field']}' (source={field_source!r}, conf=0.90) "
                    f"expected auto_applied, got {v['decision']!r}"
                )
            elif field_source in ("regex", "brain"):
                assert v["decision"] == "needs_review", (
                    f"Field '{v['field']}' (source={field_source!r}, conf=0.75) "
                    f"expected needs_review, got {v['decision']!r}"
                )


# ── Test G: Brain pattern reinforcement ───────────────────────────────────────

class TestBrainReinforcement:
    """If the brain fires for any field, its success_count must increment."""

    _gs_id = f"e2e-brain-{_TS}"

    @pytest.fixture(autouse=True)
    def _setup_cleanup(self, vite_running):
        import db_repo
        row = db_repo.upsert_listing({
            "gs_listing_id": self._gs_id,
            "name": "Attorneyster Brain Test",
            "website_url": VITE_SITE_URL,
            "is_paid": False,
            "is_verifiable": True,
            "target_city": "Berlin",
        })
        listing_id = row.get("id") or db_repo.get_listing_by_gs_id(self._gs_id)["id"]
        type(self)._listing_id = listing_id

        prefix = f"brain_{uuid.uuid4().hex[:8]}_"
        type(self)._prefix = prefix
        _seed_phase1(prefix, [{
            "name": "Attorneyster Brain Test",
            "gelbeseiten_url": "",
            "gs_uuid": self._gs_id,
            "website_url": VITE_SITE_URL,
            "is_verifiable": True,
            "target_city": "Berlin",
        }])
        yield
        _delete_versions_for_listing(listing_id)
        _delete_observations_for_listing(listing_id)
        _delete_by_gs_id(self._gs_id)

    def test_brain_pattern_success_count_incremented_when_used(self):
        """
        If brain fires for any field, the pattern's success_count must be > 0
        after the run (brain.runtime bumps on successful match).
        Brain may not fire if regex/jsonld already satisfies all fields — that
        is acceptable; the test is skipped rather than failed in that case.
        """
        import db_repo

        records = _run_phases_23(self._prefix)
        rec = records[0]
        fs = rec.get("field_sources", {})
        fp = rec.get("field_pattern_ids", {})
        print(f"\nBRAIN field_sources: {fs}")
        print(f"BRAIN field_pattern_ids: {fp}")

        brain_fields = [f for f, src in fs.items() if src == "brain"]
        if not brain_fields:
            pytest.skip("Brain did not fire for any field — all fields satisfied by regex/jsonld")

        for field in brain_fields:
            pat_id = fp.get(field)
            if pat_id is None:
                continue
            pattern = db_repo.get_pattern(pat_id)
            assert pattern is not None, f"Pattern {pat_id} not found in DB"
            assert int(pattern.get("success_count", 0)) > 0, (
                f"Pattern {pat_id} success_count not bumped after brain hit"
            )
