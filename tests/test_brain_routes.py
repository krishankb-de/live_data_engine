"""Tests for Phase 7: brain API routes.

Covers:
  - GET /api/brain/patterns (list, filter, pagination)
  - GET /api/brain/candidates (list, filter)
  - POST /api/brain/patterns/{id}/disable (ok, not found)
  - GET /api/brain/metrics (counts, accept_rate, cost, recent_decisions)
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.server import app  # noqa: E402  (load_dotenv runs here)

_API_KEY = os.environ.get("API_KEY", "test-key-dev")
os.environ.setdefault("API_KEY", _API_KEY)

client = TestClient(app, headers={"X-API-Key": _API_KEY})

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _pat(id: int = 1, status: str = "active", field: str = "phone") -> dict:
    return {
        "id": id,
        "field": field,
        "pattern_type": "regex",
        "pattern": r"\+?\d[\d\s\-]{6,}",
        "language": "de",
        "confidence_score": 0.8,
        "success_count": 12,
        "failure_count": 1,
        "status": status,
        "origin_domain": None,
        "parent_recipe_id": None,
        "created_at": "2026-05-17T10:00:00",
        "last_used_at": "2026-05-17T11:00:00",
    }


def _cand(id: int = 1, status: str = "queued", field: str = "phone") -> dict:
    return {
        "id": id,
        "field": field,
        "pattern_type": "regex",
        "candidate_pattern": r"\+?\d{10,}",
        "language": "de",
        "status": status,
        "sandbox_precision": 0.97,
        "sandbox_recall": 0.72,
        "llm_cost_eur": 0.0001,
        "ts": "2026-05-17T09:00:00",
    }


# ---------------------------------------------------------------------------
# GET /api/brain/patterns
# ---------------------------------------------------------------------------

def test_list_patterns_empty():
    with patch("db_repo.list_patterns", return_value=[]):
        r = client.get("/api/brain/patterns")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_list_patterns_returns_rows():
    rows = [_pat(1, "active"), _pat(2, "trial")]
    with patch("db_repo.list_patterns", return_value=rows) as m:
        r = client.get("/api/brain/patterns?field=phone&status=active")
    m.assert_called_once_with(field="phone", status="active")
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_patterns_pagination():
    rows = [_pat(i) for i in range(10)]
    with patch("db_repo.list_patterns", return_value=rows):
        r = client.get("/api/brain/patterns?limit=3&offset=4")
    body = r.json()
    assert body["total"] == 10
    assert len(body["items"]) == 3
    assert body["items"][0]["id"] == 4


def test_list_patterns_no_auth():
    r = client.get("/api/brain/patterns", headers={"X-API-Key": "deliberate-wrong-key-xyz"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/brain/candidates
# ---------------------------------------------------------------------------

def test_list_candidates_queued():
    rows = [_cand(1, "queued"), _cand(2, "queued")]
    with patch("db_repo.list_candidates", return_value=rows) as m:
        r = client.get("/api/brain/candidates?status=queued")
    m.assert_called_once_with(status="queued")
    assert r.json()["total"] == 2


def test_list_candidates_all_statuses():
    rows = [_cand(1, "queued"), _cand(2, "promoted"), _cand(3, "rejected")]
    with patch("db_repo.list_candidates", return_value=rows) as m:
        r = client.get("/api/brain/candidates?status=")
    m.assert_called_once_with(status=None)
    assert r.json()["total"] == 3


def test_list_candidates_pagination():
    rows = [_cand(i, "promoted") for i in range(8)]
    with patch("db_repo.list_candidates", return_value=rows):
        r = client.get("/api/brain/candidates?status=promoted&limit=3&offset=2")
    body = r.json()
    assert body["total"] == 8
    assert len(body["items"]) == 3


# ---------------------------------------------------------------------------
# POST /api/brain/patterns/{id}/disable
# ---------------------------------------------------------------------------

def test_disable_pattern_ok():
    with patch("db_repo.get_pattern", return_value=_pat(7)) as gp, \
         patch("db_repo.set_pattern_status") as sp:
        r = client.post("/api/brain/patterns/7/disable")
    gp.assert_called_once_with(7)
    sp.assert_called_once_with(7, "disabled")
    assert r.status_code == 200
    assert r.json() == {"id": 7, "status": "disabled"}


def test_disable_pattern_not_found():
    with patch("db_repo.get_pattern", return_value=None):
        r = client.post("/api/brain/patterns/999/disable")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/brain/metrics
# ---------------------------------------------------------------------------

def test_metrics_empty():
    with patch("db_repo.list_patterns", return_value=[]), \
         patch("db_repo.list_candidates", return_value=[]), \
         patch("db_repo.cost_today_eur", return_value=0.0):
        r = client.get("/api/brain/metrics")
    body = r.json()
    assert body["pattern_counts"] == {}
    assert body["accept_rate"] is None
    assert body["cost_today_eur"] == 0.0
    assert body["recent_decisions"] == []


def test_metrics_counts_patterns():
    patterns = [
        _pat(1, "active"), _pat(2, "active"), _pat(3, "trial"), _pat(4, "stale"),
    ]
    with patch("db_repo.list_patterns", return_value=patterns), \
         patch("db_repo.list_candidates", return_value=[]), \
         patch("db_repo.cost_today_eur", return_value=0.005):
        r = client.get("/api/brain/metrics")
    body = r.json()
    assert body["pattern_counts"] == {"active": 2, "trial": 1, "stale": 1}
    assert body["cost_today_eur"] == pytest.approx(0.005)


def test_metrics_accept_rate():
    candidates = [
        _cand(1, "promoted"), _cand(2, "promoted"), _cand(3, "rejected"),
    ]
    with patch("db_repo.list_patterns", return_value=[]), \
         patch("db_repo.list_candidates", return_value=candidates), \
         patch("db_repo.cost_today_eur", return_value=0.0):
        r = client.get("/api/brain/metrics")
    # 2 promoted / 3 decided → 0.6667
    assert r.json()["accept_rate"] == pytest.approx(0.6667, abs=1e-3)


def test_metrics_recent_decisions_capped_at_10():
    candidates = [_cand(i, "promoted" if i % 2 == 0 else "rejected") for i in range(20)]
    # Add one queued to confirm it's excluded
    candidates.append(_cand(99, "queued"))
    with patch("db_repo.list_patterns", return_value=[]), \
         patch("db_repo.list_candidates", return_value=candidates), \
         patch("db_repo.cost_today_eur", return_value=0.0):
        r = client.get("/api/brain/metrics")
    decisions = r.json()["recent_decisions"]
    assert len(decisions) == 10
    assert all(d["status"] in ("promoted", "rejected") for d in decisions)


def test_metrics_cost_uses_today_iso():
    captured = []

    def _fake_cost(day_iso: str) -> float:
        captured.append(day_iso)
        return 1.23

    with patch("db_repo.list_patterns", return_value=[]), \
         patch("db_repo.list_candidates", return_value=[]), \
         patch("db_repo.cost_today_eur", side_effect=_fake_cost):
        r = client.get("/api/brain/metrics")
    assert r.json()["cost_today_eur"] == pytest.approx(1.23)
    # day_iso must look like YYYY-MM-DD
    assert len(captured) == 1
    assert len(captured[0]) == 10
    assert captured[0][4] == "-" and captured[0][7] == "-"
