"""FastAPI route tests — uses TestClient (no network to Supabase unless env vars set).

Covers:
  * /healthz + /readyz always reachable (no auth)
  * auth guard: 401 without credentials
  * batches: create, get, list (requires Supabase creds)
  * listings: list, get (requires Supabase creds)
  * reviews/pending + accept/reject (requires Supabase creds)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from fastapi.testclient import TestClient

from api.server import app

_API_KEY = os.environ.get("API_KEY", "test-key-dev")
os.environ.setdefault("API_KEY", _API_KEY)

_HEADERS = {"X-API-Key": _API_KEY}
_HAS_DB = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY"))

client = TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=False)
def reset_rate_limit():
    """Clear the in-memory rate limit so batch POST tests don't conflict."""
    from api.routes.batches import _last_allowed
    _last_allowed.clear()
    yield
    _last_allowed.clear()


# ---------------------------------------------------------------------------
# Health (no auth required)
# ---------------------------------------------------------------------------

def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_readyz_returns_valid_shape():
    r = client.get("/readyz")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "ok" in body
    assert "supabase" in body
    assert "redis" in body


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def test_listings_without_auth_returns_401():
    r = client.get("/api/listings")
    assert r.status_code == 401


def test_batches_without_auth_returns_401():
    r = client.get("/api/batches")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Listings (live DB)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_list_listings():
    r = client.get("/api/listings", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_list_listings_search():
    r = client.get("/api/listings?q=Buchhandlung", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 0


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_list_listings_city_filter():
    r = client.get("/api/listings?city=Berlin", headers=_HEADERS)
    assert r.status_code == 200
    assert "items" in r.json()



@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_get_listing_404():
    r = client.get("/api/listings/999999999", headers=_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Batches (live DB)
# ---------------------------------------------------------------------------

def test_rate_limit_batch(reset_rate_limit):
    """Second POST within 60s should return 429."""
    # First call succeeds (or is skipped if no DB)
    r1 = client.post("/api/batches", json={"phases": [], "test_mode": True}, headers=_HEADERS)
    if r1.status_code == 201:
        r2 = client.post("/api/batches", json={"phases": [], "test_mode": True}, headers=_HEADERS)
        assert r2.status_code == 429


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_create_and_get_batch(reset_rate_limit):
    r = client.post(
        "/api/batches",
        json={"phases": [], "test_mode": True},  # no phases = instant done
        headers=_HEADERS,
    )
    assert r.status_code == 201
    body = r.json()
    assert "batch_id" in body
    assert body["status"] == "queued"

    batch_id = body["batch_id"]
    r2 = client.get(f"/api/batches/{batch_id}", headers=_HEADERS)
    assert r2.status_code == 200
    assert r2.json()["id"] == batch_id


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_list_batches():
    r = client.get("/api/batches?limit=5", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "total" in body


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_get_batch_404():
    r = client.get("/api/batches/999999999", headers=_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reviews (live DB)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_pending_reviews():
    r = client.get("/api/reviews/pending", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "total" in body


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_accept_nonexistent_version():
    r = client.post("/api/versions/999999999/accept", headers=_HEADERS)
    assert r.status_code == 404


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_reject_nonexistent_version():
    r = client.post("/api/versions/999999999/reject", headers=_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_costs():
    r = client.get("/api/costs", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "totals" in body
