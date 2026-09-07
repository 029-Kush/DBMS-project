"""
Runs the canary set against the live database and reports recall@k:
for each canary query, what fraction of its expected top-k ids are
still returned in the current top-k? Averaged across all canary
queries, this is the single number the rest of the project watches.

Also records latency and inserts a row into health_snapshots so the
metric is tracked over time (baseline now, degraded after a fault
injection later, recovered after a repair).
"""
import time

import psycopg2

from config import DB_DSN, CURRENT_MODEL_VERSION
from embed_model import TfidfSvdEmbedder
from load_data import vec_to_pg

TOP_K = 10


def recall_at_k(expected_ids, actual_ids):
    expected = set(expected_ids)
    if not expected:
        return None
    hit = len(expected & set(actual_ids))
    return hit / len(expected)


def main(note="baseline"):
    model = TfidfSvdEmbedder.load()
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    cur.execute("SELECT id, query_text, expected_doc_ids FROM canary_set ORDER BY id;")
    canaries = cur.fetchall()

    recalls = []
    latencies = []
    for _id, query_text, expected_ids in canaries:
        vec = model.embed([query_text])[0]
        pgvec = vec_to_pg(vec)

        t0 = time.perf_counter()
        cur.execute(
            """SELECT id, embedding <=> %s::vector AS dist FROM documents
               ORDER BY embedding <=> %s::vector LIMIT %s""",
            (pgvec, pgvec, TOP_K),
        )
        rows = cur.fetchall()
        latency_ms = (time.perf_counter() - t0) * 1000
        latencies.append(latency_ms)

        actual_ids = [r[0] for r in rows]
        r = recall_at_k(expected_ids, actual_ids)
        recalls.append(r)
        print(f"  recall@{TOP_K}={r:.2f}  latency={latency_ms:5.2f}ms  {query_text!r}")

    avg_recall = sum(recalls) / len(recalls)
    avg_latency = sum(latencies) / len(latencies)
    p95_latency = sorted(latencies)[int(0.95 * len(latencies)) - 1]

    # version skew: % of rows not on the current embedding model version
    cur.execute(
        "SELECT count(*) FILTER (WHERE embedding_model_version <> %s), count(*) FROM documents",
        (CURRENT_MODEL_VERSION,),
    )
    stale_count, total_count = cur.fetchone()
    version_skew_pct = 100.0 * stale_count / total_count if total_count else 0.0

    # dead tuple ratio, a proxy for index/table staleness from churn
    cur.execute(
        """SELECT n_dead_tup, n_live_tup FROM pg_stat_user_tables
           WHERE relname = 'documents'"""
    )
    row = cur.fetchone()
    dead_tuple_pct = 0.0
    if row and (row[0] + row[1]) > 0:
        dead_tuple_pct = 100.0 * row[0] / (row[0] + row[1])

    cur.execute(
        """INSERT INTO health_snapshots
           (recall_at_k, avg_latency_ms, p95_latency_ms, mean_nn_distance,
            version_skew_pct, dead_tuple_pct, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (avg_recall, avg_latency, p95_latency, None, version_skew_pct, dead_tuple_pct, note),
    )
    conn.commit()

    print(f"\n== Health snapshot ({note}) ==")
    print(f"  avg recall@{TOP_K}:   {avg_recall:.3f}")
    print(f"  avg latency:        {avg_latency:.2f} ms")
    print(f"  p95 latency:        {p95_latency:.2f} ms")
    print(f"  version skew:       {version_skew_pct:.1f}%")
    print(f"  dead tuple ratio:   {dead_tuple_pct:.1f}%")

    cur.close()
    conn.close()
    return avg_recall


if __name__ == "__main__":
    import sys
    note = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    main(note)
