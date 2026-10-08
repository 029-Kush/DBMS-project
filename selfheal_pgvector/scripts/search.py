"""Shared, observable vector-search operations.

All Phase 2 searches go through this module so query behaviour is recorded in
one consistent format. The caller owns the database transaction and decides
when to commit.
"""
import time

from load_data import vec_to_pg


def search_documents(cur, model, query_text, top_k=10, source="user", log_query=True, max_id=None, category=None):
    """Embed a query, run cosine search, optionally log it, and return metrics."""
    vector = model.embed([query_text])[0]
    if not any(abs(value) > 1e-12 for value in vector):
        raise ValueError(
            "The query contains no vocabulary known to this small TF-IDF model. "
            "Use terms represented in the demo corpus or replace the baseline "
            "embedder with a sentence-transformer."
        )
    pgvector = vec_to_pg(vector)

    # max_id restricts to documents that existed when the canaries were frozen,
    # so legitimate later inserts cannot masquerade as drift.
    clauses = []
    filters = []
    if max_id is not None:
        clauses.append("id <= %s")
        filters.append(max_id)
    if category is not None:
        clauses.append("category = %s")
        filters.append(category)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params = (pgvector, *filters, pgvector, top_k)
    started = time.perf_counter()
    cur.execute(
        f"""SELECT id, embedding <=> %s::vector AS distance
           FROM documents {where}
           ORDER BY embedding <=> %s::vector
           LIMIT %s""",
        params,
    )
    rows = cur.fetchall()
    latency_ms = (time.perf_counter() - started) * 1000

    result_ids = [row[0] for row in rows]
    result_distances = [float(row[1]) for row in rows]

    if log_query:
        cur.execute(
            """INSERT INTO query_log
               (query_text, query_embedding, top_k, result_ids,
                result_distances, latency_ms, source)
               VALUES (%s, %s::vector, %s, %s, %s, %s, %s)""",
            (
                query_text,
                pgvector,
                top_k,
                result_ids,
                result_distances,
                latency_ms,
                source,
            ),
        )

    return {
        "ids": result_ids,
        "distances": result_distances,
        "latency_ms": latency_ms,
        "embedding": pgvector,
    }


def hnsw_index_status(cur, query_embedding, top_k=10):
    """Return whether the expected HNSW index exists and is used by the planner."""
    # An INVALID index (e.g. an interrupted CREATE INDEX CONCURRENTLY) still
    # resolves by name but is never used; treat it as missing, not "unused".
    cur.execute(
        """SELECT coalesce(bool_and(i.indisvalid), false)
           FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
           WHERE c.relname = 'documents_embedding_hnsw'"""
    )
    exists = cur.fetchone()[0]
    if not exists:
        return False, False

    cur.execute(
        """EXPLAIN (FORMAT JSON)
           SELECT id FROM documents
           ORDER BY embedding <=> %s::vector
           LIMIT %s""",
        (query_embedding, top_k),
    )
    plan = cur.fetchone()[0][0]["Plan"]

    def uses_expected_index(node):
        if node.get("Index Name") == "documents_embedding_hnsw":
            return True
        return any(uses_expected_index(child) for child in node.get("Plans", []))

    return True, uses_expected_index(plan)


def _ranked(cur, query_embedding, top_k):
    cur.execute(
        """SELECT id, embedding <=> %s::vector FROM documents
           ORDER BY embedding <=> %s::vector LIMIT %s""",
        (query_embedding, query_embedding, top_k),
    )
    return [(row[0], float(row[1])) for row in cur.fetchall()]


def exact_top(cur, query_embedding, top_k=10):
    """[(id, distance)] by brute force: forbid index scans for this one statement."""
    cur.execute("SET LOCAL enable_indexscan = off")
    try:
        return _ranked(cur, query_embedding, top_k)
    finally:
        cur.execute("SET LOCAL enable_indexscan = on")


def ann_top(cur, query_embedding, top_k=10):
    """[(id, distance)] via the approximate path: forbid seq scans so HNSW must serve it."""
    cur.execute("SET LOCAL enable_seqscan = off")
    try:
        return _ranked(cur, query_embedding, top_k)
    finally:
        cur.execute("SET LOCAL enable_seqscan = on")


def exact_top_ids(cur, query_embedding, top_k=10):
    """Brute-force ground truth: forbid index scans for this one statement."""
    cur.execute("SET LOCAL enable_indexscan = off")
    try:
        cur.execute(
            """SELECT id FROM documents ORDER BY embedding <=> %s::vector LIMIT %s""",
            (query_embedding, top_k),
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.execute("SET LOCAL enable_indexscan = on")
