"""GET /api/reviews/pending, POST /api/versions/{id}/accept|reject, GET /api/costs"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

import db_repo
from api.auth import Principal, require_auth
from api.schemas import PaginatedVersions, ReviewDecision, VersionOut

router = APIRouter(tags=["reviews"])


@router.get("/api/reviews/pending", response_model=PaginatedVersions)
def list_pending(
    _: Annotated[Principal, Depends(require_auth)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    rows, total = db_repo.list_pending_reviews(limit=limit, offset=offset)
    return {"items": rows, "total": total}


@router.post("/api/versions/{version_id}/accept", response_model=VersionOut)
def accept_version(
    version_id: int,
    principal: Annotated[Principal, Depends(require_auth)],
) -> dict:
    try:
        return db_repo.accept_version(version_id, applied_by=principal.label())
    except LookupError:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/versions/{version_id}/reject", response_model=VersionOut)
def reject_version(
    version_id: int,
    principal: Annotated[Principal, Depends(require_auth)],
    body: ReviewDecision = ReviewDecision(),
) -> dict:
    try:
        return db_repo.reject_version(
            version_id, reviewed_by=principal.label(), reason=body.reason
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/api/costs")
def get_costs(
    _: Annotated[Principal, Depends(require_auth)],
    from_date: str = Query("", alias="from"),
    to_date: str = Query("", alias="to"),
) -> dict:
    rows = db_repo.list_cost_log(from_date=from_date, to_date=to_date)
    totals = {
        "llm_calls": sum(r.get("llm_calls", 0) for r in rows),
        "llm_cost_eur": sum(r.get("llm_cost_eur", 0.0) for r in rows),
        "http_requests": sum(r.get("http_requests", 0) for r in rows),
        "listings_processed": sum(r.get("listings_processed", 0) for r in rows),
    }
    return {"items": rows, "totals": totals}
