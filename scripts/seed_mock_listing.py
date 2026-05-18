#!/usr/bin/env python3
"""Seed mock listings for the test suite.

Modes
-----
default           Upsert single permanent listing  gs_listing_id=mock-5174
--count N         Upsert N permanent pool rows     mock-5174-001 … mock-5174-NNN
--ephemeral [N]   Upsert N ephemeral rows          test_mock_<ts>_<i>   (default N=1)

All rows share website_url=http://localhost:5174/.
Baseline field values from mock-source-site/data/business.json.
Idempotent — uses db_repo.upsert_listing.

Usage
-----
python scripts/seed_mock_listing.py
python scripts/seed_mock_listing.py --count 200
python scripts/seed_mock_listing.py --ephemeral 5
python scripts/seed_mock_listing.py --count 200 --ephemeral 3
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import db_repo

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

MOCK_SITE_URL = "http://localhost:5174/"
BUSINESS_JSON = ROOT / "mock-source-site" / "data" / "business.json"


def _load_baseline() -> dict:
    with open(BUSINESS_JSON) as f:
        return json.load(f)


def _base_record(baseline: dict) -> dict:
    return {
        "name": baseline["name"],
        "phone": baseline.get("phone"),
        "opening_hours": baseline.get("opening_hours"),
        "address": baseline.get("address"),
        "website_url": MOCK_SITE_URL,
        "is_paid": False,
        "is_verifiable": True,
    }


def seed_permanent(baseline: dict) -> dict:
    record = {**_base_record(baseline), "gs_listing_id": "mock-5174"}
    result = db_repo.upsert_listing(record)
    log.info("permanent  mock-5174 → id=%s", result.get("id"))
    return result


def seed_pool(baseline: dict, count: int) -> list[dict]:
    results = []
    for i in range(1, count + 1):
        gs_id = f"mock-5174-{i:03d}"
        record = {**_base_record(baseline), "gs_listing_id": gs_id}
        result = db_repo.upsert_listing(record)
        results.append(result)
        if i % 20 == 0:
            log.info("pool       %s/%s upserted", i, count)
    log.info("pool       done — %s rows", count)
    return results


def seed_ephemeral(baseline: dict, count: int) -> list[dict]:
    ts = int(time.time())
    results = []
    for i in range(1, count + 1):
        gs_id = f"test_mock_{ts}_{i}"
        record = {**_base_record(baseline), "gs_listing_id": gs_id}
        result = db_repo.upsert_listing(record)
        log.info("ephemeral  %s → id=%s", gs_id, result.get("id"))
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=0, metavar="N",
                        help="upsert N permanent pool rows (mock-5174-001 … mock-5174-NNN)")
    parser.add_argument("--ephemeral", type=int, nargs="?", const=1, default=0, metavar="N",
                        help="upsert N ephemeral rows (default 1)")
    args = parser.parse_args()

    baseline = _load_baseline()

    # Always seed the single permanent anchor row
    seed_permanent(baseline)

    if args.count:
        seed_pool(baseline, args.count)

    if args.ephemeral:
        seed_ephemeral(baseline, args.ephemeral)

    log.info("seed complete")


if __name__ == "__main__":
    main()
