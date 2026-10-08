"""Additional reversible faults for the Phase 5 experiments.

All vector faults back up the originals to fault_embedding_backups (so
inject_fault.restore_active_fault can undo them) and are deterministic given a
seed. They are *injection* tools: the healer never reads these backups.
"""
import json

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

from config import CURRENT_MODEL_VERSION, DB_DSN
from embed_model import FASTEMBED_MODELS, load_embedder
from inject_fault import active_run, latest_snapshot_id, parse_vector, select_document_ids
from load_data import vec_to_pg


def _inject_vectors(fault_type, parameters, new_vectors_fn, label_fn, percentage, seed):
    """new_vectors_fn(rows)->list of vectors; label_fn(old_label)->new label."""
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        if active_run(cur):
            raise RuntimeError("a fault run is still ACTIVE; restore it first")
        cur.execute("SELECT id, body, embedding::text, embedding_model_version FROM documents ORDER BY id")
        rows = cur.fetchall()
        chosen = select_document_ids([(r[0],) for r in rows], percentage, seed)
        picked = [r for r in rows if r[0] in chosen]
        parameters = {**parameters, "percentage": percentage, "seed": seed}
        cur.execute(
            """INSERT INTO fault_runs (fault_type, parameters, affected_rows, pre_fault_snapshot_id)
               VALUES (%s, %s::jsonb, %s, %s) RETURNING id""",
            (fault_type, json.dumps(parameters), len(picked), latest_snapshot_id(cur)),
        )
        run_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO fault_embedding_backups
               (run_id, document_id, embedding, embedding_model_version)
               SELECT %s, id, embedding, embedding_model_version FROM documents WHERE id = ANY(%s)""",
            (run_id, list(chosen)),
        )
        vectors = new_vectors_fn(picked)
        updates = [(r[0], vec_to_pg(v), label_fn(r[3])) for r, v in zip(picked, vectors)]
        execute_values(
            cur,
            """UPDATE documents d SET embedding = c.embedding::vector, embedding_model_version = c.version
               FROM (VALUES %s) AS c(id, embedding, version) WHERE d.id = c.id""",
            updates, template="(%s, %s, %s)", page_size=max(1, len(updates)),
        )
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def inject_cross_model(percentage, seed, other, labelled):
    """Re-embed a share of documents with a *different real model* (same dimension).

    pgvector accepts these silently. labelled=True records the true model name
    (version skew is visible); labelled=False keeps the current label (silent).
    """
    other_model = load_embedder(other)
    other_label = FASTEMBED_MODELS[other].split("/")[-1]

    def vectors(rows):
        return other_model.embed([r[1] for r in rows])

    label = (lambda old: other_label) if labelled else (lambda old: old)
    return _inject_vectors("CROSS_MODEL", {"other": other, "labelled": labelled},
                           vectors, label, percentage, seed)


def inject_noise(percentage, seed, sigma):
    """Silent corruption: add Gaussian noise to stored vectors, keep labels."""
    rng = np.random.default_rng(seed)

    def vectors(rows):
        out = []
        for r in rows:
            v = parse_vector(r[2])
            v = v + rng.normal(0.0, sigma, size=v.shape)
            out.append(v / np.linalg.norm(v))
        return out

    return _inject_vectors("VECTOR_NOISE", {"sigma": sigma}, vectors, lambda old: old,
                           percentage, seed)


def inject_silent_permutation(percentage, seed):
    """The Phase 3 coordinate permutation, but with labels left at the current model."""
    from inject_fault import incompatible_transform

    return _inject_vectors(
        "VECTOR_NOISE", {"kind": "permutation"},
        lambda rows: [incompatible_transform(parse_vector(r[2])) for r in rows],
        lambda old: old, percentage, seed,
    )


def degrade_index(m=4, ef_construction=8):
    """Replace the HNSW index with a deliberately poor one (low m / ef_construction)."""
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("DROP INDEX IF EXISTS documents_embedding_hnsw")
        cur.execute(
            f"""CREATE INDEX documents_embedding_hnsw ON documents
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = {int(m)}, ef_construction = {int(ef_construction)})"""
        )
    finally:
        cur.close()
        conn.close()


def drop_index():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    try:
        conn.cursor().execute("DROP INDEX IF EXISTS documents_embedding_hnsw")
    finally:
        conn.close()


def invalidate_index():
    """Simulate an interrupted CREATE INDEX CONCURRENTLY: index exists but is INVALID."""
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    try:
        conn.cursor().execute(
            """UPDATE pg_index SET indisvalid = false
               WHERE indexrelid = 'documents_embedding_hnsw'::regclass"""
        )
    finally:
        conn.close()


def relabel_fraction(modulus=3, label="tfidf-svd-v0"):
    """Half-finished model migration: relabel every Nth row (vectors untouched)."""
    conn = psycopg2.connect(DB_DSN)
    try:
        cur = conn.cursor()
        cur.execute("UPDATE documents SET embedding_model_version = %s WHERE id %% %s = 0",
                    (label, modulus))
        conn.commit()
    finally:
        conn.close()


def churn(rounds=2):
    """Row churn: rewrite the table without vacuuming, to create bloat."""
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        for _ in range(rounds):
            cur.execute("UPDATE documents SET last_reindexed_at = now()")
    finally:
        conn.close()
