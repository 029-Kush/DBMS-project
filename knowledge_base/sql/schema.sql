-- security-kb schema. Idempotent: safe to re-run (no DROPs).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS entities (
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    type        text NOT NULL,              -- tool | technique | vuln | concept | ...
    domain      text[] NOT NULL DEFAULT '{}',-- red | blue | ai | vuln | systems | ...
    aliases     text[] NOT NULL DEFAULT '{}',
    summary     text,
    version     text,                       -- installed/documented version (tools)
    UNIQUE (type, name)
);

-- Text is stored once per distinct body (pages installed under several names share it); chunk_refs maps
-- (entity, section, source) to it. Vectors are stored as codes only (see scripts/common.py: encode/decode).
CREATE SEQUENCE IF NOT EXISTS chunks_id_seq;
CREATE TABLE IF NOT EXISTS bodies (
    id     bigint PRIMARY KEY,                 -- from chunks_id_seq
    hash   bytea  NOT NULL UNIQUE,             -- md5(body): content address
    body   text   NOT NULL,
    flags  text[] NOT NULL DEFAULT '{}',       -- literal flags/IDs found in the text, union over its chunks (case-sensitive)
    trust  smallint NOT NULL DEFAULT 2,        -- best (lowest) trust of any chunk with this text
    -- 'simple' config on purpose: the english stemmer mangles flags and identifiers
    tsv    tsvector GENERATED ALWAYS AS (to_tsvector('simple', body)) STORED
);
CREATE TABLE IF NOT EXISTS chunk_refs (
    id             bigint PRIMARY KEY DEFAULT nextval('chunks_id_seq'),
    entity_id      int NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    section        text NOT NULL,           -- overview | syntax | examples | options | help | ...
    source         text NOT NULL,           -- man | help | tldr | ...
    source_version text,
    trust          smallint NOT NULL DEFAULT 2,  -- 1 official docs, 2 curated, 3 community
    is_code        boolean NOT NULL DEFAULT false,
    content_hash   bytea NOT NULL UNIQUE,   -- sha256(entity|section|body): idempotent re-ingest
    model_version  text NOT NULL,
    body_id        bigint NOT NULL REFERENCES bodies(id)
);
CREATE TABLE IF NOT EXISTS vec_quant (model_version text PRIMARY KEY, lo real[] NOT NULL, hi real[] NOT NULL);
CREATE TABLE IF NOT EXISTS body_vec (       -- no ANN index: an exact scan of the 1-bit codes is ~17 ms at 180k texts
    id bigint PRIMARY KEY REFERENCES bodies(id) ON DELETE CASCADE,
    b  bit(384) NOT NULL,                   -- sign bits (pgvector binary_quantize), Hamming candidates
    q  bytea NOT NULL                       -- 4 bits per dimension on the vec_quant scale, re-sort
);
ALTER TABLE body_vec SET (parallel_workers = 4);

CREATE INDEX IF NOT EXISTS bodies_tsv_idx        ON bodies USING gin (tsv);
CREATE INDEX IF NOT EXISTS bodies_trgm_idx       ON bodies USING gin (body gin_trgm_ops);
CREATE INDEX IF NOT EXISTS bodies_flags_idx      ON bodies USING gin (flags);
CREATE INDEX IF NOT EXISTS chunk_refs_entity_idx ON chunk_refs (entity_id, section);
CREATE INDEX IF NOT EXISTS chunk_refs_body_idx   ON chunk_refs (body_id);
-- one row per chunk with its text, for readers (show.py, the explorer, the benchmark loader)
CREATE OR REPLACE VIEW chunks AS
  SELECT r.id, r.entity_id, r.section, r.source, r.source_version, r.trust, r.is_code, b.body, b.flags,
         r.content_hash, r.model_version, b.tsv, r.body_id
  FROM chunk_refs r JOIN bodies b ON b.id = r.body_id;
CREATE INDEX IF NOT EXISTS entities_name_trgm ON entities USING gin (name gin_trgm_ops);
