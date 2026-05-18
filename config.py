"""Typed config loaded from environment variables.

Single source of truth for tunables used by the recheck pipeline (pipeline_ug).
`.env` is loaded upstream by main.py and api/worker.py via python-dotenv, so by
the time anything imports `settings` the values are already in os.environ.

Keep this module dependency-free — it's imported by pipeline_ug code that runs
inside Celery workers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw not in (None, "") else default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Scheduler caps (days) — see pipeline_ug/scheduler.py cap_for_tier
    interval_cap_paid: int
    interval_cap_free: int
    interval_cap_free_low_churn: int
    interval_new_listing_days: int

    # Politeness / HTTP
    per_domain_rate_limit_seconds: float
    http_timeout_seconds: int
    user_agent: str

    # Recheck batch
    recheck_batch_size: int

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


@lru_cache
def get_settings() -> Settings:
    return Settings(
        interval_cap_paid=_env_int("INTERVAL_CAP_PAID", 7),
        interval_cap_free=_env_int("INTERVAL_CAP_FREE", 30),
        interval_cap_free_low_churn=_env_int("INTERVAL_CAP_FREE_LOW_CHURN", 60),
        interval_new_listing_days=_env_int("INTERVAL_NEW_LISTING_DAYS", 7),
        per_domain_rate_limit_seconds=_env_float("PER_DOMAIN_RATE_LIMIT_SECONDS", 2.0),
        http_timeout_seconds=_env_int("HTTP_TIMEOUT_SECONDS", 15),
        user_agent=_env_str("USER_AGENT", "LiveDataEngine/0.1"),
        recheck_batch_size=_env_int("RECHECK_BATCH_SIZE", 50),
    )


settings = get_settings()
