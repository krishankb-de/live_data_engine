"""Async SQLite (aiosqlite). WAL mode + foreign keys. Schema from CLAUDE.md §9."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from .config import settings

# Schema — verbatim from CLAUDE.md §9. All Postgres-compatible.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS listings (
    id                       INTEGER PRIMARY KEY,
    gs_listing_id            TEXT  NOT NULL,
    name                     TEXT NOT NULL,
    category                 TEXT,
    address                  TEXT,
    phone                    TEXT,
    opening_hours            TEXT,
    website_url              TEXT,
    latitude                 REAL,
    hash_value                TEXT,
    longitude                REAL,
    is_paid                  INTEGER NOT NULL DEFAULT 0,
    is_verifiable            INTEGER NOT NULL DEFAULT 1,
    unverifiable_reason      TEXT,
    last_gs_modified         TEXT,
    last_checked             TEXT,
    next_check               TEXT,
    regex_id                TEXT,
    versions            INTEGER DEFAULT 0,
    check_interval_days      REAL DEFAULT 7,
    consecutive_unchanged    INTEGER DEFAULT 0,
    created_at               TEXT DEFAULT (datetime('now')),
    updated_at               TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_listings_next_check ON listings (next_check);
CREATE INDEX IF NOT EXISTS idx_listings_priority   ON listings (is_paid, next_check);

CREATE TABLE IF NOT EXISTS recipes (
    id              INTEGER PRIMARY KEY,
    domain          TEXT UNIQUE NOT NULL,
    platform        TEXT,
    recipe_version  INTEGER DEFAULT 1,
    pages           TEXT NOT NULL,
    negative_cache  TEXT,
    status          TEXT DEFAULT 'active',
    learned_at      TEXT DEFAULT (datetime('now')),
    last_used_at    TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id                       INTEGER PRIMARY KEY,
    started_at               TEXT DEFAULT (datetime('now')),
    finished_at              TEXT,
    listings_processed       INTEGER DEFAULT 0,
    changes_proposed         INTEGER DEFAULT 0,
    changes_auto_applied     INTEGER DEFAULT 0,
    changes_review_queue     INTEGER DEFAULT 0,
    changes_discarded        INTEGER DEFAULT 0,
    llm_calls                INTEGER DEFAULT 0,
    cost_eur                 REAL DEFAULT 0,
    anomaly_flagged          INTEGER DEFAULT 0,
    anomaly_reason           TEXT,
    status                   TEXT DEFAULT 'in_progress'
);

CREATE TABLE IF NOT EXISTS field_observations (
    id                    INTEGER PRIMARY KEY,
    listing_id            INTEGER NOT NULL REFERENCES listings(id),
    field                 TEXT NOT NULL,
    value                 TEXT,
    is_present            INTEGER NOT NULL DEFAULT 1,
    source                TEXT NOT NULL,
    source_url            TEXT,
    source_page           TEXT,
    extraction_confidence REAL,
    pattern_id            INTEGER REFERENCES global_patterns(id),
    observed_at           TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_obs_listing_field ON field_observations (listing_id, field, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_obs_pattern       ON field_observations (pattern_id);

CREATE TABLE IF NOT EXISTS versions (
    id                INTEGER PRIMARY KEY,
    listing_id        INTEGER NOT NULL REFERENCES listings(id),
    batch_id          INTEGER REFERENCES batches(id),
    field             TEXT NOT NULL,
    old_value         TEXT,
    new_value         TEXT,
    intent_confidence REAL,
    decision          TEXT,
    signals           TEXT,
    reasoning         TEXT,
    applied_at        TEXT,
    applied_by        TEXT,
    reviewed_at       TEXT,
    reviewed_by       TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_versions_listing ON versions (listing_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_versions_pending ON versions (decision);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY,
    listing_id    INTEGER REFERENCES listings(id),
    batch_id      INTEGER REFERENCES batches(id),
    action        TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    details       TEXT,
    cost_eur      REAL DEFAULT 0,
    duration_ms   INTEGER,
    ts            TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_listing_ts ON audit_log (listing_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_batch      ON audit_log (batch_id);

CREATE TABLE IF NOT EXISTS cost_log (
    day                TEXT PRIMARY KEY,
    llm_calls          INTEGER DEFAULT 0,
    llm_tokens_in      INTEGER DEFAULT 0,
    llm_tokens_out     INTEGER DEFAULT 0,
    llm_cost_eur       REAL DEFAULT 0,
    http_requests      INTEGER DEFAULT 0,
    listings_processed INTEGER DEFAULT 0
);


CREATE TABLE IF NOT EXISTS regexes (
    id              INTEGER PRIMARY KEY,
    address_pattern TEXT NOT NULL,
    hours_pattern   TEXT NOT NULL,
    jsonold_pattern TEXT NOT NULL,
    name_pattern    TEXT NOT NULL,
    phone_pattern   TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Self-learning brain (Phase 2). See plan: read-the-compelte-codebase-abstract-haven.md
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS global_patterns (
    id                INTEGER PRIMARY KEY,
    field             TEXT NOT NULL,                  -- 'phone' | 'address' | 'opening_hours' | 'name'
    pattern_type      TEXT NOT NULL,                  -- 'regex' | 'css'
    pattern           TEXT NOT NULL,
    language          TEXT NOT NULL DEFAULT 'any',    -- 'de' | 'en' | 'fr' | 'any'
    confidence_score  REAL NOT NULL DEFAULT 0.5,
    success_count     INTEGER NOT NULL DEFAULT 0,
    failure_count     INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'trial',  -- 'trial' | 'active' | 'stale' | 'disabled'
    origin_domain     TEXT,
    parent_recipe_id  INTEGER REFERENCES recipes(id),
    rationale         TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    last_used_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_patterns_field_status ON global_patterns (field, status, confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_patterns_language     ON global_patterns (language);

CREATE TABLE IF NOT EXISTS sandbox_fixtures (
    id              INTEGER PRIMARY KEY,
    source_url      TEXT NOT NULL,
    html_path       TEXT NOT NULL,                    -- relative to output/html_cache/
    field           TEXT NOT NULL,
    expected_value  TEXT,                             -- NULL = negative fixture (field absent)
    language        TEXT NOT NULL DEFAULT 'any',
    captured_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fixtures_field_lang ON sandbox_fixtures (field, language);

CREATE TABLE IF NOT EXISTS pattern_executions (
    id                INTEGER PRIMARY KEY,
    pattern_id        INTEGER NOT NULL REFERENCES global_patterns(id),
    listing_id        INTEGER REFERENCES listings(id),
    batch_id          INTEGER REFERENCES batches(id),
    outcome           TEXT NOT NULL,                  -- 'hit' | 'miss' | 'invalid'
    extracted_value   TEXT,
    validator_passed  INTEGER,                        -- nullable boolean
    failing_snippet   TEXT,                           -- captured on invalid outcomes for repair
    ts                TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_exec_pattern_ts ON pattern_executions (pattern_id, ts DESC);

CREATE TABLE IF NOT EXISTS candidate_queue (
    id                  INTEGER PRIMARY KEY,
    parent_recipe_id    INTEGER REFERENCES recipes(id),
    parent_pattern_id   INTEGER REFERENCES global_patterns(id),     -- set for repair candidates
    field               TEXT NOT NULL,
    pattern_type        TEXT NOT NULL,                              -- 'regex' | 'css'
    candidate_pattern   TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'any',
    status              TEXT NOT NULL DEFAULT 'queued',             -- 'queued' | 'validating' | 'promoted' | 'rejected'
    sandbox_precision   REAL,
    sandbox_recall      REAL,
    sandbox_details     TEXT,                                       -- JSON: sample_failures, counts
    llm_cost_eur        REAL DEFAULT 0,
    rationale           TEXT,
    ts                  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_queue (status, ts);
"""

# WAL = single-writer + many-reader concurrency (needed for batch + UI activity feed).
# busy_timeout=10000 ms means a blocked writer waits up to 10s for the lock instead
# of getting an immediate "database is locked" error.
PRAGMAS_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;
PRAGMA busy_timeout=10000;
"""


async def init_db() -> None:
    """Create DB file (if missing), set pragmas, ensure all tables/indexes exist."""
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.db_path, timeout=15) as conn:
        await conn.executescript(PRAGMAS_SQL)
        await conn.executescript(SCHEMA_SQL)
        # Brain migration: add pattern_id to pre-existing field_observations tables.
        async with conn.execute("PRAGMA table_info(field_observations)") as cur:
            cols = {row[1] async for row in cur}
        if "pattern_id" not in cols:
            await conn.execute(
                "ALTER TABLE field_observations ADD COLUMN pattern_id INTEGER REFERENCES global_patterns(id)"
            )
        await conn.commit()


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager yielding a connection with `Row` factory + FK on."""
    async with aiosqlite.connect(settings.db_path, timeout=15) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=10000")
        yield conn
