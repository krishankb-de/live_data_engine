"""Phase 4 — Supabase integration tests.

All tests are marked `online` and require --online flag + real Supabase creds.

Run:  pytest tests/test_supabase_integration.py --online

Covers:
  - connectivity smoke-test
  - decision_from_confidence boundary checks (no network)
  - listing CRUD (upsert idempotency, list, fetch)
  - field_observations insert + latest-per-field dedup
  - versions insert, get, list, accept, reject
  - batch lifecycle
  - global_patterns insert + bump_success / bump_failure
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MOCK_SITE_URL = "http://localhost:5174/"
_TS = int(time.time())


# ---------------------------------------------------------------------------
# Env loading fixture (all online classes share this)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _load_env():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")


# ---------------------------------------------------------------------------
# Cleanup helper — raw client delete so we don't pollute real data
# ---------------------------------------------------------------------------

def _delete_listing_by_gs_id(gs_id: str) -> None:
    from scraper.supabase_client import get_client
    get_client().table("listings").delete().eq("gs_listing_id", gs_id).execute()


def _delete_pattern(pattern_id: int) -> None:
    from scraper.supabase_client import get_client
    get_client().table("global_patterns").delete().eq("id", pattern_id).execute()


def _delete_batch(batch_id: int) -> None:
    from scraper.supabase_client import get_client
    get_client().table("batches").delete().eq("id", batch_id).execute()


def _delete_version(version_id: int) -> None:
    from scraper.supabase_client import get_client
    get_client().table("versions").delete().eq("id", version_id).execute()


def _delete_observation(obs_id: int) -> None:
    from scraper.supabase_client import get_client
    get_client().table("field_observations").delete().eq("id", obs_id).execute()


# ---------------------------------------------------------------------------
# Decision boundary — pure logic, no network needed
# ---------------------------------------------------------------------------

class TestDecisionBoundaries:
    """Verify decision_from_confidence thresholds without any network calls."""

    def test_high_confidence_auto_applied(self):
        import db_repo
        assert db_repo.decision_from_confidence(0.90) == "auto_applied"

    def test_exact_high_boundary_auto_applied(self):
        import db_repo
        assert db_repo.decision_from_confidence(0.85) == "auto_applied"

    def test_just_below_high_boundary_needs_review(self):
        import db_repo
        assert db_repo.decision_from_confidence(0.849) == "needs_review"

    def test_mid_range_needs_review(self):
        import db_repo
        assert db_repo.decision_from_confidence(0.70) == "needs_review"

    def test_exact_low_boundary_needs_review(self):
        import db_repo
        assert db_repo.decision_from_confidence(0.50) == "needs_review"

    def test_just_below_low_boundary_discarded(self):
        import db_repo
        assert db_repo.decision_from_confidence(0.499) == "discarded"

    def test_zero_confidence_discarded(self):
        import db_repo
        assert db_repo.decision_from_confidence(0.0) == "discarded"

    def test_full_confidence_auto_applied(self):
        import db_repo
        assert db_repo.decision_from_confidence(1.0) == "auto_applied"


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

@pytest.mark.online
class TestConnectivity:
    def test_smoke_test_ok(self):
        from scraper.supabase_client import smoke_test
        result = smoke_test()
        assert result["ok"] is True, f"smoke_test failed: {result}"

    def test_smoke_test_stage_is_select(self):
        from scraper.supabase_client import smoke_test
        result = smoke_test()
        assert result.get("stage") == "select"

    def test_smoke_test_row_count_is_int(self):
        from scraper.supabase_client import smoke_test
        result = smoke_test()
        assert isinstance(result.get("row_count"), int)


# ---------------------------------------------------------------------------
# Listing CRUD
# ---------------------------------------------------------------------------

@pytest.mark.online
class TestListingCRUD:
    _gs_id = f"test_supabase_{_TS}_listing"

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        _delete_listing_by_gs_id(self._gs_id)

    def _make_record(self, **overrides) -> dict:
        base = {
            "gs_listing_id": self._gs_id,
            "name": "Integration Test Listing",
            "website_url": MOCK_SITE_URL,
            "phone": "+49 30 9999999",
            "address": "Teststr. 1, 10115 Berlin",
            "is_paid": False,
            "is_verifiable": True,
        }
        return {**base, **overrides}

    def test_upsert_returns_gs_listing_id(self):
        import db_repo
        result = db_repo.upsert_listing(self._make_record())
        assert result.get("gs_listing_id") == self._gs_id

    def test_upsert_creates_row_fetchable(self):
        import db_repo
        db_repo.upsert_listing(self._make_record())
        row = db_repo.get_listing_by_gs_id(self._gs_id)
        assert row is not None
        assert row["gs_listing_id"] == self._gs_id

    def test_upsert_website_url_persisted(self):
        import db_repo
        db_repo.upsert_listing(self._make_record())
        row = db_repo.get_listing_by_gs_id(self._gs_id)
        assert row["website_url"] == MOCK_SITE_URL

    def test_upsert_idempotent_no_duplicate(self):
        import db_repo
        db_repo.upsert_listing(self._make_record())
        db_repo.upsert_listing(self._make_record())
        rows, count = db_repo.list_listings(q="Integration Test Listing")
        matching = [r for r in rows if r["gs_listing_id"] == self._gs_id]
        assert len(matching) == 1, f"expected 1 row, got {len(matching)}"

    def test_upsert_updates_field_on_second_call(self):
        import db_repo
        db_repo.upsert_listing(self._make_record(phone="+49 30 1111111"))
        db_repo.upsert_listing(self._make_record(phone="+49 30 2222222"))
        row = db_repo.get_listing_by_gs_id(self._gs_id)
        assert row["phone"] == "+49 30 2222222"

    def test_get_listing_by_gs_id_missing_returns_none(self):
        import db_repo
        assert db_repo.get_listing_by_gs_id("nonexistent_gs_id_xyz_999") is None

    def test_list_listings_includes_upserted(self):
        import db_repo
        db_repo.upsert_listing(self._make_record())
        rows, total = db_repo.list_listings(q="Integration Test Listing")
        gs_ids = [r["gs_listing_id"] for r in rows]
        assert self._gs_id in gs_ids

    def test_get_listing_by_id(self):
        import db_repo
        row = db_repo.upsert_listing(self._make_record())
        listing_id = row.get("id")
        if listing_id is None:
            row = db_repo.get_listing_by_gs_id(self._gs_id)
            listing_id = row["id"]
        fetched = db_repo.get_listing(listing_id)
        assert fetched is not None
        assert fetched["id"] == listing_id


# ---------------------------------------------------------------------------
# Field observations
# ---------------------------------------------------------------------------

@pytest.mark.online
class TestObservations:
    _gs_id = f"test_supabase_{_TS}_obs"
    _listing_id: int | None = None
    _obs_ids: list[int]

    @pytest.fixture(autouse=True)
    def _setup_and_cleanup(self):
        import db_repo
        row = db_repo.upsert_listing({
            "gs_listing_id": self._gs_id,
            "name": "Obs Test Listing",
            "website_url": MOCK_SITE_URL,
            "is_paid": False,
            "is_verifiable": True,
        })
        listing_id = row.get("id") or db_repo.get_listing_by_gs_id(self._gs_id)["id"]
        type(self)._listing_id = listing_id
        type(self)._obs_ids = []
        yield
        for obs_id in type(self)._obs_ids:
            _delete_observation(obs_id)
        _delete_listing_by_gs_id(self._gs_id)

    def test_insert_observation_returns_id(self):
        import db_repo
        obs = db_repo.insert_observation(
            self._listing_id, "phone", "+49 30 1234567", "regex", confidence=0.80
        )
        assert "id" in obs
        type(self)._obs_ids.append(obs["id"])

    def test_insert_observation_field_stored(self):
        import db_repo
        obs = db_repo.insert_observation(
            self._listing_id, "phone", "+49 30 1234567", "jsonld", confidence=0.95
        )
        type(self)._obs_ids.append(obs["id"])
        assert obs["field"] == "phone"

    def test_insert_observation_source_stored(self):
        import db_repo
        obs = db_repo.insert_observation(
            self._listing_id, "address", "Teststr. 1", "regex", confidence=0.75
        )
        type(self)._obs_ids.append(obs["id"])
        assert obs["source"] == "regex"

    def test_latest_observations_dedup_by_field(self):
        import db_repo
        o1 = db_repo.insert_observation(
            self._listing_id, "phone", "+49 30 1111111", "regex", confidence=0.70
        )
        o2 = db_repo.insert_observation(
            self._listing_id, "phone", "+49 30 2222222", "jsonld", confidence=0.90
        )
        type(self)._obs_ids.extend([o1["id"], o2["id"]])
        latest = db_repo.latest_observations(self._listing_id)
        phone_obs = [o for o in latest if o["field"] == "phone"]
        assert len(phone_obs) == 1

    def test_latest_observations_returns_most_recent(self):
        import db_repo
        o1 = db_repo.insert_observation(
            self._listing_id, "phone", "+49 30 1111111", "regex", confidence=0.70
        )
        time.sleep(0.1)
        o2 = db_repo.insert_observation(
            self._listing_id, "phone", "+49 30 9999999", "jsonld", confidence=0.95
        )
        type(self)._obs_ids.extend([o1["id"], o2["id"]])
        latest = db_repo.latest_observations(self._listing_id)
        phone_obs = next(o for o in latest if o["field"] == "phone")
        assert phone_obs["value"] == "+49 30 9999999"

    def test_insert_observation_with_pattern_id(self):
        import db_repo
        from scraper.supabase_client import get_client
        pat = db_repo.insert_pattern(
            field="opening_hours", pattern_type="regex",
            pattern=r"Mo-Fr \d{2}:\d{2}", language="de",
            confidence_score=0.50, status="trial",
        )
        obs_id = None
        try:
            obs = db_repo.insert_observation(
                self._listing_id, "opening_hours", "Mo-Fr 09:00-18:00",
                "brain", confidence=0.88, pattern_id=pat["id"],
            )
            obs_id = obs["id"]
            type(self)._obs_ids.append(obs_id)
            assert obs.get("pattern_id") == pat["id"]
        finally:
            # Delete observation first (FK child) then pattern (FK parent)
            if obs_id is not None:
                _delete_observation(obs_id)
                type(self)._obs_ids.remove(obs_id)
            get_client().table("global_patterns").delete().eq("id", pat["id"]).execute()


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

@pytest.mark.online
class TestVersions:
    _gs_id = f"test_supabase_{_TS}_ver"
    _listing_id: int | None = None
    _version_ids: list[int]

    @pytest.fixture(autouse=True)
    def _setup_and_cleanup(self):
        import db_repo
        row = db_repo.upsert_listing({
            "gs_listing_id": self._gs_id,
            "name": "Version Test Listing",
            "website_url": MOCK_SITE_URL,
            "is_paid": False,
            "is_verifiable": True,
        })
        listing_id = row.get("id") or db_repo.get_listing_by_gs_id(self._gs_id)["id"]
        type(self)._listing_id = listing_id
        type(self)._version_ids = []
        yield
        for vid in type(self)._version_ids:
            _delete_version(vid)
        _delete_listing_by_gs_id(self._gs_id)

    def test_insert_version_auto_applied(self):
        import db_repo
        ver = db_repo.insert_version(
            self._listing_id, None, "phone",
            "+49 30 OLD", "+49 30 NEW", confidence=0.90
        )
        type(self)._version_ids.append(ver["id"])
        assert ver["decision"] == "auto_applied"

    def test_insert_version_needs_review(self):
        import db_repo
        ver = db_repo.insert_version(
            self._listing_id, None, "phone",
            "+49 30 OLD", "+49 30 MID", confidence=0.65
        )
        type(self)._version_ids.append(ver["id"])
        assert ver["decision"] == "needs_review"

    def test_insert_version_discarded(self):
        import db_repo
        ver = db_repo.insert_version(
            self._listing_id, None, "phone",
            "+49 30 OLD", "+49 30 LOW", confidence=0.30
        )
        type(self)._version_ids.append(ver["id"])
        assert ver["decision"] == "discarded"

    def test_get_version_roundtrip(self):
        import db_repo
        ver = db_repo.insert_version(
            self._listing_id, None, "address",
            "Old Addr", "New Addr", confidence=0.88
        )
        type(self)._version_ids.append(ver["id"])
        fetched = db_repo.get_version(ver["id"])
        assert fetched is not None
        assert fetched["field"] == "address"
        assert fetched["new_value"] == "New Addr"

    def test_list_versions_for_listing(self):
        import db_repo
        ver = db_repo.insert_version(
            self._listing_id, None, "opening_hours",
            None, "Mo-Fr 09:00-18:00", confidence=0.91
        )
        type(self)._version_ids.append(ver["id"])
        versions = db_repo.list_versions_for_listing(self._listing_id)
        ids = [v["id"] for v in versions]
        assert ver["id"] in ids

    def test_accept_version_changes_decision(self):
        import db_repo
        ver = db_repo.insert_version(
            self._listing_id, None, "phone",
            "+49 30 OLD", "+49 30 ACCEPTED", confidence=0.60
        )
        type(self)._version_ids.append(ver["id"])
        accepted = db_repo.accept_version(ver["id"], applied_by="test_suite")
        assert accepted["decision"] == "auto_applied"

    def test_reject_version_changes_decision(self):
        import db_repo
        ver = db_repo.insert_version(
            self._listing_id, None, "phone",
            "+49 30 OLD", "+49 30 REJECTED", confidence=0.55
        )
        type(self)._version_ids.append(ver["id"])
        rejected = db_repo.reject_version(ver["id"], reviewed_by="test_suite", reason="bad data")
        assert rejected["decision"] == "discarded"

    def test_get_version_missing_returns_none(self):
        import db_repo
        assert db_repo.get_version(999_999_999) is None


# ---------------------------------------------------------------------------
# Batch lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.online
class TestBatchLifecycle:
    _batch_ids: list[int]

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        type(self)._batch_ids = []
        yield
        for bid in type(self)._batch_ids:
            _delete_batch(bid)

    def test_create_batch_returns_id(self):
        import db_repo
        batch = db_repo.create_batch()
        type(self)._batch_ids.append(batch["id"])
        assert "id" in batch

    def test_create_batch_status_queued(self):
        import db_repo
        batch = db_repo.create_batch()
        type(self)._batch_ids.append(batch["id"])
        assert batch.get("status") == "queued"

    def test_get_batch_roundtrip(self):
        import db_repo
        batch = db_repo.create_batch()
        type(self)._batch_ids.append(batch["id"])
        fetched = db_repo.get_batch(batch["id"])
        assert fetched is not None
        assert fetched["id"] == batch["id"]

    def test_update_batch_status(self):
        import db_repo
        batch = db_repo.create_batch()
        type(self)._batch_ids.append(batch["id"])
        db_repo.update_batch(batch["id"], status="running")
        fetched = db_repo.get_batch(batch["id"])
        assert fetched["status"] == "running"

    def test_finalize_batch_done(self):
        import db_repo
        batch = db_repo.create_batch()
        type(self)._batch_ids.append(batch["id"])
        db_repo.finalize_batch(batch["id"], counts={"listings_processed": 5, "changes_proposed": 2})
        fetched = db_repo.get_batch(batch["id"])
        assert fetched["status"] == "done"

    def test_finalize_batch_custom_status(self):
        import db_repo
        batch = db_repo.create_batch()
        type(self)._batch_ids.append(batch["id"])
        db_repo.finalize_batch(batch["id"], counts={}, status="failed")
        fetched = db_repo.get_batch(batch["id"])
        assert fetched["status"] == "failed"

    def test_get_batch_missing_returns_none(self):
        import db_repo
        assert db_repo.get_batch(999_999_999) is None

    def test_list_batches_includes_created(self):
        import db_repo
        batch = db_repo.create_batch()
        type(self)._batch_ids.append(batch["id"])
        batches, total = db_repo.list_batches(limit=50)
        ids = [b["id"] for b in batches]
        assert batch["id"] in ids


# ---------------------------------------------------------------------------
# Global patterns
# ---------------------------------------------------------------------------

@pytest.mark.online
class TestPatternCRUD:
    _pattern_ids: list[int]

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        type(self)._pattern_ids = []
        yield
        for pid in type(self)._pattern_ids:
            _delete_pattern(pid)

    def _insert(self, **overrides) -> dict:
        import db_repo
        base = dict(
            field="phone",
            pattern_type="regex",
            pattern=r"\+49 ?\d{2,4} ?\d{3,}",
            language="de",
            confidence_score=0.50,
            status="trial",
        )
        pat = db_repo.insert_pattern(**{**base, **overrides})
        type(self)._pattern_ids.append(pat["id"])
        return pat

    def test_insert_pattern_returns_id(self):
        pat = self._insert()
        assert "id" in pat

    def test_insert_pattern_field_stored(self):
        pat = self._insert(field="address")
        assert pat["field"] == "address"

    def test_get_pattern_roundtrip(self):
        import db_repo
        pat = self._insert()
        fetched = db_repo.get_pattern(pat["id"])
        assert fetched is not None
        assert fetched["id"] == pat["id"]

    def test_get_pattern_missing_returns_none(self):
        import db_repo
        assert db_repo.get_pattern(999_999_999) is None

    def test_list_active_patterns_includes_trial(self):
        import db_repo
        pat = self._insert(status="trial", field="phone")
        active = db_repo.list_active_patterns("phone")
        ids = [p["id"] for p in active]
        assert pat["id"] in ids

    def test_list_active_patterns_excludes_retired(self):
        import db_repo
        pat = self._insert(status="retired", field="phone")
        active = db_repo.list_active_patterns("phone")
        ids = [p["id"] for p in active]
        assert pat["id"] not in ids

    def test_bump_pattern_success_increases_confidence(self):
        import db_repo
        pat = self._insert(confidence_score=0.50)
        db_repo.bump_pattern_success(pat["id"], delta=0.05)
        fetched = db_repo.get_pattern(pat["id"])
        assert float(fetched["confidence_score"]) > 0.50

    def test_bump_pattern_failure_decreases_confidence(self):
        import db_repo
        pat = self._insert(confidence_score=0.80)
        db_repo.bump_pattern_failure(pat["id"], delta=0.10)
        fetched = db_repo.get_pattern(pat["id"])
        assert float(fetched["confidence_score"]) < 0.80

    def test_set_pattern_status(self):
        import db_repo
        pat = self._insert(status="trial")
        db_repo.set_pattern_status(pat["id"], "active")
        fetched = db_repo.get_pattern(pat["id"])
        assert fetched["status"] == "active"
