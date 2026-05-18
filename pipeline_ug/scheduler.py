"""Adaptive scheduler — pick due listings + recompute next_check.

Cadence math (locked in CLAUDE.md §5):
  • change detected     → halve interval
  • no change           → multiply interval by 1.5
  • unreachable         → exponential backoff (×3) up to cap×4
  • paid listings       → multiply final interval by 0.5 (check twice as often)
  • jitter              → ±20% so check loads don't pile up on one day
  • tier caps (.env)    → never let interval exceed cap-for-tier

`next_interval_days` and `tier_of` are pure — unit-testable without DB/network.
`pick_due` and `update_next_check` are thin wrappers around db_repo (Supabase).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Literal

import db_repo
from config import settings

Tier = Literal["paid", "free", "free_low_churn", "new", "unverifiable"]


def cap_for_tier(tier: Tier) -> int:
    return {
        "paid": settings.interval_cap_paid,
        "free": settings.interval_cap_free,
        "free_low_churn": settings.interval_cap_free_low_churn,
        "new": settings.interval_new_listing_days,
        "unverifiable": settings.interval_cap_free,
    }[tier]


def next_interval_days(
    current_interval: float,
    *,
    tier: Tier,
    changed: bool,
    unreachable: bool = False,
    jitter: bool = True,
) -> float:
    """Pure function: returns the new interval (days) after one run."""
    cap = cap_for_tier(tier)
    if unreachable:
        new = current_interval * 3.0
        cap = cap * 4  # let truly dead sites drift longer
    elif changed:
        new = current_interval / 2.0
    else:
        new = current_interval * 1.5

    if tier == "paid":
        new *= 0.5

    new = max(1.0, min(new, float(cap)))

    if jitter:
        new *= random.uniform(0.9, 1.1)
        new = max(1.0, new)

    return round(new, 2)


def tier_of(row: dict) -> Tier:
    if not row.get("is_verifiable", True):
        return "unverifiable"
    if row.get("is_paid"):
        return "paid"
    if not row.get("last_checked"):
        return "new"
    return "free"


def pick_due(limit: int = 50, *, verifiable_only: bool = True) -> list[dict]:
    """Pick the next listings that are due. Paid first, then earliest next_check.

    Thin wrapper — the SQL lives in db_repo so we go through the Supabase client.
    """
    return db_repo.pick_due_listings(limit=limit, verifiable_only=verifiable_only)


def update_next_check(
    listing_id: int,
    *,
    changed: bool,
    unreachable: bool = False,
) -> tuple[float, str]:
    """Recompute interval + next_check after a run.

    Returns (new_interval_days, next_check_iso).
    """
    listing = db_repo.get_listing(listing_id)
    if listing is None:
        raise ValueError(f"listing {listing_id} not found")

    tier = tier_of(listing)
    current = float(listing.get("check_interval_days") or 7.0)
    new_interval = next_interval_days(
        current, tier=tier, changed=changed, unreachable=unreachable
    )
    new_unchanged = 0 if changed else int(listing.get("consecutive_unchanged") or 0) + 1
    next_at = datetime.now(UTC) + timedelta(days=new_interval)
    next_iso = next_at.isoformat()

    db_repo.update_listing_schedule(
        listing_id,
        interval_days=new_interval,
        consecutive_unchanged=new_unchanged,
        next_check_iso=next_iso,
    )
    return new_interval, next_iso
