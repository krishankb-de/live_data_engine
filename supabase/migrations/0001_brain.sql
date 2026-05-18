-- Brain (Phase 2) migration. Idempotent — safe to re-run.
-- Paste into Supabase Dashboard → SQL Editor → Run.
-- Mirrors the same block at the bottom of supabase/schema.sql.

CREATE TABLE IF NOT EXISTS global_patterns (
    id                BIGSERIAL PRIMARY KEY,
    field             TEXT NOT NULL,
    pattern_type      TEXT NOT NULL,
    pattern           TEXT NOT NULL,
    language          TEXT NOT NULL DEFAULT 'any',
    confidence_score  DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    success_count     INTEGER NOT NULL DEFAULT 0,
    failure_count     INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'trial',
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
    html_path       TEXT NOT NULL,
    field           TEXT NOT NULL,
    expected_value  TEXT,
    language        TEXT NOT NULL DEFAULT 'any',
    captured_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fixtures_field_lang ON sandbox_fixtures (field, language);

CREATE TABLE IF NOT EXISTS pattern_executions (
    id                BIGSERIAL PRIMARY KEY,
    pattern_id        BIGINT NOT NULL REFERENCES global_patterns(id),
    listing_id        BIGINT REFERENCES listings(id),
    batch_id          BIGINT REFERENCES batches(id),
    outcome           TEXT NOT NULL,
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
    pattern_type        TEXT NOT NULL,
    candidate_pattern   TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'any',
    status              TEXT NOT NULL DEFAULT 'queued',
    sandbox_precision   DOUBLE PRECISION,
    sandbox_recall      DOUBLE PRECISION,
    sandbox_details     JSONB,
    llm_cost_eur        DOUBLE PRECISION DEFAULT 0,
    rationale           TEXT,
    ts                  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_queue (status, ts);

ALTER TABLE field_observations ADD COLUMN IF NOT EXISTS pattern_id BIGINT REFERENCES global_patterns(id);
CREATE INDEX IF NOT EXISTS idx_obs_pattern ON field_observations (pattern_id);

-- After running this, refresh PostgREST schema cache:
NOTIFY pgrst, 'reload schema';
