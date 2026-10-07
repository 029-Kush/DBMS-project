-- Phase 2 is additive: preserve the Phase 1 corpus, canaries, and history.

ALTER TABLE query_log
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'user';

ALTER TABLE health_snapshots
    ADD COLUMN IF NOT EXISTS p95_nn_distance FLOAT8,
    ADD COLUMN IF NOT EXISTS distance_shift_pct FLOAT8,
    ADD COLUMN IF NOT EXISTS index_exists BOOLEAN,
    ADD COLUMN IF NOT EXISTS index_used BOOLEAN,
    ADD COLUMN IF NOT EXISTS health_status TEXT,
    ADD COLUMN IF NOT EXISTS issues TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS query_log_queried_at_idx
    ON query_log (queried_at DESC);

CREATE INDEX IF NOT EXISTS health_snapshots_recorded_at_idx
    ON health_snapshots (recorded_at DESC);
