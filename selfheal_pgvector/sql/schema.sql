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
    queried_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    version_skew_pct    FLOAT8,
    dead_tuple_pct      FLOAT8,
    note                TEXT,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
