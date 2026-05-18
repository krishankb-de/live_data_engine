"""Self-learning brain (Phase 2+). See plan: read-the-compelte-codebase-abstract-haven.md.

Public surface stays intentionally small — every module under `brain/` is internal
to the brain subsystem. Callers in `parsers/`, `phase3_extract.py`, and
`recipe_builder.py` import only from this package root.
"""
from __future__ import annotations

import os
from typing import Final

FIELDS: Final[tuple[str, ...]] = ("phone", "address", "opening_hours", "name")

# Per-field promotion thresholds (precision / recall). Negative-fixture hits = 0 required.
PROMOTION_THRESHOLDS: Final[dict[str, tuple[float, float]]] = {
    "phone":         (0.97, 0.70),
    "address":       (0.95, 0.60),
    "opening_hours": (0.90, 0.50),
    "name":          (0.85, 0.50),
}

# Confidence dynamics.
CONFIDENCE_SUCCESS_DELTA: Final[float] = 0.01
CONFIDENCE_FAILURE_DELTA: Final[float] = 0.10
STALE_THRESHOLD: Final[float] = 0.20
TRIAL_TO_ACTIVE_MIN_SUCCESS: Final[int] = 10
TRIAL_TO_ACTIVE_MIN_CONFIDENCE: Final[float] = 0.70
TRIAL_TO_ACTIVE_MAX_FAILURE_RATIO: Final[float] = 0.05


def is_enabled() -> bool:
    """Feature flag. Off by default. Read fresh each call so tests can flip env."""
    return os.getenv("BRAIN_ENABLED", "").lower() in ("1", "true", "yes")


def daily_budget_eur() -> float:
    """Hard ceiling on LLM spend per UTC day for Generalizer + Repair tasks."""
    try:
        return float(os.getenv("BRAIN_DAILY_BUDGET_EUR", "10"))
    except ValueError:
        return 10.0
