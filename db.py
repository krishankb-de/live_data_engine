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
    observed_at           TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_obs_listing_field ON field_observations (listing_id, field, observed_at DESC);

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


create table if not exists regexes (
    id              integer primary key,
    address_pattern         text not null,
    hours_pattern             text not null,
    jsonold_pattern            text not null,
    name_pattern               text not null,
    phone_pattern              text not null,
    description     text,
    created_at      text default (datetime('now'))
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
        await conn.commit()


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager yielding a connection with `Row` factory + FK on."""
    async with aiosqlite.connect(settings.db_path, timeout=15) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=10000")
        yield conn
