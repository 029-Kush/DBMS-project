"""Run the canary suite and store an explainable Phase 2 health snapshot."""
import math

import psycopg2

import json

import numpy as np

from config import (
    ANN_PROBE_QUERIES,
    ANN_RECALL_MARGIN,
    DB_DSN,
    CURRENT_MODEL_VERSION,
    MAX_DEAD_TUPLE_PCT,
    MAX_DISTANCE_SHIFT_PCT,
    MAX_VERSION_SKEW_PCT,
    MIN_ANN_RECALL_AT_K,
    MIN_RECALL_AT_K,
    SENTINEL_SAMPLE,
    SENTINEL_TOLERANCE,
)
from embed_model import load_embedder
from search import ann_top, exact_top, hnsw_index_status, search_documents

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
    ann_recall=1.0,
    sentinel_mismatch_pct=0.0,
    ann_baseline=1.0,
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
    if sentinel_mismatch_pct > 0:
        issues.append("VECTOR_MISMATCH")
    if not index_exists:
        issues.append("INDEX_MISSING")
    elif ann_recall < max(MIN_ANN_RECALL_AT_K, ann_baseline - ANN_RECALL_MARGIN):
        issues.append("INDEX_DEGRADED")
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


def get_ann_baseline(cur):
    """First measured ANN-vs-exact recall = the healthy reference for this deployment."""
    cur.execute(
        """SELECT ann_recall_at_k FROM health_snapshots
           WHERE ann_recall_at_k IS NOT NULL ORDER BY id LIMIT 1"""
    )
    row = cur.fetchone()
    return float(row[0]) if row else None


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


TIE_EPSILON = 1e-6


def ann_recall_at_k(cur, probe_embeddings, k=TOP_K):
    """Index health: how much of the exact top-k does the HNSW path return?

    Distance-aware: an approximate result counts as correct if it is at least as
    close as the exact k-th neighbour, so duplicate/tied documents cannot make a
    healthy index look broken. Independent of the frozen canaries, so inserts and
    drift cannot affect it.
    """
    recalls = []
    for embedding in probe_embeddings:
        exact = exact_top(cur, embedding, k)
        if not exact:
            continue
        kth = exact[-1][1] + TIE_EPSILON
        ann = ann_top(cur, embedding, k)
        recalls.append(sum(1 for _id, dist in ann if dist <= kth) / len(exact))
    return sum(recalls) / len(recalls) if recalls else 1.0


def sentinel_mismatch_pct(cur, model, sample=SENTINEL_SAMPLE):
    """Silent-corruption probe: re-embed random stored rows and compare.

    Only rows already labelled with the current model are sampled; stale-label
    rows are VERSION_SKEW's job. Returns percent of sampled rows whose stored
    vector differs from a fresh embedding beyond SENTINEL_TOLERANCE.
    """
    cur.execute(
        """SELECT body, embedding::text FROM documents
           WHERE embedding_model_version = %s
           ORDER BY random() LIMIT %s""",
        (CURRENT_MODEL_VERSION, sample),
    )
    rows = cur.fetchall()
    if not rows:
        return 0.0
    fresh = model.embed([row[0] for row in rows])
    stored = np.array([json.loads(row[1]) for row in rows])
    cosine_distance = 1.0 - np.sum(fresh * stored, axis=1) / (
        np.linalg.norm(fresh, axis=1) * np.linalg.norm(stored, axis=1)
    )
    return 100.0 * float(np.mean(cosine_distance > SENTINEL_TOLERANCE))


def main(note="baseline"):
    model = load_embedder()
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT id, query_text, expected_doc_ids, corpus_max_id FROM canary_set ORDER BY id"
        )
        canaries = cur.fetchall()
        if not canaries:
            raise RuntimeError("canary_set is empty; run build_canary.py first")

        recalls = []
        probe_embeddings = []
        latencies = []
        nearest_distances = []
        first_embedding = None

        for _id, query_text, expected_ids, corpus_max_id in canaries:
            result = search_documents(
                cur,
                model,
                query_text,
                top_k=TOP_K,
                source="canary",
                log_query=True,
                max_id=corpus_max_id,
            )
            if first_embedding is None:
                first_embedding = result["embedding"]

            probe_embeddings.append(result["embedding"])
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
        cur.execute(
            """SELECT embedding::text FROM documents
               ORDER BY random() LIMIT %s""",
            (ANN_PROBE_QUERIES,),
        )
        probe_embeddings += [row[0] for row in cur.fetchall()]
        ann_recall = ann_recall_at_k(cur, probe_embeddings)
        mismatch_pct = sentinel_mismatch_pct(cur, model)
        ann_baseline = get_ann_baseline(cur)
        if ann_baseline is None:
            ann_baseline = ann_recall  # first snapshot defines the reference
        status, issues = classify_health(
            avg_recall,
            version_skew_pct,
            dead_tuple_pct,
            distance_shift_pct,
            index_exists,
            index_used,
            ann_recall,
            mismatch_pct,
            ann_baseline,
        )

        cur.execute(
            """INSERT INTO health_snapshots
               (recall_at_k, avg_latency_ms, p95_latency_ms,
                mean_nn_distance, p95_nn_distance, distance_shift_pct,
                version_skew_pct, dead_tuple_pct, index_exists, index_used,
                health_status, issues, note,
                ann_recall_at_k, sentinel_mismatch_pct)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                ann_recall,
                mismatch_pct,
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
        print(f"  ANN recall vs exact:{ann_recall:.3f}")
        print(f"  sentinel mismatch:  {mismatch_pct:.1f}%")

        return {
            "snapshot_id": snapshot_id,
            "status": status,
            "issues": issues,
            "recall_at_k": avg_recall,
            "mean_nn_distance": mean_nn_distance,
            "distance_shift_pct": distance_shift_pct,
            "ann_recall": ann_recall,
            "sentinel_mismatch_pct": mismatch_pct,
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
