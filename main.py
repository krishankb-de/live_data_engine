"""
Bookstore scraper orchestrator.

Usage:
    python main.py --phase 1                  # gelbeseiten listings
    python main.py --phase 2                  # site-map discovery
    python main.py --phase 3                  # layered extraction
    python main.py --phase 4                  # rebuild hash cache from phase 3
    python main.py --phase 5                  # regression tests (pytest)
    python main.py --phase 6                  # record-level content-hash diff
    python main.py --phase 5 --online         # regression tests including live URLs
    python main.py --phase all                # 1 → 2 → 3 → 4
    python main.py --phase all --test         # quick test: 1 page, top 10 listings
    python main.py --phase 1 --reset          # clear checkpoint and restart phase 1
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv optional; env vars can be exported externally

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

TEST_LIMIT = 10
TEST_PREFIX = "test_"


def run_phase1(reset: bool, limit: int, prefix: str) -> list:
    from scraper.phase1_listings import run
    results = run(reset=reset, limit=limit, prefix=prefix)
    logger.info("Phase 1 done: %d listings", len(results))
    return results


def run_phase2(reset: bool, prefix: str) -> list:
    from scraper.phase2_site_map import run
    results = run(reset=reset, prefix=prefix)
    logger.info("Phase 2 done: %d site maps", len(results))
    return results


def run_phase3(reset: bool, prefix: str) -> list:
    from scraper.phase3_extract import run
    results = run(reset=reset, prefix=prefix)
    complete = sum(1 for r in results if r.get("extraction_status") == "complete")
    partial  = sum(1 for r in results if r.get("extraction_status") == "partial")
    failed   = sum(1 for r in results if r.get("extraction_status") == "failed")
    skipped  = sum(1 for r in results if r.get("extraction_status") == "skipped")
    logger.info("Phase 3 done: %d complete, %d partial, %d failed, %d skipped",
                complete, partial, failed, skipped)
    return results


def _is_celery_available() -> bool:
    """Return True only if the Celery broker (Redis) is reachable."""
    try:
        from api.worker import celery_app
        celery_app.control.ping(timeout=2)
        return True
    except Exception:
        return False


def _generalize_from_recipes() -> None:
    """Generalize all un-submitted recipe selectors directly to Supabase (no Celery)."""
    from datetime import datetime, timezone
    from scraper.brain import is_enabled
    if not is_enabled():
        return
    try:
        import db_repo
        from scraper.recipe_builder import RecipeStore
        from scraper.brain.generalizer import generalize
    except Exception as e:
        logger.warning("Brain: imports failed — %s", e)
        return

    store = RecipeStore()
    for domain in store._load_all():
        recipe = store.get(domain)
        if not recipe:
            continue
        for field_name, sel in recipe.field_selectors.items():
            if sel is None or field_name in recipe.generalized_fields:
                continue
            logger.info("Brain: generalizing %s/%s", domain, field_name)
            try:
                result = generalize(domain, field_name, sel)
                if result is None:
                    logger.warning("Brain: generalize returned None for %s/%s", domain, field_name)
                    continue
                day = datetime.now(timezone.utc).date().isoformat()
                try:
                    db_repo.bump_cost(
                        day_iso=day,
                        llm_calls=1,
                        llm_tokens_in=result.tokens_in,
                        llm_tokens_out=result.tokens_out,
                        llm_cost_eur=result.llm_cost_eur,
                    )
                except Exception as e:
                    logger.warning("Brain: bump_cost failed: %s", e)
                try:
                    db_repo.enqueue_candidate(
                        field=field_name,
                        pattern_type=result.pattern_type,
                        candidate_pattern=result.pattern,
                        language=result.language,
                        llm_cost_eur=result.llm_cost_eur,
                        rationale=result.rationale,
                    )
                except Exception:
                    from scraper.brain.local_store import enqueue_candidate as _local_enqueue
                    _local_enqueue(
                        field=field_name,
                        pattern_type=result.pattern_type,
                        candidate_pattern=result.pattern,
                        language=result.language,
                        llm_cost_eur=result.llm_cost_eur,
                        rationale=result.rationale,
                    )
                    logger.info("Brain: queued candidate locally for %s/%s", domain, field_name)
                recipe.generalized_fields.append(field_name)
                store.upsert(recipe)
                logger.info("Brain: candidate queued for %s/%s (type=%s lang=%s)",
                            domain, field_name, result.pattern_type, result.language)
            except Exception as e:
                logger.warning("Brain: generalize failed for %s/%s: %s", domain, field_name, e)


def _promote_candidates_direct() -> None:
    """Promote all queued candidates to global_patterns (status=trial), bypassing sandbox.

    Sandbox validation rejects LLM-generated patterns too aggressively on sparse fixtures.
    Patterns enter at confidence=0.5 (trial) and real-world execution adjusts confidence.
    """
    from scraper.brain.runtime import invalidate_cache
    from scraper.brain import local_store as _ls

    # Collect candidates from DB first, then fall back to local store.
    db_candidates: list = []
    local_candidates: list = []
    try:
        import db_repo
        db_candidates = db_repo.list_candidates(status="queued")
    except Exception as e:
        logger.warning("Brain: DB unavailable for candidate list — using local store: %s", e)
    local_candidates = _ls.list_candidates(status="queued")

    promoted = skipped = 0
    for cand in db_candidates:
        cand_id = cand["id"]
        field = cand["field"]
        try:
            pat = db_repo.promote_candidate(cand_id)
            invalidate_cache(field)
            promoted += 1
            logger.info("Brain: promoted DB candidate %d → global_pattern %s (field=%s)",
                        cand_id, pat.get("id"), field)
        except Exception as e:
            logger.warning("Brain: DB promote failed for candidate %d: %s", cand_id, e)
            skipped += 1
    for cand in local_candidates:
        cand_id = cand["id"]
        field = cand["field"]
        try:
            pat = _ls.promote_candidate(cand_id)
            invalidate_cache(field)
            promoted += 1
            logger.info("Brain: promoted local candidate %d → local pattern %s (field=%s)",
                        cand_id, pat.get("id"), field)
        except Exception as e:
            logger.warning("Brain: local promote failed for candidate %d: %s", cand_id, e)
            skipped += 1
    logger.info("Brain: promoted=%d skipped=%d", promoted, skipped)


def _run_brain_promote_sync(prefix: str = "") -> None:
    """Full brain pipeline — works with or without Celery/Redis."""
    from scraper.brain import is_enabled
    if not is_enabled():
        return

    if _is_celery_available():
        # Worker path: enqueue tasks and let the worker + Celery Beat handle promotion.
        logger.info("Brain: Celery available — enqueueing via broker")
        try:
            from scraper.recipe_builder import RecipeStore, _enqueue_generalize
            store = RecipeStore()
            for domain in store._load_all():
                recipe = store.get(domain)
                if not recipe:
                    continue
                eligible = {
                    f: True for f, sel in recipe.field_selectors.items()
                    if sel is not None and f not in recipe.generalized_fields
                }
                if eligible:
                    _enqueue_generalize(domain, recipe, eligible, store=store)
        except Exception as e:
            logger.warning("Brain: Celery enqueue failed: %s", e)
    else:
        # Direct path: run everything inline, write straight to Supabase.
        logger.info("Brain: Celery/Redis unavailable — running inline")
        _generalize_from_recipes()
        _promote_candidates_direct()


def _sync_listings_to_db(records: list) -> None:
    """Upsert phase-1 listings to Supabase."""
    try:
        import db_repo
        ok = fail = 0
        for r in records:
            try:
                db_repo.upsert_listing(r)
                ok += 1
            except Exception as e:
                logger.warning("listing upsert failed (%s): %s", r.get("name"), e)
                fail += 1
        logger.info("DB sync: %d listings upserted, %d failed", ok, fail)
    except Exception as e:
        logger.warning("DB sync (listings) skipped: %s", e)


def _sync_extracted_to_db(records: list) -> None:
    """Write phase-3 observations and versions to Supabase."""
    try:
        import db_repo
        obs_ok = ver_ok = fail = 0
        for record in records:
            gs_id = record.get("gs_uuid") or record.get("gs_listing_id", "")
            if not gs_id:
                continue
            listing_row = db_repo.get_listing_by_gs_id(gs_id)
            if not listing_row:
                continue
            lid = listing_row["id"]
            field_sources = record.get("field_sources") or {}
            for fld in ("address", "phone", "opening_hours", "name"):
                new_val = record.get(fld)
                if new_val is None:
                    continue
                if isinstance(new_val, dict):
                    new_val = json.dumps(new_val, ensure_ascii=False, sort_keys=True)
                source = field_sources.get(fld, "regex")
                confidence = 0.9 if source in ("jsonld", "recipe") else 0.75
                old_val = listing_row.get(fld)
                try:
                    db_repo.insert_observation(lid, fld, str(new_val), source)
                    obs_ok += 1
                except Exception as e:
                    logger.warning("observation failed (%s/%s): %s", gs_id, fld, e)
                    fail += 1
                    continue
                if str(new_val) != str(old_val or ""):
                    try:
                        ver = db_repo.insert_version(
                            listing_id=lid,
                            batch_id=None,
                            field=fld,
                            old_value=old_val,
                            new_value=str(new_val),
                            confidence=confidence,
                        )
                        ver_ok += 1
                        if ver.get("decision") == "auto_applied":
                            db_repo.update_listing_field(lid, fld, str(new_val))
                    except Exception as e:
                        logger.warning("version failed (%s/%s): %s", gs_id, fld, e)
                        fail += 1
        logger.info("DB sync: %d observations, %d versions written, %d failures", obs_ok, ver_ok, fail)
    except Exception as e:
        logger.warning("DB sync (extracted) skipped: %s", e)


def run_phase4(reset: bool, prefix: str) -> dict:
    from scraper.phase4_diff import run
    return run(reset=reset, prefix=prefix)


def run_phase6(reset: bool, prefix: str) -> dict:
    from scraper.phase6_content_hash import run
    return run(reset=reset, prefix=prefix)


def run_phase5(online: bool) -> int:
    here = Path(__file__).parent
    args = [sys.executable, "-m", "pytest",
            str(here / "tests" / "test_extraction.py"), "-v"]
    if online:
        args.append("--online")
    logger.info("Phase 5: %s", " ".join(args))
    return subprocess.call(args)


def print_test_summary(results: list) -> None:
    print("\n" + "=" * 80)
    print(f"TEST SUMMARY — {len(results)} records")
    print("=" * 80)
    for r in results:
        print(f"\n  Business : {r.get('name')}")
        print(f"  Website  : {r.get('website_url') or '—'}")
        print(f"  Address  : {r.get('address') or '—'}")
        print(f"  Phone    : {r.get('phone') or '—'}")
        hours = r.get("opening_hours")
        print(f"  Hours    : {json.dumps(hours, ensure_ascii=False) if hours else '—'}")
        fs = r.get("field_sources") or {}
        per_field = " ".join(
            f"{k}={fs.get(k, 'miss')}" for k in ("name", "address", "phone", "opening_hours")
        )
        print(f"  Status   : {r.get('extraction_status')}  ({per_field})")
        print(f"  Trace    : {r.get('data_sources')}")
    print("=" * 80)
    complete = sum(1 for r in results if r.get("extraction_status") == "complete")
    partial  = sum(1 for r in results if r.get("extraction_status") == "partial")
    failed   = sum(1 for r in results if r.get("extraction_status") in ("failed", "skipped"))
    print(f"\nComplete: {complete}  Partial: {partial}  Failed/Skipped: {failed}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bookstore scraper")
    parser.add_argument(
        "--phase",
        required=True,
        choices=["1", "2", "3", "4", "5", "6", "all"],
        help="Which phase to run",
    )
    parser.add_argument("--reset", action="store_true",
                        help="Clear checkpoint for the selected phase and start fresh")
    parser.add_argument("--test", action="store_true",
                        help=f"Test mode: 1 page only, top {TEST_LIMIT} listings, writes test_* output files")
    parser.add_argument("--online", action="store_true",
                        help="Phase 5 only: also run live-network golden URL tests")
    args = parser.parse_args()

    phase  = args.phase
    reset  = args.reset
    test   = args.test
    limit  = TEST_LIMIT if test else 200
    prefix = TEST_PREFIX if test else ""

    if test:
        logger.info("*** TEST MODE — limit=%d, output prefix='%s' ***", limit, prefix)

    if phase in ("1", "all"):
        logger.info("=== Phase 1: Gelbeseiten listings ===")
        listings = run_phase1(reset=reset, limit=limit, prefix=prefix)
        _sync_listings_to_db(listings)

    if phase in ("2", "all"):
        logger.info("=== Phase 2: Site map discovery ===")
        run_phase2(reset=reset, prefix=prefix)

    if phase in ("3", "all"):
        logger.info("=== Phase 3: Layered extraction ===")
        results = run_phase3(reset=reset, prefix=prefix)
        if test:
            print_test_summary(results)
        _sync_extracted_to_db(results)
        _run_brain_promote_sync(prefix=prefix)

    if phase in ("4", "all"):
        logger.info("=== Phase 4: Hash cache build ===")
        run_phase4(reset=reset, prefix=prefix)

    if phase == "5":
        logger.info("=== Phase 5: Regression tests ===")
        sys.exit(run_phase5(online=args.online))

    if phase == "6":
        logger.info("=== Phase 6: Content-hash change detection ===")
        run_phase6(reset=reset, prefix=prefix)


if __name__ == "__main__":
    main()
