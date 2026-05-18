"""Phase 7 — extends test_api.py.

New coverage:
  * Brain: /api/brain/patterns, candidates, metrics, disable
  * Listing versions: GET /api/listings/{id}/versions
  * Notifications: POST /api/listings/{id}/send-approval-email
  * Auth guard for every new route (no DB needed)
  * Mocked unit layer for shape / error-path assertions
  * CELERY_TASK_ALWAYS_EAGER=true inline (no Redis)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ.pop("REDIS_URL", None)

from fastapi.testclient import TestClient
from api.server import app

_API_KEY = os.environ.get("API_KEY", "test-key-dev")
os.environ.setdefault("API_KEY", _API_KEY)

_H = {"X-API-Key": _API_KEY}

def _check_db() -> bool:
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SECRET_KEY")):
        return False
    try:
        from supabase import create_client  # noqa: F401
        return True
    except Exception:
        return False

_HAS_DB = _check_db()

client = TestClient(app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _paginated(items: list, total: int | None = None) -> tuple:
    return items, total if total is not None else len(items)


# ---------------------------------------------------------------------------
# Auth guard — brain routes
# ---------------------------------------------------------------------------

def test_brain_patterns_no_auth():
    assert client.get("/api/brain/patterns").status_code == 401


def test_brain_candidates_no_auth():
    assert client.get("/api/brain/candidates").status_code == 401


def test_brain_metrics_no_auth():
    assert client.get("/api/brain/metrics").status_code == 401


def test_brain_disable_no_auth():
    assert client.post("/api/brain/patterns/1/disable").status_code == 401


# ---------------------------------------------------------------------------
# Auth guard — listing versions / notifications
# ---------------------------------------------------------------------------

def test_listing_versions_no_auth():
    assert client.get("/api/listings/1/versions").status_code == 401


def test_send_approval_email_no_auth():
    assert client.post("/api/listings/1/send-approval-email").status_code == 401


# ---------------------------------------------------------------------------
# Brain — mocked unit tests (no DB)
# ---------------------------------------------------------------------------

def _pat(**kw):
    base = {"id": 1, "field": "phone", "pattern_type": "regex", "pattern": r"\+49",
            "language": "de", "confidence_score": 0.9, "success_count": 10,
            "failure_count": 1, "status": "active"}
    base.update(kw)
    return base


def _cand(**kw):
    base = {"id": 1, "field": "phone", "pattern_type": "regex",
            "candidate_pattern": r"\+49 ?\d+", "language": "de", "status": "queued"}
    base.update(kw)
    return base


@patch("db_repo.list_patterns", return_value=[_pat(id=1), _pat(id=2, field="opening_hours", status="disabled")])
def test_brain_patterns_shape(mock_lp):
    r = client.get("/api/brain/patterns", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "total" in body
    assert body["total"] == 2


@patch("db_repo.list_patterns", return_value=[_pat()])
def test_brain_patterns_field_filter(mock_lp):
    r = client.get("/api/brain/patterns?field=phone", headers=_H)
    assert r.status_code == 200
    assert r.json()["total"] >= 0


@patch("db_repo.list_patterns", return_value=[_pat(id=1), _pat(id=2, field="address")])
def test_brain_patterns_status_filter(mock_lp):
    r = client.get("/api/brain/patterns?status=active", headers=_H)
    assert r.status_code == 200


@patch("db_repo.list_candidates", return_value=[_cand()])
def test_brain_candidates_shape(mock_lc):
    r = client.get("/api/brain/candidates", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "total" in body


@patch("db_repo.list_candidates", return_value=[_cand(id=1, status="promoted"), _cand(id=2, status="rejected")])
def test_brain_candidates_status_param(mock_lc):
    r = client.get("/api/brain/candidates?status=promoted", headers=_H)
    assert r.status_code == 200


@patch("db_repo.get_pattern", return_value=None)
def test_brain_disable_pattern_404(mock_gp):
    r = client.post("/api/brain/patterns/999999/disable", headers=_H)
    assert r.status_code == 404


@patch("db_repo.set_pattern_status")
@patch("db_repo.get_pattern", return_value={"id": 5, "status": "active"})
def test_brain_disable_pattern_ok(mock_gp, mock_sp):
    r = client.post("/api/brain/patterns/5/disable", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 5
    assert body["status"] == "disabled"
    mock_sp.assert_called_once_with(5, "disabled")


@patch("db_repo.cost_today_eur", return_value=0.03)
@patch("db_repo.list_candidates", return_value=[
    {"id": 1, "status": "promoted", "ts": "2026-05-17T10:00:00"},
    {"id": 2, "status": "rejected", "ts": "2026-05-17T09:00:00"},
])
@patch("db_repo.list_patterns", return_value=[
    {"id": 1, "status": "active"},
    {"id": 2, "status": "disabled"},
])
def test_brain_metrics_shape(mock_lp, mock_lc, mock_cost):
    r = client.get("/api/brain/metrics", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert "pattern_counts" in body
    assert "accept_rate" in body
    assert "cost_today_eur" in body
    assert "recent_decisions" in body


@patch("db_repo.cost_today_eur", return_value=0.0)
@patch("db_repo.list_candidates", return_value=[])
@patch("db_repo.list_patterns", return_value=[])
def test_brain_metrics_empty_state(mock_lp, mock_lc, mock_cost):
    r = client.get("/api/brain/metrics", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert body["accept_rate"] is None
    assert body["cost_today_eur"] == 0.0


# ---------------------------------------------------------------------------
# Brain — live DB
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_brain_patterns_live():
    r = client.get("/api/brain/patterns", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_brain_candidates_live():
    r = client.get("/api/brain/candidates", headers=_H)
    assert r.status_code == 200
    assert "items" in r.json()


@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_brain_metrics_live():
    r = client.get("/api/brain/metrics", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert "pattern_counts" in body
    assert "cost_today_eur" in body


# ---------------------------------------------------------------------------
# Listing versions — mocked unit tests
# ---------------------------------------------------------------------------

@patch("db_repo.list_versions_for_listing", return_value=[])
@patch("db_repo.get_listing", return_value=None)
def test_listing_versions_404(mock_gl, mock_lv):
    r = client.get("/api/listings/999999999/versions", headers=_H)
    assert r.status_code == 404


@patch("db_repo.list_versions_for_listing", return_value=[
    {"id": 1, "listing_id": 42, "field": "phone", "old_value": "123", "new_value": "456",
     "intent_confidence": 0.9, "decision": "auto_applied"},
])
@patch("db_repo.get_listing", return_value={"id": 42, "name": "Test Biz"})
def test_listing_versions_shape(mock_gl, mock_lv):
    r = client.get("/api/listings/42/versions", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["field"] == "phone"


# ---------------------------------------------------------------------------
# Listing versions — live DB
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_DB, reason="no Supabase creds")
def test_listing_versions_live_404():
    r = client.get("/api/listings/999999999/versions", headers=_H)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Notifications — mocked unit tests (no external call)
# ---------------------------------------------------------------------------

@patch("db_repo.get_listing", return_value=None)
def test_send_approval_email_listing_not_found(mock_gl):
    with patch.dict(os.environ, {"RESEND_API_KEY": "re_fake", "APPROVAL_EMAIL_TO": "x@example.com"}):
        r = client.post("/api/listings/999999/send-approval-email", headers=_H)
    assert r.status_code == 404


def test_send_approval_email_no_resend_key():
    env = {"RESEND_API_KEY": "", "APPROVAL_EMAIL_TO": "x@example.com"}
    with patch.dict(os.environ, env):
        r = client.post("/api/listings/1/send-approval-email", headers=_H)
    assert r.status_code == 500
    assert "RESEND_API_KEY" in r.json()["detail"]


def test_send_approval_email_no_to_addr():
    env = {"RESEND_API_KEY": "re_fake", "APPROVAL_EMAIL_TO": ""}
    with patch.dict(os.environ, env):
        r = client.post("/api/listings/1/send-approval-email", headers=_H)
    assert r.status_code == 500
    assert "APPROVAL_EMAIL_TO" in r.json()["detail"]


@patch("resend.Emails.send", return_value={"id": "msg-abc"})
@patch("db_repo.get_listing", return_value={"id": 7, "name": "Café Test", "address": "Berlin", "opening_hours": "Mo-Fr 9-18"})
def test_send_approval_email_ok(mock_gl, mock_send):
    env = {"RESEND_API_KEY": "re_fake", "APPROVAL_EMAIL_TO": "ops@example.com", "SITE_URL": "http://localhost:5173"}
    with patch.dict(os.environ, env):
        r = client.post("/api/listings/7/send-approval-email", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["to"] == "ops@example.com"
    assert body["message_id"] == "msg-abc"


# ---------------------------------------------------------------------------
# Pagination / query param validation
# ---------------------------------------------------------------------------

@patch("db_repo.list_patterns", return_value=[_pat(id=i) for i in range(10)])
def test_brain_patterns_pagination(mock_lp):
    r = client.get("/api/brain/patterns?limit=3&offset=0", headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 3
    assert body["total"] == 10


@patch("db_repo.list_patterns", return_value=[])
def test_brain_patterns_limit_too_large(mock_lp):
    r = client.get("/api/brain/patterns?limit=999", headers=_H)
    assert r.status_code == 422


@patch("db_repo.list_candidates", return_value=[])
def test_brain_candidates_limit_too_large(mock_lc):
    r = client.get("/api/brain/candidates?limit=999", headers=_H)
    assert r.status_code == 422
