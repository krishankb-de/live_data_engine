"""POST /api/batches, GET /api/batches, GET /api/batches/{id}"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

import db_repo
from api.auth import Principal, require_auth
from api.schemas import BatchCreate, BatchStatus, PaginatedBatches

router = APIRouter(prefix="/api/batches", tags=["batches"])
logger = logging.getLogger(__name__)

_USE_CELERY = bool(os.environ.get("REDIS_URL"))

# In-memory token bucket: 1 POST /api/batches per principal per 60s
_RATE_LIMIT_WINDOW = 60.0
_rate_lock = Lock()
_last_allowed: dict[str, float] = defaultdict(float)


def _run_pipeline(batch_id: int, phases: list[int], test_mode: bool) -> None:
    """Synchronous pipeline runner used as BackgroundTasks fallback (no Redis)."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    prefix = "test_" if test_mode else ""
    limit = 10 if test_mode else 200
    counts: dict = {
        "listings_processed": 0,
        "changes_proposed": 0,
        "changes_auto_applied": 0,
        "changes_review_queue": 0,
    }

    try:
        db_repo.update_batch(batch_id, status="running")

        if 1 in phases:
            from scraper.phase1_listings import run as p1
            results = p1(limit=limit, prefix=prefix)
            counts["listings_processed"] = len(results)
            for r in results:
                try:
                    db_repo.upsert_listing(r)
                except Exception as e:
                    logger.warning("listing upsert failed: %s", e)
            db_repo.log_audit("phase1", "ok", batch_id=batch_id,
                              details={"count": len(results)})

        if 2 in phases:
            from scraper.phase2_site_map import run as p2
            p2(prefix=prefix)
            db_repo.log_audit("phase2", "ok", batch_id=batch_id)

        if 3 in phases:
            from scraper.phase3_extract import run as p3
            extracted = p3(prefix=prefix)
            proposed = auto_applied = review_queue = 0
            for record in extracted:
                gs_id = record.get("gs_uuid") or record.get("gs_listing_id", "")
                listing_row = db_repo.get_listing_by_gs_id(gs_id) if gs_id else None
                if not listing_row:
                    continue
                lid = listing_row["id"]
                field_sources = record.get("field_sources") or {}
                for field in ("address", "phone", "opening_hours", "name"):
                    new_val = record.get(field)
                    if new_val is None:
                        continue
                    if isinstance(new_val, dict):
                        new_val = json.dumps(new_val, ensure_ascii=False, sort_keys=True)
                    source = field_sources.get(field, "regex")
                    # cache = previously-verified data (page unchanged); jsonld/recipe = structured
                    confidence = 0.9 if source in ("jsonld", "recipe", "cache") else 0.75
                    old_val = listing_row.get(field)
                    db_repo.insert_observation(lid, field, str(new_val), source)
                    if str(new_val) != str(old_val or ""):
                        ver = db_repo.insert_version(
                            listing_id=lid,
                            batch_id=batch_id,
                            field=field,
                            old_value=old_val,
                            new_value=str(new_val),
                            confidence=confidence,
                        )
                        proposed += 1
                        if ver.get("decision") == "auto_applied":
                            db_repo.update_listing_field(lid, field, str(new_val))
                            auto_applied += 1
                        elif ver.get("decision") == "needs_review":
                            review_queue += 1
            counts.update({
                "changes_proposed": proposed,
                "changes_auto_applied": auto_applied,
                "changes_review_queue": review_queue,
            })
            db_repo.log_audit("phase3", "ok", batch_id=batch_id, details=counts)

        if 4 in phases:
            from scraper.phase4_diff import run as p4
            p4(prefix=prefix)

        if 6 in phases:
            from scraper.phase6_content_hash import run as p6
            p6(prefix=prefix)

        db_repo.finalize_batch(batch_id, counts, status="done")
        logger.info("batch %d done: %s", batch_id, counts)

    except Exception as e:
        logger.exception("batch %d failed: %s", batch_id, e)
        db_repo.finalize_batch(batch_id, counts, status="failed")
        db_repo.log_audit("batch", "error", batch_id=batch_id, details={"error": str(e)})


def _enqueue(batch_id: int, phases: list[int], test_mode: bool,
             background_tasks: BackgroundTasks) -> str:
    """Enqueue via Celery if REDIS_URL is set, else BackgroundTasks."""
    if _USE_CELERY:
        from api.tasks import run_pipeline_task
        run_pipeline_task.delay(batch_id, phases, test_mode)
        return "celery"
    background_tasks.add_task(_run_pipeline, batch_id, phases, test_mode)
    return "thread"


def _check_rate_limit(principal_label: str) -> None:
    with _rate_lock:
        now = time.monotonic()
        last = _last_allowed[principal_label]
        if now - last < _RATE_LIMIT_WINDOW:
            retry_after = int(_RATE_LIMIT_WINDOW - (now - last)) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit: 1 batch/min. Retry after {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        _last_allowed[principal_label] = now


@router.post("", status_code=status.HTTP_201_CREATED)
def create_batch(
    body: BatchCreate,
    background_tasks: BackgroundTasks,
    principal: Annotated[Principal, Depends(require_auth)],
) -> dict:
    _check_rate_limit(principal.label())
    row = db_repo.create_batch()
    batch_id = row["id"]
    backend = _enqueue(batch_id, body.phases, body.test_mode, background_tasks)
    logger.info("batch %d queued (%s) by %s, phases=%s",
                batch_id, backend, principal.label(), body.phases)
    return {"batch_id": batch_id, "status": "queued"}


@router.get("/{batch_id}", response_model=BatchStatus)
def get_batch(
    batch_id: int,
    _: Annotated[Principal, Depends(require_auth)],
) -> dict:
    row = db_repo.get_batch(batch_id)
    if row is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return row


@router.get("", response_model=PaginatedBatches)
def list_batches(
    _: Annotated[Principal, Depends(require_auth)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    rows, total = db_repo.list_batches(limit=limit, offset=offset)
    return {"items": rows, "total": total}
