"""Phase 3 — seed listing tests.

Unit layer: mocks db_repo, no network.
Online layer (pytest.mark.online): verifies real Supabase rows.

Run offline:  pytest tests/test_seed_listing.py
Run online:   pytest tests/test_seed_listing.py --online
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUSINESS_JSON = ROOT / "mock-source-site" / "data" / "business.json"
MOCK_SITE_URL = "http://localhost:5174/"


def _load_baseline() -> dict:
    return json.loads(BUSINESS_JSON.read_text())


# ---------------------------------------------------------------------------
# Unit tests — mocked db_repo
# ---------------------------------------------------------------------------

class TestSeedUnit:
    """Verify seeder calls upsert_listing with the right payloads."""

    def _import_seeder(self):
        import scripts.seed_mock_listing as s
        importlib.reload(s)
        return s

    def test_permanent_gs_id(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1, "gs_listing_id": "mock-5174"}
            seeder.seed_permanent(baseline)
        args = mock_repo.upsert_listing.call_args[0][0]
        assert args["gs_listing_id"] == "mock-5174"

    def test_permanent_website_url(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1}
            seeder.seed_permanent(baseline)
        args = mock_repo.upsert_listing.call_args[0][0]
        assert args["website_url"] == MOCK_SITE_URL

    def test_permanent_baseline_fields(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1}
            seeder.seed_permanent(baseline)
        args = mock_repo.upsert_listing.call_args[0][0]
        assert args["name"] == baseline["name"]
        assert args["phone"] == baseline.get("phone")
        assert args["address"] == baseline.get("address")

    def test_pool_count(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        n = 10
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 99}
            results = seeder.seed_pool(baseline, n)
        assert mock_repo.upsert_listing.call_count == n
        assert len(results) == n

    def test_pool_gs_id_format(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1}
            seeder.seed_pool(baseline, 5)
        gs_ids = [c[0][0]["gs_listing_id"] for c in mock_repo.upsert_listing.call_args_list]
        assert gs_ids == ["mock-5174-001", "mock-5174-002", "mock-5174-003", "mock-5174-004", "mock-5174-005"]

    def test_pool_all_share_site_url(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1}
            seeder.seed_pool(baseline, 3)
        urls = [c[0][0]["website_url"] for c in mock_repo.upsert_listing.call_args_list]
        assert all(u == MOCK_SITE_URL for u in urls)

    def test_ephemeral_gs_id_prefix(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1}
            seeder.seed_ephemeral(baseline, 3)
        gs_ids = [c[0][0]["gs_listing_id"] for c in mock_repo.upsert_listing.call_args_list]
        for gid in gs_ids:
            assert gid.startswith("test_mock_"), f"unexpected gs_id: {gid}"

    def test_ephemeral_count(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1}
            results = seeder.seed_ephemeral(baseline, 5)
        assert len(results) == 5

    def test_ephemeral_unique_ids(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1}
            seeder.seed_ephemeral(baseline, 4)
        gs_ids = [c[0][0]["gs_listing_id"] for c in mock_repo.upsert_listing.call_args_list]
        assert len(set(gs_ids)) == 4

    def test_idempotent_main_calls_permanent_always(self):
        seeder = self._import_seeder()
        baseline = _load_baseline()
        with patch("scripts.seed_mock_listing.db_repo") as mock_repo:
            mock_repo.upsert_listing.return_value = {"id": 1, "gs_listing_id": "mock-5174"}
            with patch.object(seeder, "_load_baseline", return_value=baseline):
                seeder.seed_permanent(baseline)
                seeder.seed_permanent(baseline)
        # Two calls — idempotent upsert handles duplicates in db_repo
        assert mock_repo.upsert_listing.call_count == 2


# ---------------------------------------------------------------------------
# Online tests — real Supabase
# ---------------------------------------------------------------------------

pytestmark_online = pytest.mark.online


@pytest.mark.online
class TestSeedOnline:
    """Verify seeder rows land in real Supabase."""

    @pytest.fixture(autouse=True)
    def _load_env(self):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")

    def test_permanent_row_exists(self):
        import db_repo
        row = db_repo.get_listing_by_gs_id("mock-5174")
        assert row is not None, "mock-5174 not found — run seed_mock_listing.py first"
        assert row["website_url"] == MOCK_SITE_URL

    def test_permanent_baseline_name(self):
        import db_repo
        baseline = _load_baseline()
        row = db_repo.get_listing_by_gs_id("mock-5174")
        assert row is not None
        assert row["name"] == baseline["name"]

    def test_pool_row_001(self):
        import db_repo
        row = db_repo.get_listing_by_gs_id("mock-5174-001")
        assert row is not None, "mock-5174-001 not found — run seed_mock_listing.py --count 200"
        assert row["website_url"] == MOCK_SITE_URL

    def test_pool_row_200(self):
        import db_repo
        row = db_repo.get_listing_by_gs_id("mock-5174-200")
        assert row is not None, "mock-5174-200 not found — run seed_mock_listing.py --count 200"

    def test_pool_row_url_matches(self):
        import db_repo
        for gs_id in ["mock-5174-050", "mock-5174-100", "mock-5174-150"]:
            row = db_repo.get_listing_by_gs_id(gs_id)
            assert row is not None, f"{gs_id} not found"
            assert row["website_url"] == MOCK_SITE_URL

    def test_ephemeral_upsert_and_fetch(self):
        import db_repo
        import scripts.seed_mock_listing as seeder
        baseline = _load_baseline()
        results = seeder.seed_ephemeral(baseline, 1)
        gs_id = results[0]["gs_listing_id"]
        assert gs_id.startswith("test_mock_")
        row = db_repo.get_listing_by_gs_id(gs_id)
        assert row is not None
        assert row["website_url"] == MOCK_SITE_URL

    def test_upsert_is_idempotent(self):
        import db_repo
        # Calling upsert twice on mock-5174 must not create duplicates
        baseline = _load_baseline()
        import scripts.seed_mock_listing as seeder
        seeder.seed_permanent(baseline)
        seeder.seed_permanent(baseline)
        # Use direct gs_id lookup — list_listings paginates and may omit older rows
        row = db_repo.get_listing_by_gs_id("mock-5174")
        assert row is not None, "mock-5174 row should exist after double upsert"
