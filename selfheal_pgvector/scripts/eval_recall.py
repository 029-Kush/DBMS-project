"""Run the canary suite and store an explainable Phase 2 health snapshot."""
import math

import psycopg2

from config import (
    DB_DSN,
    CURRENT_MODEL_VERSION,
    MAX_DEAD_TUPLE_PCT,
    MAX_DISTANCE_SHIFT_PCT,
    MAX_VERSION_SKEW_PCT,
    MIN_RECALL_AT_K,
)
from embed_model import TfidfSvdEmbedder
from search import hnsw_index_status, search_documents

TOP_K = 10


def recall_at_k(expected_ids, actual_ids):
    expected = set(expected_ids)
    if not expected:
        return None
    return len(expected & set(actual_ids)) / len(expected)


def percentile(values, fraction):
    """Nearest-rank percentile; sufficient and transparent for 12 canaries."""
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def classify_health(
    recall,
    version_skew_pct,
    dead_tuple_pct,
    distance_shift_pct,
    index_exists,
    index_used,
):
    """Convert independent measurements into explicit, actionable issues."""
    issues = []
    if recall < MIN_RECALL_AT_K:
        issues.append("LOW_RECALL")
    if version_skew_pct > MAX_VERSION_SKEW_PCT:
        issues.append("VERSION_SKEW")
    if dead_tuple_pct > MAX_DEAD_TUPLE_PCT:
        issues.append("TABLE_BLOAT")
    if distance_shift_pct > MAX_DISTANCE_SHIFT_PCT:
        issues.append("DISTANCE_DRIFT")
    if not index_exists:
        issues.append("INDEX_MISSING")
    elif not index_used:
        issues.append("INDEX_NOT_USED")

    if not issues:
        status = "HEALTHY"
    elif "LOW_RECALL" in issues or "INDEX_MISSING" in issues:
        status = "CRITICAL"
    elif issues == ["INDEX_NOT_USED"]:
        # PostgreSQL may reasonably prefer a sequential scan for the 480-row
        # demo corpus. Keep the observation visible without claiming damage.
        status = "WARNING"
    else:
        status = "DEGRADED"
    return status, issues


def get_distance_baseline(cur):
    """Use the first measured Phase 2 snapshot as the fixed drift baseline."""
    cur.execute(
        """SELECT mean_nn_distance
           FROM health_snapshots
           WHERE mean_nn_distance IS NOT NULL
           ORDER BY id
           LIMIT 1"""
    )
    row = cur.fetchone()
    return float(row[0]) if row else None


def main(note="baseline"):
    model = TfidfSvdEmbedder.load()
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id, query_text, expected_doc_ids FROM canary_set ORDER BY id"
        )
        canaries = cur.fetchall()
        if not canaries:
            raise RuntimeError("canary_set is empty; run build_canary.py first")

        recalls = []
        latencies = []
        nearest_distances = []
        first_embedding = None

        for _id, query_text, expected_ids in canaries:
            result = search_documents(
                cur,
                model,
                query_text,
                top_k=TOP_K,
                source="canary",
                log_query=True,
            )
            if first_embedding is None:
                first_embedding = result["embedding"]

            recall = recall_at_k(expected_ids, result["ids"])
            recalls.append(recall)
            latencies.append(result["latency_ms"])
            if result["distances"]:
                nearest_distances.append(result["distances"][0])

            print(
                f"  recall@{TOP_K}={recall:.2f}  "
                f"latency={result['latency_ms']:5.2f}ms  "
                f"nearest={result['distances'][0]:.4f}  {query_text!r}"
            )

        avg_recall = sum(recalls) / len(recalls)
        avg_latency = sum(latencies) / len(latencies)
        p95_latency = percentile(latencies, 0.95)
        mean_nn_distance = sum(nearest_distances) / len(nearest_distances)
        p95_nn_distance = percentile(nearest_distances, 0.95)

        cur.execute(
            """SELECT count(*) FILTER (WHERE embedding_model_version <> %s),
                      count(*)
               FROM documents""",
            (CURRENT_MODEL_VERSION,),
        )
        stale_count, total_count = cur.fetchone()
        version_skew_pct = 100.0 * stale_count / total_count if total_count else 0.0

        cur.execute(
            """SELECT n_dead_tup, n_live_tup
               FROM pg_stat_user_tables
               WHERE relname = 'documents'"""
        )
        row = cur.fetchone()
        dead_tuple_pct = 0.0
        if row and (row[0] + row[1]) > 0:
            dead_tuple_pct = 100.0 * row[0] / (row[0] + row[1])

        baseline_distance = get_distance_baseline(cur)
        if baseline_distance and baseline_distance > 0:
            distance_shift_pct = (
                100.0 * (mean_nn_distance - baseline_distance) / baseline_distance
            )
        else:
            distance_shift_pct = 0.0

        index_exists, index_used = hnsw_index_status(
            cur, first_embedding, top_k=TOP_K
        )
        status, issues = classify_health(
            avg_recall,
            version_skew_pct,
            dead_tuple_pct,
            distance_shift_pct,
            index_exists,
            index_used,
        )

        cur.execute(
            """INSERT INTO health_snapshots
               (recall_at_k, avg_latency_ms, p95_latency_ms,
                mean_nn_distance, p95_nn_distance, distance_shift_pct,
                version_skew_pct, dead_tuple_pct, index_exists, index_used,
                health_status, issues, note)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                avg_recall,
                avg_latency,
                p95_latency,
                mean_nn_distance,
                p95_nn_distance,
                distance_shift_pct,
                version_skew_pct,
                dead_tuple_pct,
                index_exists,
                index_used,
                status,
                issues,
                note,
            ),
        )
        snapshot_id = cur.fetchone()[0]
        conn.commit()

        print(f"\n== Health snapshot #{snapshot_id} ({note}) ==")
        print(f"  status:             {status}")
        print(f"  issues:             {', '.join(issues) if issues else 'none'}")
        print(f"  avg recall@{TOP_K}:   {avg_recall:.3f}")
        print(f"  avg / p95 latency:  {avg_latency:.2f} / {p95_latency:.2f} ms")
        print(f"  mean / p95 NN dist: {mean_nn_distance:.4f} / {p95_nn_distance:.4f}")
        print(f"  distance shift:     {distance_shift_pct:+.1f}%")
        print(f"  version skew:       {version_skew_pct:.1f}%")
        print(f"  dead tuple ratio:   {dead_tuple_pct:.1f}%")
        print(f"  HNSW exists / used: {index_exists} / {index_used}")

        return {
            "snapshot_id": snapshot_id,
            "status": status,
            "issues": issues,
            "recall_at_k": avg_recall,
            "mean_nn_distance": mean_nn_distance,
            "distance_shift_pct": distance_shift_pct,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    import sys

    snapshot_note = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    main(snapshot_note)
