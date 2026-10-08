-- Phase 4 self-healing audit trail. Additive and safe to run repeatedly.

CREATE TABLE IF NOT EXISTS maintenance_events (
    id                  BIGSERIAL PRIMARY KEY,
    issues              TEXT[] NOT NULL DEFAULT '{}',
    diagnosis           TEXT NOT NULL,
    actions             TEXT[] NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'RUNNING'
                        CHECK (status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED',
                                          'ROLLED_BACK', 'ESCALATED')),
    parameters          JSONB NOT NULL DEFAULT '{}',
    affected_rows       INT NOT NULL DEFAULT 0,
    pre_snapshot_id     BIGINT REFERENCES health_snapshots(id),
    post_snapshot_id    BIGINT REFERENCES health_snapshots(id),
    fault_run_id        BIGINT REFERENCES fault_runs(id),
    error_message       TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ
);

-- One repair at a time keeps rollback unambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS one_running_maintenance
    ON maintenance_events ((status))
    WHERE status = 'RUNNING';

CREATE INDEX IF NOT EXISTS maintenance_events_started_at_idx
    ON maintenance_events (started_at DESC);

-- The healer's own rollback data. Deliberately separate from
-- fault_embedding_backups: the healer must never read the fault lab's
-- originals, or "healing" would just be restoring.
CREATE TABLE IF NOT EXISTS embedding_backups (
    event_id                BIGINT NOT NULL REFERENCES maintenance_events(id) ON DELETE CASCADE,
    document_id             BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding               VECTOR(64) NOT NULL,
    embedding_model_version TEXT NOT NULL,
    -- What the healer wrote. Rollback only reverts rows that still hold this
    -- value, so it never overwrites a user's later update.
    healed_embedding        VECTOR(64),
    PRIMARY KEY (event_id, document_id)
);

-- A fault that the healer repaired is HEALED, not RESTORED (nobody replayed
-- the fault lab's backup). HEALED is not ACTIVE, so it frees the one-active slot.
ALTER TABLE fault_runs DROP CONSTRAINT IF EXISTS fault_runs_status_check;
ALTER TABLE fault_runs ADD CONSTRAINT fault_runs_status_check
    CHECK (status IN ('ACTIVE', 'RESTORED', 'FAILED', 'HEALED'));

-- Extra health signals: ANN-vs-exact recall (index health, immune to inserts),
-- sentinel re-embedding mismatch (silent vector corruption), and the id horizon
-- the frozen canaries were built on (so legitimate inserts do not look like drift).
ALTER TABLE health_snapshots
    ADD COLUMN IF NOT EXISTS ann_recall_at_k FLOAT8,
    ADD COLUMN IF NOT EXISTS sentinel_mismatch_pct FLOAT8;
ALTER TABLE canary_set
    ADD COLUMN IF NOT EXISTS corpus_max_id BIGINT;
