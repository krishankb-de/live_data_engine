"""db_repo smoke tests — live Supabase. Requires env vars in .env.

Run: pytest tests/test_db_repo.py -v
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import db_repo


# Skip all if no Supabase creds
pytestmark = pytest.mark.skipif(
    not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SECRET_KEY"),
    reason="SUPABASE_URL / SUPABASE_SECRET_KEY not set",
)

_DUMMY_GS_ID = "test-db-repo-smoke-00000000"


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

class TestListings:
    def test_upsert_and_get(self):
        row = db_repo.upsert_listing(
            {
                "gs_listing_id": _DUMMY_GS_ID,
                "name": "Smoke Test Bookstore",
                "website_url": "https://example.com",
                "address": "Test Str. 1, 10115 Berlin",
                "phone": "+4930000000",
            }
        )
        assert row.get("id")
        listing_id = row["id"]

        fetched = db_repo.get_listing(listing_id)
        assert fetched is not None
        assert fetched["name"] == "Smoke Test Bookstore"

    def test_get_by_gs_id(self):
        row = db_repo.get_listing_by_gs_id(_DUMMY_GS_ID)
        assert row is not None
        assert row["gs_listing_id"] == _DUMMY_GS_ID

    def test_list_listings(self):
        rows, total = db_repo.list_listings(q="Smoke Test", limit=5)
        assert total >= 1
        assert any(r["gs_listing_id"] == _DUMMY_GS_ID for r in rows)

    def test_update_field(self):
        row = db_repo.get_listing_by_gs_id(_DUMMY_GS_ID)
        assert row
        db_repo.update_listing_field(row["id"], "phone", "+4930111111")
        updated = db_repo.get_listing(row["id"])
        assert updated["phone"] == "+4930111111"


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

class TestBatches:
    _batch_id: int | None = None

    def test_create(self):
        row = db_repo.create_batch()
        assert row.get("id")
        TestBatches._batch_id = row["id"]
        assert row["status"] == "queued"

    def test_get(self):
        row = db_repo.get_batch(TestBatches._batch_id)
        assert row is not None
        assert row["id"] == TestBatches._batch_id

    def test_update(self):
        db_repo.update_batch(TestBatches._batch_id, status="running")
        row = db_repo.get_batch(TestBatches._batch_id)
        assert row["status"] == "running"

    def test_finalize(self):
        db_repo.finalize_batch(
            TestBatches._batch_id,
            {"listings_processed": 5, "changes_proposed": 2},
            status="done",
        )
        row = db_repo.get_batch(TestBatches._batch_id)
        assert row["status"] == "done"
        assert row["listings_processed"] == 5

    def test_list(self):
        rows, total = db_repo.list_batches(limit=10)
        assert total >= 1
        ids = [r["id"] for r in rows]
        assert TestBatches._batch_id in ids


# ---------------------------------------------------------------------------
# Field observations
# ---------------------------------------------------------------------------

class TestObservations:
    def test_insert_and_latest(self):
        listing = db_repo.get_listing_by_gs_id(_DUMMY_GS_ID)
        assert listing
        lid = listing["id"]

        db_repo.insert_observation(
            listing_id=lid,
            field="phone",
            value="+4930999999",
            source="regex",
            confidence=0.9,
        )
        obs = db_repo.latest_observations(lid)
        fields = {o["field"] for o in obs}
        assert "phone" in fields


# ---------------------------------------------------------------------------
# Versions + accept/reject
# ---------------------------------------------------------------------------

class TestVersions:
    _ver_id: int | None = None

    def test_insert(self):
        listing = db_repo.get_listing_by_gs_id(_DUMMY_GS_ID)
        assert listing
        ver = db_repo.insert_version(
            listing_id=listing["id"],
            batch_id=None,
            field="phone",
            old_value="+4930111111",
            new_value="+4930222222",
            confidence=0.6,   # → needs_review
        )
        assert ver.get("id")
        assert ver["decision"] == "needs_review"
        TestVersions._ver_id = ver["id"]

    def test_get(self):
        ver = db_repo.get_version(TestVersions._ver_id)
        assert ver is not None

    def test_pending_list(self):
        rows, total = db_repo.list_pending_reviews(limit=20)
        assert total >= 1
        ids = [r["id"] for r in rows]
        assert TestVersions._ver_id in ids

    def test_accept(self):
        result = db_repo.accept_version(TestVersions._ver_id, applied_by="pytest")
        assert result["decision"] == "auto_applied"
        # double-accept should raise
        with pytest.raises(ValueError):
            db_repo.accept_version(TestVersions._ver_id, applied_by="pytest")

    def test_reject_on_needs_review_version(self):
        listing = db_repo.get_listing_by_gs_id(_DUMMY_GS_ID)
        ver2 = db_repo.insert_version(
            listing_id=listing["id"],
            batch_id=None,
            field="address",
            old_value="old addr",
            new_value="new addr",
            confidence=0.6,
        )
        result = db_repo.reject_version(ver2["id"], reviewed_by="pytest", reason="test")
        assert result["decision"] == "discarded"


# ---------------------------------------------------------------------------
# Cost log
# ---------------------------------------------------------------------------

class TestCostLog:
    def test_bump_and_list(self):
        today = date.today().isoformat()
        db_repo.bump_cost(today, llm_calls=1, llm_cost_eur=0.001, http_requests=5)
        rows = db_repo.list_cost_log(from_date=today, to_date=today)
        assert rows
        assert rows[0]["llm_calls"] >= 1
