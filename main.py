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
        print(f"  Status   : {r.get('extraction_status')}  (sources: {r.get('data_sources')})")
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
        run_phase1(reset=reset, limit=limit, prefix=prefix)

    if phase in ("2", "all"):
        logger.info("=== Phase 2: Site map discovery ===")
        run_phase2(reset=reset, prefix=prefix)

    if phase in ("3", "all"):
        logger.info("=== Phase 3: Layered extraction ===")
        results = run_phase3(reset=reset, prefix=prefix)
        if test:
            print_test_summary(results)

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
