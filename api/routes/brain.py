"""Brain inspection endpoints.

GET  /api/brain/patterns?field=&status=   list patterns with scores
GET  /api/brain/candidates?status=        pending/reviewed candidates
POST /api/brain/patterns/{id}/disable     manual kill switch
GET  /api/brain/metrics                   cost, accept rate, status breakdown
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

import db_repo
from api.auth import Principal, require_auth
from api.schemas import (
    BrainCandidateOut,
    BrainMetrics,
    BrainPatternOut,
    PaginatedBrainCandidates,
    PaginatedBrainPatterns,
)

router = APIRouter(prefix="/api/brain", tags=["brain"])


@router.get("/patterns", response_model=PaginatedBrainPatterns)
def list_patterns(
    _: Annotated[Principal, Depends(require_auth)],
    field: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = db_repo.list_patterns(field=field, status=status)
    total = len(rows)
    return {"items": rows[offset: offset + limit], "total": total}


@router.get("/candidates", response_model=PaginatedBrainCandidates)
def list_candidates(
    _: Annotated[Principal, Depends(require_auth)],
    status: Optional[str] = Query("queued"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    rows = db_repo.list_candidates(status=status or None)
    total = len(rows)
    return {"items": rows[offset: offset + limit], "total": total}


@router.post("/patterns/{pattern_id}/disable", status_code=status.HTTP_200_OK)
def disable_pattern(
    pattern_id: int,
    _: Annotated[Principal, Depends(require_auth)],
) -> dict:
    pat = db_repo.get_pattern(pattern_id)
    if pat is None:
        raise HTTPException(status_code=404, detail="pattern not found")
    db_repo.set_pattern_status(pattern_id, "disabled")
    return {"id": pattern_id, "status": "disabled"}


@router.get("/metrics", response_model=BrainMetrics)
def get_metrics(
    _: Annotated[Principal, Depends(require_auth)],
) -> dict:
    today = datetime.now(tz=timezone.utc).date().isoformat()

    all_patterns = db_repo.list_patterns()
    status_counts: dict[str, int] = {}
    for p in all_patterns:
        s = p.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    all_candidates = db_repo.list_candidates(status=None)
    promoted = sum(1 for c in all_candidates if c.get("status") == "promoted")
    rejected = sum(1 for c in all_candidates if c.get("status") == "rejected")
    total_decided = promoted + rejected
    accept_rate = round(promoted / total_decided, 4) if total_decided else None

    cost_eur = db_repo.cost_today_eur(today)

    recent_decisions = sorted(
        [c for c in all_candidates if c.get("status") in ("promoted", "rejected")],
        key=lambda c: c.get("ts") or "",
        reverse=True,
    )[:10]

    return {
        "pattern_counts": status_counts,
        "accept_rate": accept_rate,
        "cost_today_eur": cost_eur,
        "recent_decisions": recent_decisions,
    }
