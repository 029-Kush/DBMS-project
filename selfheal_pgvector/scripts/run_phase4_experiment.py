"""Fault scenarios against a live database: break it, let the healer respond.

The healer is never told what was injected; it sees only health signals. The
harness takes a full snapshot of (body, vector, label) before each scenario and
afterwards counts rows that differ from it -- ground truth independent of the
fault lab's backups. Usage: python run_phase4_experiment.py [--out results.json]
"""
import argparse
import json
import time

import psycopg2

import fault_lab
from config import CURRENT_MODEL_VERSION, DB_DSN, EMBEDDER
from eval_recall import main as run_health_check
from heal import heal_until_stable
from inject_fault import active_run, inject_model_drift, restore_active_fault
from repair import rebuild_index, vacuum_documents


def sql(statement, params=None, fetch=False):
    conn = psycopg2.connect(DB_DSN)
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall() if fetch else None
        conn.commit()
        return rows
    finally:
        conn.close()


def snapshot():
    return {r[0]: r[1:] for r in sql(
        "SELECT id, body, embedding::text, embedding_model_version FROM documents", fetch=True)}


def rows_differing(snap, compare_body=False):
    now = snapshot()
    bad = 0
    for doc_id, (body, vec, version) in snap.items():
        cur = now.get(doc_id)
        if cur is None or cur[1] != vec or cur[2] != version or (compare_body and cur[0] != body):
            bad += 1
    return bad + len(set(now) - set(snap))


_FRESH = {}  # body -> fresh embedding; text->vector is deterministic, so cache across scenarios


def inconsistent_rows(tolerance=1e-3):
    """Independent audit: rows whose stored vector disagrees with a fresh embedding
    of their text, or whose label is not the current model. 0 == fully correct."""
    import numpy as np
    from embed_model import load_embedder

    rows = sql("SELECT body, embedding::text, embedding_model_version FROM documents ORDER BY id", fetch=True)
    model = load_embedder()
    missing = sorted({r[0] for r in rows} - set(_FRESH))
    for start in range(0, len(missing), 500):
        for body, vec in zip(missing[start:start + 500], model.embed(missing[start:start + 500])):
            _FRESH[body] = vec
    bad = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        fresh = np.array([_FRESH[r[0]] for r in chunk])
        stored = np.array([json.loads(r[1]) for r in chunk])
        cos = 1 - np.sum(fresh * stored, axis=1) / (np.linalg.norm(fresh, axis=1) * np.linalg.norm(stored, axis=1))
        bad += int(np.sum(cos > tolerance)) + sum(1 for r in chunk if r[2] != CURRENT_MODEL_VERSION)
    return bad


def reset_pristine(snap, canary_backup):
    """Harness-only: return the database to the pre-scenario state."""
    conn = psycopg2.connect(DB_DSN)
    try:
        cur = conn.cursor()
        if active_run(cur):
            cur.execute("UPDATE fault_runs SET status='RESTORED', restored_at=now() WHERE status='ACTIVE'")
        now = snapshot()
        for doc_id, (body, vec, version) in snap.items():
            if doc_id not in now or now[doc_id] != (body, vec, version):
                cur.execute(
                    """INSERT INTO documents (id, category, body, embedding, embedding_model_version)
                       VALUES (%s, 'restored', %s, %s::vector, %s)
                       ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body,
                         embedding = EXCLUDED.embedding,
                         embedding_model_version = EXCLUDED.embedding_model_version""",
                    (doc_id, body, vec, version))
        extra = set(now) - set(snap)
        if extra:
            cur.execute("DELETE FROM documents WHERE id = ANY(%s)", (list(extra),))
        for cid, expected in canary_backup:
            cur.execute("UPDATE canary_set SET expected_doc_ids = %s WHERE id = %s", (expected, cid))
        conn.commit()
    finally:
        conn.close()
    rebuild_index()
    vacuum_documents()


def churn_and_wait():
    fault_lab.churn(2)
    time.sleep(2)  # let pg_stat counters flush


def corrupt_canary():
    sql("UPDATE canary_set SET expected_doc_ids = ARRAY(SELECT id FROM documents ORDER BY id DESC LIMIT 10) WHERE id <= 6")


def stale_content_update():
    """User edits document text; the stored embedding is not refreshed."""
    sql("""UPDATE documents d SET body = d.body || ' ' || e.body
           FROM documents e WHERE e.id = d.id + 1 AND d.id % 7 = 0""")


SCENARIOS = [
    # name, inject, compares_body, expected primary behaviour
    ("healthy (control)", lambda: None, False),
    ("model drift 25% (labelled)", lambda: inject_model_drift(25.0, 42), False),
    ("stale labels (id%3)", lambda: fault_lab.relabel_fraction(), False),
    ("cross-model mix 25% (labelled)", lambda: fault_lab.inject_cross_model(25.0, 42, "minilm" if EMBEDDER == "bge" else "bge", True) if EMBEDDER != "tfidf" else None, False),
    ("cross-model mix 25% (SILENT)", lambda: fault_lab.inject_cross_model(25.0, 42, "minilm" if EMBEDDER == "bge" else "bge", False) if EMBEDDER != "tfidf" else None, False),
    ("silent permutation 25%", lambda: fault_lab.inject_silent_permutation(25.0, 42), False),
    ("silent noise 25% (sigma .15)", lambda: fault_lab.inject_noise(25.0, 42, 0.15), False),
    ("content updated, embedding stale", stale_content_update, True),
    ("HNSW index dropped", fault_lab.drop_index, False),
    ("HNSW index INVALID", fault_lab.invalidate_index, False),
    ("HNSW index degraded (m=4)", fault_lab.degrade_index, False),
    ("table bloat", churn_and_wait, False),
    ("drift + dropped index + bloat", lambda: (inject_model_drift(25.0, 42), fault_lab.drop_index(), churn_and_wait()), False),
    ("unexplained recall loss (canary rot)", corrupt_canary, False),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", help="write results as JSON")
    parser.add_argument("--only", help="substring filter on scenario name")
    args = parser.parse_args()

    sql("ALTER TABLE documents SET (autovacuum_enabled = false)")  # deterministic bloat tests
    results = []
    try:
        for name, inject, compares_body in SCENARIOS:
            if args.only and args.only not in name:
                continue
            if inject is None or (EMBEDDER == "tfidf" and "cross-model" in name):
                continue
            print(f"\n################ {name} ################")
            sql("TRUNCATE maintenance_events CASCADE")  # experiment isolation: no cross-scenario failure memory
            snap = snapshot()
            canary_backup = sql("SELECT id, expected_doc_ids FROM canary_set", fetch=True)
            inject()
            injected = run_health_check(note="scenario_injected")
            started = time.perf_counter()
            history = heal_until_stable(note="scenario", max_cycles=6)
            seconds = time.perf_counter() - started
            final = run_health_check(note="scenario_final")
            wrong = rows_differing(snap, compare_body=False if compares_body else False)
            results.append({
                "scenario": name,
                "issues_seen": injected["issues"],
                "status_before": injected["status"], "recall_before": injected["recall_at_k"],
                "ann_before": injected["ann_recall"],
                "outcomes": [h["outcome"] for h in history],
                "actions": [list(h["actions"]) for h in history],
                "status_after": final["status"], "issues_after": final["issues"],
                "recall_after": final["recall_at_k"], "ann_after": final["ann_recall"],
                "rows_not_matching_pre_fault": wrong,
                "rows_inconsistent_with_text": inconsistent_rows(),
                "heal_seconds": round(seconds, 2),
            })
            reset_pristine(snap, canary_backup)

        print("\n\n===== SUMMARY =====")
        for r in results:
            print(f"{r['scenario']:38s} {'>'.join(r['outcomes']):38s} "
                  f"{r['status_before']:9s}->{r['status_after']:9s} "
                  f"recall {r['recall_before']:.3f}->{r['recall_after']:.3f} "
                  f"ann {r['ann_before']:.2f}->{r['ann_after']:.2f} wrong={r['rows_not_matching_pre_fault']} inconsistent={r['rows_inconsistent_with_text']} "
                  f"issues_after={r['issues_after']}")
        if args.out:
            json.dump(results, open(args.out, "w"), indent=2)
    finally:
        sql("ALTER TABLE documents RESET (autovacuum_enabled)")


if __name__ == "__main__":
    main()
