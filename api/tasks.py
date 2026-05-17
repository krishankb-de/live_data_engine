"""Celery task — run pipeline phases in background worker."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="api.tasks.run_pipeline_task")
def run_pipeline_task(self, batch_id: int, phases: list[int], test_mode: bool) -> dict:
    """Execute scraper phases and persist to Supabase. Called via .delay() from POST /api/batches."""
    import db_repo

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
            db_repo.log_audit("phase1", "ok", batch_id=batch_id, details={"count": len(results)})

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
                    confidence = 0.9 if source in ("jsonld", "recipe") else 0.75
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
        return counts

    except Exception as e:
        logger.exception("batch %d failed: %s", batch_id, e)
        db_repo.finalize_batch(batch_id, counts, status="failed")
        db_repo.log_audit("batch", "error", batch_id=batch_id, details={"error": str(e)})
        raise
