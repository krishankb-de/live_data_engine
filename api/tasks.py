"""Celery task — run pipeline phases in background worker."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from api.worker import celery_app

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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


# ---------------------------------------------------------------------------
# Brain: Phase 5 — Generalizer & Promotion
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="api.tasks.generalize_recipe_task")
def generalize_recipe_task(self, domain: str, field: str, field_selector: dict) -> dict:
    """Generalize a site-specific recipe selector into a candidate global brain pattern.

    Args:
        domain: e.g. "danteconnection.de"
        field: "phone" | "address" | "opening_hours" | "name"
        field_selector: recipe selector dict {"page_url", "css", "regex"}
    """
    import db_repo
    from scraper import brain
    from scraper.brain.generalizer import generalize

    if not brain.is_enabled():
        return {"skipped": "brain_disabled"}

    day = _today_iso()
    budget = brain.daily_budget_eur()
    try:
        today_cost = db_repo.cost_today_eur(day)
    except Exception:
        today_cost = 0.0

    if today_cost >= budget:
        logger.info(
            "Brain budget exhausted (€%.2f/€%.2f) — skipping generalize for %s/%s",
            today_cost, budget, domain, field,
        )
        return {"skipped": "budget_exhausted", "cost_today": today_cost, "budget": budget}

    result = generalize(domain, field, field_selector)
    if result is None:
        return {"skipped": "no_result"}

    try:
        db_repo.bump_cost(
            day_iso=day,
            llm_calls=1,
            llm_tokens_in=result.tokens_in,
            llm_tokens_out=result.tokens_out,
            llm_cost_eur=result.llm_cost_eur,
        )
    except Exception as e:
        logger.warning("Failed to record brain LLM cost: %s", e)

    try:
        cand = db_repo.enqueue_candidate(
            field=field,
            pattern_type=result.pattern_type,
            candidate_pattern=result.pattern,
            language=result.language,
            llm_cost_eur=result.llm_cost_eur,
            rationale=result.rationale,
        )
        cand_id = cand.get("id")
        logger.info("Generalizer enqueued candidate %s for %s/%s", cand_id, domain, field)
        return {"candidate_id": cand_id, "field": field, "domain": domain}
    except Exception as e:
        logger.error("Failed to enqueue candidate for %s/%s: %s", domain, field, e)
        return {"error": str(e)}


@celery_app.task(bind=True, name="api.tasks.promote_candidates_task")
def promote_candidates_task(self) -> dict:
    """Periodic task (hourly): validate queued candidates and promote passing ones.

    Passing threshold: per-field precision/recall + zero negative-fixture hits.
    On pass  → global_patterns row with status='trial'.
    On fail  → candidate status='rejected', sample_failures stored for context.
    """
    import db_repo
    from scraper import brain
    from scraper.brain.runtime import invalidate_cache
    from scraper.brain.sandbox import passes_thresholds, validate_candidate

    candidates = db_repo.list_candidates(status="queued")
    promoted = rejected = errors = 0

    for cand in candidates:
        cand_id = cand["id"]
        field = cand["field"]
        pattern = cand["candidate_pattern"]
        pattern_type = cand["pattern_type"]
        language = cand.get("language") or "any"

        try:
            db_repo.update_candidate(cand_id, status="validating")

            metrics = validate_candidate(
                candidate_pattern=pattern,
                field=field,
                pattern_type=pattern_type,
                language=language,
            )
            precision = metrics["precision"]
            recall = metrics["recall"]
            neg_hits = metrics.get("negative_hits", 0)

            if passes_thresholds(metrics, field):
                # Save metrics onto candidate, then promote to global_patterns
                db_repo.update_candidate(
                    cand_id,
                    status="validating",
                    sandbox_precision=precision,
                    sandbox_recall=recall,
                    sandbox_details={
                        "true_positives": metrics.get("true_positives"),
                        "false_positives": metrics.get("false_positives"),
                        "negative_hits": neg_hits,
                    },
                )
                pat = db_repo.promote_candidate(cand_id)
                invalidate_cache(field)
                logger.info(
                    "Promoted candidate %d → pattern %s (field=%s prec=%.2f recall=%.2f)",
                    cand_id, pat.get("id"), field, precision, recall,
                )
                promoted += 1
            else:
                db_repo.update_candidate(
                    cand_id,
                    status="rejected",
                    sandbox_precision=precision,
                    sandbox_recall=recall,
                    sandbox_details={
                        "sample_failures": metrics.get("sample_failures", []),
                        "negative_hits": neg_hits,
                    },
                )
                logger.info(
                    "Rejected candidate %d (field=%s prec=%.2f recall=%.2f neg_hits=%d)",
                    cand_id, field, precision, recall, neg_hits,
                )
                rejected += 1

        except Exception as e:
            logger.error("promote_candidates_task: error on candidate %d: %s", cand_id, e)
            try:
                db_repo.update_candidate(cand_id, status="queued")  # reset for retry
            except Exception:
                pass
            errors += 1

    # --- Trial → Active promotion ---
    # Patterns already in global_patterns that have accumulated enough real-world evidence.
    activated = 0
    try:
        trial_patterns = db_repo.list_patterns(status="trial")
        for tpat in trial_patterns:
            pid = tpat["id"]
            success = int(tpat.get("success_count") or 0)
            failure = int(tpat.get("failure_count") or 0)
            conf = float(tpat.get("confidence_score") or 0.0)
            total = success + failure
            failure_ratio = failure / total if total > 0 else 0.0

            if (
                success >= brain.TRIAL_TO_ACTIVE_MIN_SUCCESS
                and conf >= brain.TRIAL_TO_ACTIVE_MIN_CONFIDENCE
                and failure_ratio <= brain.TRIAL_TO_ACTIVE_MAX_FAILURE_RATIO
            ):
                db_repo.set_pattern_status(pid, "active")
                invalidate_cache(tpat["field"])
                activated += 1
                logger.info(
                    "Trial pattern %d promoted to active (field=%s success=%d conf=%.2f failure_ratio=%.3f)",
                    pid, tpat["field"], success, conf, failure_ratio,
                )
    except Exception as e:
        logger.error("promote_candidates_task: trial→active scan failed: %s", e)

    logger.info(
        "promote_candidates_task done: promoted=%d rejected=%d activated=%d errors=%d",
        promoted, rejected, activated, errors,
    )
    return {"promoted": promoted, "rejected": rejected, "activated": activated, "errors": errors}


# ---------------------------------------------------------------------------
# Recheck pipeline (pipeline_ug) — adaptive doorman + scheduler
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="api.tasks.run_recheck_batch_task")
def run_recheck_batch_task(self, limit: int | None = None) -> dict:
    """Adaptive recheck: pick due listings, doorman-gate, extract on change, reschedule.

    Per listing:
      1. Doorman conditional-GET (cheap) — politeness-gated, uses stored ETag/hash.
      2. If 304 / same hash / vanished / error → skip extraction, just update next_check.
      3. If changed → call scraper.phase3_extract.extract_site → write observations
         + versions (same path as run_pipeline_task) → update next_check.

    Wired to a frequent Celery beat (every RECHECK_BEAT_SECONDS, default 300s).
    Listings only become 'due' on their own cadence, so most beat ticks do nothing
    and external sites aren't hammered. Tier caps + politeness gate are the safety
    net against ever exceeding sensible request rates.
    """
    import asyncio

    import db_repo
    from config import settings
    from pipeline_ug.runner import run_listing
    from pipeline_ug.scheduler import update_next_check

    effective_limit = limit if limit is not None else settings.recheck_batch_size

    batch_row = db_repo.create_batch()
    batch_id = batch_row.get("id") if isinstance(batch_row, dict) else None
    if batch_id is not None:
        db_repo.update_batch(batch_id, status="running")
        db_repo.log_audit(
            action="recheck_batch_start",
            outcome="ok",
            batch_id=batch_id,
            details={"limit": effective_limit},
        )

    due = db_repo.pick_due_listings(limit=effective_limit, verifiable_only=True)
    counts = {
        "processed": 0,
        "skipped_unchanged": 0,
        "skipped_unverifiable": 0,
        "extracted": 0,
        "vanished": 0,
        "errors": 0,
        "changes_proposed": 0,
        "changes_auto_applied": 0,
        "changes_review_queue": 0,
    }

    if not due:
        if batch_id is not None:
            db_repo.finalize_batch(
                batch_id,
                {
                    "listings_processed": 0,
                    "changes_proposed": 0,
                    "changes_auto_applied": 0,
                    "changes_review_queue": 0,
                },
                status="done",
            )
            db_repo.log_audit(
                action="recheck_batch_finish",
                outcome="ok",
                batch_id=batch_id,
                details=counts,
            )
        return counts

    # Lazy-load scraper deps — only when there's actual work.
    from scraper import phase4_diff, recipe_builder
    from scraper.phase3_extract import extract_site

    cache = phase4_diff.load_cache(prefix="")
    recipe_store = recipe_builder.RecipeStore()

    for listing in due:
        lid = int(listing["id"])

        # 1) Doorman pre-check — fresh event loop per listing, default_politeness
        #    is a module-level singleton so per-domain rate-limit state survives.
        try:
            trace = asyncio.run(run_listing(lid))
        except Exception as exc:
            logger.exception("doorman failed for listing %s: %s", lid, exc)
            try:
                update_next_check(lid, changed=False, unreachable=True)
            except Exception:
                pass
            counts["errors"] += 1
            if batch_id is not None:
                db_repo.log_audit(
                    action="recheck_doorman_error",
                    outcome="error",
                    listing_id=lid,
                    batch_id=batch_id,
                    details={"error": str(exc)},
                )
            continue

        counts["processed"] += 1
        outcome = trace.outcome

        # 2) Cheap outcomes — no extraction needed.
        if outcome in ("skipped_unverifiable", "skipped_no_website"):
            update_next_check(lid, changed=False)
            counts["skipped_unverifiable"] += 1
            continue

        if outcome == "all_unchanged":
            update_next_check(lid, changed=False)
            counts["skipped_unchanged"] += 1
            if batch_id is not None:
                db_repo.log_audit(
                    action="recheck_unchanged",
                    outcome="ok",
                    listing_id=lid,
                    batch_id=batch_id,
                )
            continue

        if outcome == "vanished":
            update_next_check(lid, changed=False, unreachable=True)
            counts["vanished"] += 1
            if batch_id is not None:
                db_repo.log_audit(
                    action="recheck_vanished",
                    outcome="vanished",
                    listing_id=lid,
                    batch_id=batch_id,
                    details={"reason": trace.reason},
                )
            continue

        if outcome == "error":
            update_next_check(lid, changed=False, unreachable=True)
            counts["errors"] += 1
            if batch_id is not None:
                db_repo.log_audit(
                    action="recheck_doorman_error",
                    outcome="error",
                    listing_id=lid,
                    batch_id=batch_id,
                    details={"reason": trace.reason},
                )
            continue

        # 3) outcome == "ok" — at least one page changed → extract.
        entry = {
            "name": listing.get("name"),
            "website_url": listing.get("website_url"),
            "gelbeseiten_url": None,
            "gs_uuid": listing.get("gs_listing_id"),
            "target_city": None,
            "pages": {},  # extract_site defaults to homepage-only visit order
        }
        try:
            record = extract_site(entry, cache=cache, recipe_store=recipe_store)
        except Exception as exc:
            logger.exception("extract_site failed for listing %s: %s", lid, exc)
            counts["errors"] += 1
            try:
                update_next_check(lid, changed=True)
            except Exception:
                pass
            if batch_id is not None:
                db_repo.log_audit(
                    action="recheck_extract_error",
                    outcome="error",
                    listing_id=lid,
                    batch_id=batch_id,
                    details={"error": str(exc)},
                )
            continue

        counts["extracted"] += 1

        # 4) Write observations + versions — mirrors run_pipeline_task phase3 block.
        field_sources = record.get("field_sources") or {}
        proposed_this_listing = 0
        for field in ("address", "phone", "opening_hours", "name"):
            new_val = record.get(field)
            if new_val is None:
                continue
            if isinstance(new_val, dict):
                new_val = json.dumps(new_val, ensure_ascii=False, sort_keys=True)
            source = field_sources.get(field, "regex")
            confidence = 0.9 if source in ("jsonld", "recipe") else 0.75
            old_val = listing.get(field)
            try:
                db_repo.insert_observation(lid, field, str(new_val), source)
            except Exception as e:
                logger.warning("observation insert failed (listing %s, field %s): %s", lid, field, e)
            if str(new_val) != str(old_val or ""):
                try:
                    ver = db_repo.insert_version(
                        listing_id=lid,
                        batch_id=batch_id,
                        field=field,
                        old_value=old_val,
                        new_value=str(new_val),
                        confidence=confidence,
                    )
                except Exception as e:
                    logger.warning("version insert failed (listing %s, field %s): %s", lid, field, e)
                    continue
                proposed_this_listing += 1
                counts["changes_proposed"] += 1
                if ver.get("decision") == "auto_applied":
                    db_repo.update_listing_field(lid, field, str(new_val))
                    counts["changes_auto_applied"] += 1
                elif ver.get("decision") == "needs_review":
                    counts["changes_review_queue"] += 1

        update_next_check(lid, changed=True)
        if batch_id is not None:
            db_repo.log_audit(
                action="recheck_changed",
                outcome="ok",
                listing_id=lid,
                batch_id=batch_id,
                details={
                    "proposed": proposed_this_listing,
                    "fields_with_data": [
                        f for f in ("address", "phone", "opening_hours", "name") if record.get(f)
                    ],
                },
            )

    if batch_id is not None:
        db_repo.finalize_batch(
            batch_id,
            {
                "listings_processed": counts["processed"],
                "changes_proposed": counts["changes_proposed"],
                "changes_auto_applied": counts["changes_auto_applied"],
                "changes_review_queue": counts["changes_review_queue"],
            },
            status="done",
        )
        db_repo.log_audit(
            action="recheck_batch_finish",
            outcome="ok",
            batch_id=batch_id,
            details=counts,
        )

    logger.info("recheck batch %s done: %s", batch_id, counts)
    return counts


# ---------------------------------------------------------------------------
# Brain: Phase 6 — Repair
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="api.tasks.repair_pattern_task")
def repair_pattern_task(self, pattern_id: int) -> dict:
    """Repair a decayed pattern (confidence < STALE_THRESHOLD) via LLM.

    Triggered automatically when extract_with_brain flips a pattern to stale.
    Sends the broken regex + up to 3 failing HTML snippets to OpenAI and
    enqueues the repaired pattern as a new candidate (with parent_pattern_id set).
    """
    import db_repo
    from scraper import brain
    from scraper.brain.repairer import repair

    if not brain.is_enabled():
        return {"skipped": "brain_disabled"}

    day = _today_iso()
    budget = brain.daily_budget_eur()
    try:
        today_cost = db_repo.cost_today_eur(day)
    except Exception:
        today_cost = 0.0

    if today_cost >= budget:
        logger.info(
            "Brain budget exhausted (€%.2f/€%.2f) — skipping repair for pattern %d",
            today_cost, budget, pattern_id,
        )
        return {"skipped": "budget_exhausted", "cost_today": today_cost, "budget": budget}

    pat = db_repo.get_pattern(pattern_id)
    if pat is None:
        return {"skipped": "pattern_not_found"}

    try:
        failing_snippets = db_repo.recent_failing_snippets(pattern_id, limit=3)
    except Exception as e:
        logger.warning("repair_pattern_task: failed to fetch snippets for %d: %s", pattern_id, e)
        failing_snippets = []

    result = repair(
        pattern_id=pattern_id,
        field=pat["field"],
        pattern=pat["pattern"],
        pattern_type=pat["pattern_type"],
        failing_snippets=failing_snippets,
    )
    if result is None:
        return {"skipped": "no_result"}

    try:
        db_repo.bump_cost(
            day_iso=day,
            llm_calls=1,
            llm_tokens_in=result.tokens_in,
            llm_tokens_out=result.tokens_out,
            llm_cost_eur=result.llm_cost_eur,
        )
    except Exception as e:
        logger.warning("Failed to record repair LLM cost: %s", e)

    try:
        cand = db_repo.enqueue_candidate(
            field=pat["field"],
            pattern_type=result.pattern_type,
            candidate_pattern=result.pattern,
            language=result.language,
            parent_pattern_id=pattern_id,
            llm_cost_eur=result.llm_cost_eur,
            rationale=result.rationale,
        )
        cand_id = cand.get("id")
        logger.info(
            "Repair enqueued candidate %s for stale pattern %d (field=%s)",
            cand_id, pattern_id, pat["field"],
        )
        return {"candidate_id": cand_id, "pattern_id": pattern_id, "field": pat["field"]}
    except Exception as e:
        logger.error("Failed to enqueue repair candidate for pattern %d: %s", pattern_id, e)
        return {"error": str(e)}
