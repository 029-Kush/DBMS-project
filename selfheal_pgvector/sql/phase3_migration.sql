-- Phase 3 fault-laboratory metadata. Additive and safe to run repeatedly.

CREATE TABLE IF NOT EXISTS fault_runs (
    id                          BIGSERIAL PRIMARY KEY,
    fault_type                  TEXT NOT NULL,
    parameters                  JSONB NOT NULL DEFAULT '{}',
    status                      TEXT NOT NULL DEFAULT 'ACTIVE'
                                CHECK (status IN ('ACTIVE', 'RESTORED', 'FAILED')),
    affected_rows               INT NOT NULL DEFAULT 0,
    pre_fault_snapshot_id       BIGINT REFERENCES health_snapshots(id),
    degraded_snapshot_id        BIGINT REFERENCES health_snapshots(id),
    recovered_snapshot_id       BIGINT REFERENCES health_snapshots(id),
    started_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    restored_at                 TIMESTAMPTZ,
    error_message               TEXT
);

-- Only one active experiment at a time keeps restoration unambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_fault_run
    ON fault_runs ((status))
    WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS fault_embedding_backups (
    run_id                      BIGINT NOT NULL REFERENCES fault_runs(id) ON DELETE CASCADE,
    document_id                 BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding                   VECTOR(64) NOT NULL,
    embedding_model_version     TEXT NOT NULL,
    PRIMARY KEY (run_id, document_id)
);

CREATE INDEX IF NOT EXISTS fault_runs_started_at_idx
    ON fault_runs (started_at DESC);
