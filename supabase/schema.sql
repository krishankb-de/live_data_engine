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

-- ---------------------------------------------------------------------------
-- Self-learning brain (Phase 2). See plan: read-the-compelte-codebase-abstract-haven.md
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS global_patterns (
    id                BIGSERIAL PRIMARY KEY,
    field             TEXT NOT NULL,                  -- 'phone' | 'address' | 'opening_hours' | 'name'
    pattern_type      TEXT NOT NULL,                  -- 'regex' | 'css'
    pattern           TEXT NOT NULL,
    language          TEXT NOT NULL DEFAULT 'any',    -- 'de' | 'en' | 'fr' | 'any'
    confidence_score  DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    success_count     INTEGER NOT NULL DEFAULT 0,
    failure_count     INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'trial',  -- 'trial' | 'active' | 'stale' | 'disabled'
    origin_domain     TEXT,
    parent_recipe_id  BIGINT REFERENCES recipes(id),
    rationale         TEXT,
    created_at        TIMESTAMPTZ DEFAULT now(),
    last_used_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_patterns_field_status ON global_patterns (field, status, confidence_score DESC);
CREATE INDEX IF NOT EXISTS idx_patterns_language     ON global_patterns (language);

CREATE TABLE IF NOT EXISTS sandbox_fixtures (
    id              BIGSERIAL PRIMARY KEY,
    source_url      TEXT NOT NULL,
    html_path       TEXT NOT NULL,                    -- relative to output/html_cache/
    field           TEXT NOT NULL,
    expected_value  TEXT,                             -- NULL = negative fixture (field absent)
    language        TEXT NOT NULL DEFAULT 'any',
    captured_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fixtures_field_lang ON sandbox_fixtures (field, language);

CREATE TABLE IF NOT EXISTS pattern_executions (
    id                BIGSERIAL PRIMARY KEY,
    pattern_id        BIGINT NOT NULL REFERENCES global_patterns(id),
    listing_id        BIGINT REFERENCES listings(id),
    batch_id          BIGINT REFERENCES batches(id),
    outcome           TEXT NOT NULL,                  -- 'hit' | 'miss' | 'invalid'
    extracted_value   TEXT,
    validator_passed  BOOLEAN,
    failing_snippet   TEXT,
    ts                TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_exec_pattern_ts ON pattern_executions (pattern_id, ts DESC);

CREATE TABLE IF NOT EXISTS candidate_queue (
    id                  BIGSERIAL PRIMARY KEY,
    parent_recipe_id    BIGINT REFERENCES recipes(id),
    parent_pattern_id   BIGINT REFERENCES global_patterns(id),
    field               TEXT NOT NULL,
    pattern_type        TEXT NOT NULL,                              -- 'regex' | 'css'
    candidate_pattern   TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'any',
    status              TEXT NOT NULL DEFAULT 'queued',             -- 'queued' | 'validating' | 'promoted' | 'rejected'
    sandbox_precision   DOUBLE PRECISION,
    sandbox_recall      DOUBLE PRECISION,
    sandbox_details     JSONB,
    llm_cost_eur        DOUBLE PRECISION DEFAULT 0,
    rationale           TEXT,
    ts                  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_queue (status, ts);

-- Add pattern_id provenance to field_observations (safe on existing tables).
ALTER TABLE field_observations ADD COLUMN IF NOT EXISTS pattern_id BIGINT REFERENCES global_patterns(id);
CREATE INDEX IF NOT EXISTS idx_obs_pattern ON field_observations (pattern_id);
