"""GET /api/listings, GET /api/listings/{id}, GET /api/listings/{id}/versions"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

import db_repo
from api.auth import Principal, require_auth
from api.schemas import FieldObs, ListingOut, PaginatedListings, VersionOut

router = APIRouter(prefix="/api/listings", tags=["listings"])


@router.get("", response_model=PaginatedListings)
def list_listings(
    _: Annotated[Principal, Depends(require_auth)],
    q: str = Query("", description="Search name/address"),
    city: str = Query("", description="Filter by city substring in address"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    rows, total = db_repo.list_listings(q=q, city=city, limit=limit, offset=offset)
    return {"items": rows, "total": total}


@router.get("/{listing_id}", response_model=dict)
def get_listing(
    listing_id: int,
    _: Annotated[Principal, Depends(require_auth)],
) -> dict:
    row = db_repo.get_listing(listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")
    obs = db_repo.latest_observations(listing_id)
    return {"listing": row, "latest_observations": obs}


@router.get("/{listing_id}/versions", response_model=list[VersionOut])
def list_versions(
    listing_id: int,
    _: Annotated[Principal, Depends(require_auth)],
) -> list:
    row = db_repo.get_listing(listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")
    return db_repo.list_versions_for_listing(listing_id)
