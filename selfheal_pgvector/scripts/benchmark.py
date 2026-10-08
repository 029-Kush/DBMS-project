"""Phase 5 evidence: false alarms under load, targeted vs blanket repair, and
fault identifiability. Run against a loaded + canary-built + baselined database.

  python benchmark.py fp      --out fp.json       # false alarms under insert/update load
  python benchmark.py compare --out compare.json  # targeted healer vs blanket "fix everything"
  python benchmark.py matrix  --out matrix.json   # which faults are detectable/distinguishable
"""
import argparse
import csv
import json
import os
import random
import statistics
import threading
import time
from collections import Counter

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

import embed_model
import eval_recall
import fault_lab
import repair
from config import CURRENT_MODEL_VERSION, DB_DSN, EMBEDDER
from eval_recall import main as health_check
from heal import heal_once, heal_until_stable
from inject_fault import inject_model_drift, restore_active_fault
from load_data import vec_to_pg
from repair import CANONICAL_INDEX_SQL, INDEX_NAME, vacuum_documents
from run_phase4_experiment import inconsistent_rows, reset_pristine, snapshot, sql

OTHER = "minilm" if EMBEDDER == "bge" else "bge"


# ---------------------------------------------------------------- instrumentation
class CountingEmbedder:
    """Wraps an embedder and counts how many documents it embedded."""
    total = 0

    def __init__(self, inner):
        self.inner = inner

    def embed(self, texts):
        texts = list(texts)
        CountingEmbedder.total += len(texts)
        return self.inner.embed(texts)


_REAL_LOAD = embed_model.load_embedder
_SHARED = {}


def counting_load(preset=None):
    key = preset or EMBEDDER
    if key not in _SHARED:
        _SHARED[key] = _REAL_LOAD(preset)
    return CountingEmbedder(_SHARED[key])


def instrument():
    """Count embedding work done by the healer (repair) and by health checks."""
    repair.load_embedder = counting_load
    eval_recall.load_embedder = counting_load


class Probe(threading.Thread):
    """Background clients: a reader (ANN query) and a writer (single-row update)."""

    def __init__(self, kind, vector_text):
        super().__init__(daemon=True)
        self.kind, self.vector, self.stop_flag = kind, vector_text, False
        self.latencies, self.errors = [], 0

    def run(self):
        conn = None
        while not self.stop_flag:
            started = time.perf_counter()
            try:
                if conn is None or conn.closed:
                    conn = psycopg2.connect(DB_DSN)
                    conn.autocommit = True
                    conn.cursor().execute("SET statement_timeout = '60s'")
                cur = conn.cursor()
                if self.kind == "read":
                    cur.execute("SELECT id FROM documents ORDER BY embedding <=> %s::vector LIMIT 10", (self.vector,))
                    cur.fetchall()
                else:
                    cur.execute("UPDATE documents SET last_reindexed_at = last_reindexed_at WHERE id = 1")
                self.latencies.append((time.perf_counter() - started) * 1000)
            except psycopg2.Error:
                self.errors += 1
                conn = None
            time.sleep(0.02)

    def summary(self):
        lat = sorted(self.latencies) or [0.0]
        return {"ops": len(lat), "errors": self.errors, "max_ms": round(lat[-1], 1),
                "p99_ms": round(lat[int(0.99 * (len(lat) - 1))], 1), "median_ms": round(statistics.median(lat), 2)}


def wal_lsn():
    return sql("SELECT pg_current_wal_lsn()::text", fetch=True)[0][0]


def wal_bytes_since(lsn):
    return int(sql("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), %s)", (lsn,), fetch=True)[0][0])


def tuple_updates():
    time.sleep(2)  # let pg_stat counters flush
    return sql("SELECT n_tup_upd FROM pg_stat_user_tables WHERE relname='documents'", fetch=True)[0][0]


# ---------------------------------------------------------------- blanket repair
def blanket_repair(batch=500):
    """What an operator does with no diagnosis: re-embed everything, rebuild the
    index (DROP+CREATE, blocking) and VACUUM FULL (rewrite table, exclusive lock)."""
    model = counting_load()
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    version = cur.mogrify("%s", (CURRENT_MODEL_VERSION,)).decode()
    last = 0
    while True:
        cur.execute("SELECT id, body FROM documents WHERE id > %s ORDER BY id LIMIT %s", (last, batch))
        rows = cur.fetchall()
        if not rows:
            break
        vecs = model.embed([r[1] for r in rows])
        execute_values(
            cur,
            f"""UPDATE documents d SET embedding = v.new::vector, embedding_model_version = {version},
                last_reindexed_at = now() FROM (VALUES %s) AS v(id, new) WHERE d.id = v.id""",
            [(r[0], vec_to_pg(v)) for r, v in zip(rows, vecs)], template="(%s, %s)", page_size=len(rows))
        conn.commit()
        last = rows[-1][0]
    conn.close()
    auto = psycopg2.connect(DB_DSN)
    auto.autocommit = True
    c = auto.cursor()
    c.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    c.execute(f"CREATE INDEX {INDEX_NAME} ON documents {CANONICAL_INDEX_SQL}")
    c.execute("VACUUM FULL documents")
    c.execute("ANALYZE documents")
    auto.close()


def manual_repair(fault_name):
    """A skilled operator who already knows the fault, using standard blocking DDL."""
    auto = psycopg2.connect(DB_DSN)
    auto.autocommit = True
    c = auto.cursor()
    if "index" in fault_name:
        c.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
        c.execute(f"CREATE INDEX {INDEX_NAME} ON documents {CANONICAL_INDEX_SQL}")
    else:
        c.execute("VACUUM FULL documents")
    c.execute("ANALYZE documents")
    auto.close()


def targeted_repair():
    return heal_until_stable(note="bench", max_cycles=6)


# ---------------------------------------------------------------- compare
FAULTS = {
    "cross-model 10% (labelled)": lambda: fault_lab.inject_cross_model(10.0, 42, OTHER, True),
    "cross-model 50% (labelled)": lambda: fault_lab.inject_cross_model(50.0, 42, OTHER, True),
    "silent noise 10%": lambda: fault_lab.inject_noise(10.0, 42, 0.15),
    "index degraded (m=4)": fault_lab.degrade_index,
    "table bloat": lambda: (fault_lab.churn(2), time.sleep(2)),
}


def run_strategy(strategy, fault_name, h):
    sql("TRUNCATE maintenance_events CASCADE")
    reset_pristine(h["snap"], h["canary"])
    FAULTS[fault_name]()
    pre = health_check(note="bench_pre")
    CountingEmbedder.total = 0
    updates_before = tuple_updates()
    lsn = wal_lsn()
    reader, writer = Probe("read", h["qvec"]), Probe("write", h["qvec"])
    reader.start(); writer.start()
    time.sleep(0.5)
    started = time.perf_counter()
    if strategy == "targeted":
        targeted_repair()
    elif strategy == "blanket":
        blanket_repair()
    else:
        manual_repair(fault_name)
    seconds = time.perf_counter() - started
    reader.stop_flag = writer.stop_flag = True
    reader.join(); writer.join()
    wal = wal_bytes_since(lsn)
    updates = tuple_updates() - updates_before
    post = health_check(note="bench_post")
    return {
        "fault": fault_name, "strategy": strategy,
        "seconds": round(seconds, 1), "docs_embedded": CountingEmbedder.total,
        "rows_updated": updates - writer.summary()["ops"], "wal_mb": round(wal / 1e6, 1),
        "reader": reader.summary(), "writer": writer.summary(),
        "status_before": pre["status"], "status_after": post["status"],
        "issues_after": post["issues"], "recall_after": round(post["recall_at_k"], 3),
        "inconsistent_rows_after": inconsistent_rows(),
    }


def cmd_compare(args):
    instrument()
    h = {"snap": snapshot(), "canary": sql("SELECT id, expected_doc_ids FROM canary_set", fetch=True)}
    h["qvec"] = h["snap"][min(h["snap"])][1]
    sql("ALTER TABLE documents SET (autovacuum_enabled = false)")
    results = []
    try:
        for fault in FAULTS:
            if args.only and args.only not in fault:
                continue
            strategies = ["targeted", "blanket"] + (["manual-expert"] if ("index" in fault or "bloat" in fault) else [])
            for strategy in strategies:
                r = run_strategy(strategy, fault, h)
                results.append(r)
                print(f"{fault:28s} {strategy:13s} {r['seconds']:7.1f}s embedded={r['docs_embedded']:6d} "
                      f"rows_upd={r['rows_updated']:6d} wal={r['wal_mb']:7.1f}MB "
                      f"read(max {r['reader']['max_ms']}ms err {r['reader']['errors']}) "
                      f"write(max {r['writer']['max_ms']}ms err {r['writer']['errors']}) "
                      f"after={r['status_after']} wrong={r['inconsistent_rows_after']}", flush=True)
        reset_pristine(h["snap"], h["canary"])
    finally:
        sql("ALTER TABLE documents RESET (autovacuum_enabled)")
    if args.out:
        json.dump(results, open(args.out, "w"), indent=2)


# ---------------------------------------------------------------- fp
def naive_canary_recall(cur, model):
    """What recall would be WITHOUT the corpus-horizon fix (unfiltered, frozen ids)."""
    cur.execute("SELECT query_text, expected_doc_ids FROM canary_set ORDER BY id")
    recalls = []
    for text, expected in cur.fetchall():
        vec = vec_to_pg(model.embed([text])[0])
        cur.execute("SELECT id FROM documents ORDER BY embedding <=> %s::vector LIMIT 10", (vec,))
        got = {r[0] for r in cur.fetchall()}
        recalls.append(len(got & set(expected)) / len(expected))
    return sum(recalls) / len(recalls)


def cmd_fp(args):
    model = embed_model.load_embedder()
    inserts = list(csv.DictReader(open(os.environ["SELFHEAL_INSERTS"])))
    random.seed(5)
    sql("TRUNCATE maintenance_events CASCADE")
    rows, naive_min = [], 1.0
    conn = psycopg2.connect(DB_DSN)
    for cycle in range(1, args.cycles + 1):
        load = cycle > args.idle_cycles
        if load:  # legitimate workload: new documents + consistently re-embedded edits
            batch = inserts[(cycle - args.idle_cycles - 1) * args.inserts:(cycle - args.idle_cycles) * args.inserts]
            vecs = model.embed([r["body"] for r in batch])
            cur = conn.cursor()
            execute_values(cur, """INSERT INTO documents (category, body, embedding, embedding_model_version)
                                   VALUES %s""",
                           [(r["category"], r["body"], vec_to_pg(v), CURRENT_MODEL_VERSION) for r, v in zip(batch, vecs)],
                           template="(%s, %s, %s::vector, %s)")
            cur.execute("SELECT id, body FROM documents ORDER BY random() LIMIT %s", (args.updates,))
            edit = cur.fetchall()
            new_bodies = [b + " (edited)" for _, b in edit]
            for (doc_id, _), body, v in zip(edit, new_bodies, model.embed(new_bodies)):
                cur.execute("UPDATE documents SET body=%s, embedding=%s::vector WHERE id=%s", (body, vec_to_pg(v), doc_id))
            conn.commit()
        naive = naive_canary_recall(conn.cursor(), model)
        naive_min = min(naive_min, naive)
        out = heal_once(note=f"fp_{cycle}")
        status = out["after"] or out["before"]
        rows.append({"cycle": cycle, "load": load, "outcome": out["outcome"], "status": status["status"],
                     "issues": status["issues"], "naive_canary_recall": round(naive, 3),
                     "docs": sql("SELECT count(*) FROM documents", fetch=True)[0][0]})
        print(rows[-1], flush=True)
    actions = [r for r in rows if r["outcome"] not in ("NO_ACTION",)]
    issue_hist = Counter(i for r in rows for i in r["issues"])
    summary = {"cycles": len(rows), "cycles_with_action_or_escalation": len(actions),
               "issue_histogram": dict(issue_hist), "naive_recall_min_under_load": naive_min,
               "naive_would_have_alarmed_cycles": sum(1 for r in rows if r["naive_canary_recall"] < 0.95)}
    print(json.dumps(summary, indent=2))
    if args.out:
        json.dump({"summary": summary, "rows": rows}, open(args.out, "w"), indent=2)


# ---------------------------------------------------------------- matrix
def warm_other_model_cache():
    """Embed the whole corpus once with the other model so repeated injections are cheap."""
    other = _REAL_LOAD(OTHER)
    bodies = [r[0] for r in sql("SELECT body FROM documents ORDER BY id", fetch=True)]
    memo = {}
    for i in range(0, len(bodies), 500):
        for b, v in zip(bodies[i:i + 500], other.embed(bodies[i:i + 500])):
            memo[b] = v

    class Memo:
        def embed(self, texts):
            return np.array([memo[t] for t in texts])

    fault_lab.load_embedder = lambda preset=None: Memo()


MATRIX_FAULTS = {
    "cross-model labelled": lambda pct, seed: fault_lab.inject_cross_model(pct, seed, OTHER, True),
    "cross-model silent": lambda pct, seed: fault_lab.inject_cross_model(pct, seed, OTHER, False),
    "noise 0.05 silent": lambda pct, seed: fault_lab.inject_noise(pct, seed, 0.05),
    "noise 0.15 silent": lambda pct, seed: fault_lab.inject_noise(pct, seed, 0.15),
    "permutation silent": lambda pct, seed: fault_lab.inject_silent_permutation(pct, seed),
}


def cmd_matrix(args):
    warm_other_model_cache()
    sql("ALTER TABLE documents SET (autovacuum_enabled = false)")
    cells = []
    try:
        for fault, inject in MATRIX_FAULTS.items():
            if args.only and args.only not in fault:
                continue
            for pct in args.severities:
                for seed in range(1, args.seeds + 1):
                    inject(pct, seed)
                    r = health_check(note=f"matrix_{fault}_{pct}_{seed}")
                    restore_active_fault()
                    vacuum_documents()
                    cells.append({"fault": fault, "pct": pct, "seed": seed, "issues": sorted(r["issues"]),
                                  "recall": round(r["recall_at_k"], 3), "shift": round(r["distance_shift_pct"], 1),
                                  "sentinel_pct": round(r["sentinel_mismatch_pct"], 1)})
                    print(cells[-1], flush=True)
    finally:
        sql("ALTER TABLE documents RESET (autovacuum_enabled)")
    if args.out:
        json.dump(cells, open(args.out, "w"), indent=2)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("fp", "compare", "matrix"):
        p = sub.add_parser(name)
        p.add_argument("--out")
        p.add_argument("--only")
        p.add_argument("--cycles", type=int, default=20)
        p.add_argument("--idle-cycles", type=int, default=5)
        p.add_argument("--inserts", type=int, default=100)
        p.add_argument("--updates", type=int, default=20)
        p.add_argument("--seeds", type=int, default=3)
        p.add_argument("--severities", type=float, nargs="+", default=[2, 5, 10, 25, 50])
    args = parser.parse_args()
    {"fp": cmd_fp, "compare": cmd_compare, "matrix": cmd_matrix}[args.cmd](args)


if __name__ == "__main__":
    main()
