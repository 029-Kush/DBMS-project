-- Phase 1 schema: documents table + supporting metadata for monitoring.
-- Embedding dimension is 64 to keep the toy corpus fast; bump EMBED_DIM
-- consistently in scripts/config.py if you change it here.

CREATE EXTENSION IF NOT EXISTS vector;

DROP TABLE IF EXISTS documents CASCADE;
CREATE TABLE documents (
    id                      BIGSERIAL PRIMARY KEY,
    category                TEXT NOT NULL,
    body                    TEXT NOT NULL,
    embedding               VECTOR(64) NOT NULL,
    embedding_model_version TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_reindexed_at       TIMESTAMPTZ
);

-- HNSW index for approximate nearest-neighbor search (cosine distance).
CREATE INDEX documents_embedding_hnsw
    ON documents
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Every query issued against the DB, for latency + drift monitoring later.
DROP TABLE IF EXISTS query_log CASCADE;
CREATE TABLE query_log (
    id              BIGSERIAL PRIMARY KEY,
    query_text      TEXT,
    query_embedding VECTOR(64),
    top_k           INT,
    result_ids      BIGINT[],
    result_distances FLOAT8[],
    latency_ms      FLOAT8,
    source          TEXT NOT NULL DEFAULT 'user',
    queried_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX query_log_queried_at_idx ON query_log (queried_at DESC);

-- Fixed canary set: query -> the doc ids we expect back, decided at
-- baseline time when the corpus and embedding model are known-good.
DROP TABLE IF EXISTS canary_set CASCADE;
CREATE TABLE canary_set (
    id                  BIGSERIAL PRIMARY KEY,
    query_text          TEXT NOT NULL,
    expected_doc_ids    BIGINT[] NOT NULL,
    category            TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per health-check run, so we can plot recall/latency over time
-- and see drift/staleness injections show up as a visible dip.
DROP TABLE IF EXISTS health_snapshots CASCADE;
CREATE TABLE health_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    recall_at_k         FLOAT8,
    avg_latency_ms      FLOAT8,
    p95_latency_ms      FLOAT8,
    mean_nn_distance    FLOAT8,
    p95_nn_distance     FLOAT8,
    distance_shift_pct  FLOAT8,
    version_skew_pct    FLOAT8,
    dead_tuple_pct      FLOAT8,
    index_exists        BOOLEAN,
    index_used          BOOLEAN,
    health_status       TEXT,
    issues              TEXT[] NOT NULL DEFAULT '{}',
    note                TEXT,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX health_snapshots_recorded_at_idx
    ON health_snapshots (recorded_at DESC);

-- Phase 3: reversible fault-experiment audit trail and exact vector backups.
CREATE TABLE fault_runs (
    id                          BIGSERIAL PRIMARY KEY,
    fault_type                  TEXT NOT NULL,
    parameters                  JSONB NOT NULL DEFAULT '{}',
    status                      TEXT NOT NULL DEFAULT 'ACTIVE'
                                CHECK (status IN ('ACTIVE', 'RESTORED', 'FAILED', 'HEALED')),
    affected_rows               INT NOT NULL DEFAULT 0,
    pre_fault_snapshot_id       BIGINT REFERENCES health_snapshots(id),
    degraded_snapshot_id        BIGINT REFERENCES health_snapshots(id),
    recovered_snapshot_id       BIGINT REFERENCES health_snapshots(id),
    started_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    restored_at                 TIMESTAMPTZ,
    error_message               TEXT
);

CREATE UNIQUE INDEX one_active_fault_run
    ON fault_runs ((status)) WHERE status = 'ACTIVE';

CREATE TABLE fault_embedding_backups (
    run_id                      BIGINT NOT NULL REFERENCES fault_runs(id) ON DELETE CASCADE,
    document_id                 BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding                   VECTOR(64) NOT NULL,
    embedding_model_version     TEXT NOT NULL,
    PRIMARY KEY (run_id, document_id)
);

CREATE INDEX fault_runs_started_at_idx ON fault_runs (started_at DESC);

-- Phase 4: self-healing audit trail and the healer's own rollback data.
CREATE TABLE maintenance_events (
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
CREATE UNIQUE INDEX one_running_maintenance
    ON maintenance_events ((status))
    WHERE status = 'RUNNING';

CREATE INDEX maintenance_events_started_at_idx
    ON maintenance_events (started_at DESC);

-- The healer's own rollback data. Deliberately separate from
-- fault_embedding_backups: the healer must never read the fault lab's
-- originals, or "healing" would just be restoring.
CREATE TABLE embedding_backups (
    event_id                BIGINT NOT NULL REFERENCES maintenance_events(id) ON DELETE CASCADE,
    document_id             BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding               VECTOR(64) NOT NULL,
    embedding_model_version TEXT NOT NULL,
    healed_embedding        VECTOR(64),
    PRIMARY KEY (event_id, document_id)
);
-- Extra health signals: ANN-vs-exact recall (index health, immune to inserts),
-- sentinel re-embedding mismatch (silent vector corruption), and the id horizon
-- the frozen canaries were built on (so legitimate inserts do not look like drift).
ALTER TABLE health_snapshots
    ADD COLUMN IF NOT EXISTS ann_recall_at_k FLOAT8,
    ADD COLUMN IF NOT EXISTS sentinel_mismatch_pct FLOAT8;
ALTER TABLE canary_set
    ADD COLUMN IF NOT EXISTS corpus_max_id BIGINT;
