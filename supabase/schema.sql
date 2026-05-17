-- Supabase / Postgres schema. Port of db.py SCHEMA_SQL with:
--   * BIGSERIAL primary keys
--   * JSONB instead of TEXT for structured columns
--   * now() defaults
--   * recipes.field_selectors + recipes.last_hash added for LLM-recipe flow
--
-- Run this in Supabase SQL editor (or psql) once. Idempotent via IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS listings (
    id                       BIGSERIAL PRIMARY KEY,
    gs_listing_id            TEXT UNIQUE NOT NULL,
    name                     TEXT NOT NULL,
    category                 TEXT,
    address                  TEXT,
    phone                    TEXT,
    opening_hours            TEXT,
    website_url              TEXT,
    latitude                 DOUBLE PRECISION,
    longitude                DOUBLE PRECISION,
    is_paid                  BOOLEAN NOT NULL DEFAULT FALSE,
    is_verifiable            BOOLEAN NOT NULL DEFAULT TRUE,
    unverifiable_reason      TEXT,
    last_gs_modified         TIMESTAMPTZ,
    last_checked             TIMESTAMPTZ,
    next_check               TIMESTAMPTZ,
    check_interval_days      DOUBLE PRECISION DEFAULT 7,
    consecutive_unchanged    INTEGER DEFAULT 0,
    created_at               TIMESTAMPTZ DEFAULT now(),
    updated_at               TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_listings_next_check ON listings (next_check);
CREATE INDEX IF NOT EXISTS idx_listings_priority   ON listings (is_paid, next_check);

CREATE TABLE IF NOT EXISTS recipes (
    id              BIGSERIAL PRIMARY KEY,
    domain          TEXT UNIQUE NOT NULL,
    platform        TEXT,
    recipe_version  INTEGER DEFAULT 1,
    pages           JSONB NOT NULL,                  -- {"imprint": "...", "contact": "..."}
    field_selectors JSONB,                           -- {"phone": {"page":"contact","css":".tel"}, ...}
    negative_cache  JSONB,                           -- ["hours"]  fields LLM confirmed absent
    last_hash       TEXT,                            -- content hash at last successful build, for staleness check
    status          TEXT DEFAULT 'active',           -- 'active' | 'stale' | 'failed'
    learned_at      TIMESTAMPTZ DEFAULT now(),
    last_used_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS batches (
    id                       BIGSERIAL PRIMARY KEY,
    started_at               TIMESTAMPTZ DEFAULT now(),
    finished_at              TIMESTAMPTZ,
    listings_processed       INTEGER DEFAULT 0,
    changes_proposed         INTEGER DEFAULT 0,
    changes_auto_applied     INTEGER DEFAULT 0,
    changes_review_queue     INTEGER DEFAULT 0,
    changes_discarded        INTEGER DEFAULT 0,
    llm_calls                INTEGER DEFAULT 0,
    cost_eur                 DOUBLE PRECISION DEFAULT 0,
    anomaly_flagged          BOOLEAN DEFAULT FALSE,
    anomaly_reason           TEXT,
    status                   TEXT DEFAULT 'in_progress'
);

CREATE TABLE IF NOT EXISTS field_observations (
    id                    BIGSERIAL PRIMARY KEY,
    listing_id            BIGINT NOT NULL REFERENCES listings(id),
    field                 TEXT NOT NULL,
    value                 TEXT,
    is_present            BOOLEAN NOT NULL DEFAULT TRUE,
    source                TEXT NOT NULL,             -- 'regex' | 'jsonld' | 'recipe' | 'llm'
    source_url            TEXT,
    source_page           TEXT,
    extraction_confidence DOUBLE PRECISION,
    observed_at           TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_obs_listing_field ON field_observations (listing_id, field, observed_at DESC);

CREATE TABLE IF NOT EXISTS versions (
    id                BIGSERIAL PRIMARY KEY,
    listing_id        BIGINT NOT NULL REFERENCES listings(id),
    batch_id          BIGINT REFERENCES batches(id),
    field             TEXT NOT NULL,
    old_value         TEXT,
    new_value         TEXT,
    intent_confidence DOUBLE PRECISION,
    decision          TEXT,                          -- 'auto_applied' | 'needs_review' | 'needs_recipe_rebuild' | 'discarded'
    signals           JSONB,
    reasoning         TEXT,
    applied_at        TIMESTAMPTZ,
    applied_by        TEXT,
    reviewed_at       TIMESTAMPTZ,
    reviewed_by       TEXT,
    created_at        TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_versions_listing ON versions (listing_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_versions_pending ON versions (decision);

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    listing_id    BIGINT REFERENCES listings(id),
    batch_id      BIGINT REFERENCES batches(id),
    action        TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    details       JSONB,
    cost_eur      DOUBLE PRECISION DEFAULT 0,
    duration_ms   INTEGER,
    ts            TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_listing_ts ON audit_log (listing_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_batch      ON audit_log (batch_id);

CREATE TABLE IF NOT EXISTS cost_log (
    day                DATE PRIMARY KEY,
    llm_calls          INTEGER DEFAULT 0,
    llm_tokens_in      INTEGER DEFAULT 0,
    llm_tokens_out     INTEGER DEFAULT 0,
    llm_cost_eur       DOUBLE PRECISION DEFAULT 0,
    http_requests      INTEGER DEFAULT 0,
    listings_processed INTEGER DEFAULT 0
);
