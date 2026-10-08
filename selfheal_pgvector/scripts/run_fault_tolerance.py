"""Live fault-tolerance matrix for the healer.

Kills, restarts and races real healer processes against a real database, then
checks hard invariants: no stuck RUNNING events, no lost documents, no "torn"
rows (every row is either its pre-fault or its post-fault state, never a
half-written mix), no leftover helper indexes, and that a clean follow-up cycle
reaches a fully consistent state. Needs the docker container named by
SELFHEAL_CONTAINER (default selfheal-pg) for restart tests.
Usage: python run_fault_tolerance.py [--out results.json]
"""
import argparse
import json
import os
import subprocess
import sys
import time

import psycopg2

import fault_lab
from config import DB_DSN
from heal import _recover_orphans, heal_once
from inject_fault import inject_model_drift
from repair import reembed_stale, rollback_reembed
from run_phase4_experiment import inconsistent_rows, reset_pristine, snapshot, sql

CONTAINER = os.environ.get("SELFHEAL_CONTAINER", "selfheal-pg")
HERE = os.path.dirname(os.path.abspath(__file__))
HEAL_CODE = (
    "import json, heal; o = heal.heal_once('ft'); "
    "print('RESULT' + json.dumps({k: o.get(k) for k in ('outcome','recovered','event_id','error')}, default=str))"
)


def spawn(env_extra=None, code=HEAL_CODE):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.Popen([sys.executable, "-c", code], cwd=HERE, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def finish(proc, timeout=180):
    out, err = proc.communicate(timeout=timeout)
    result = None
    for line in out.splitlines():
        if line.startswith("RESULT"):
            result = json.loads(line[6:])
    return proc.returncode, result, err


def run_heal(env_extra=None):
    return finish(spawn(env_extra))


def wait_db(timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        try:
            psycopg2.connect(DB_DSN).close()
            return True
        except psycopg2.Error:
            time.sleep(1)
    return False


def docker(*args):
    subprocess.run(["docker", *args], check=True, capture_output=True)


def wait_for(sql_text, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if sql(sql_text, fetch=True)[0][0]:
                return True
        except psycopg2.Error:
            pass
        time.sleep(0.3)
    return False


class Harness:
    def __init__(self):
        self.P = snapshot()
        self.canary = sql("SELECT id, expected_doc_ids FROM canary_set", fetch=True)
        self.C = None

    def fresh(self, injector=None):
        sql("TRUNCATE maintenance_events CASCADE")
        reset_pristine(self.P, self.canary)
        if injector:
            injector()
        self.C = snapshot()

    def invariants(self, settled):
        """Returns list of violated invariants (empty == all hold)."""
        bad = []
        if sql("SELECT count(*) FROM maintenance_events WHERE status='RUNNING'", fetch=True)[0][0]:
            bad.append("RUNNING event left behind")
        now = snapshot()
        if set(now) != set(self.P):
            bad.append(f"document set changed ({len(now)} vs {len(self.P)})")
        torn = sum(1 for i, v in now.items()
                   if (v[1], v[2]) not in {(self.P[i][1], self.P[i][2]), (self.C[i][1], self.C[i][2])})
        if torn:
            bad.append(f"{torn} torn rows")
        if settled:
            if inconsistent_rows():
                bad.append("rows inconsistent with their text after settle")
            if sql("SELECT to_regclass('public.documents_embedding_hnsw_new') IS NOT NULL", fetch=True)[0][0]:
                bad.append("leftover _new index")
            if not sql("""SELECT coalesce(bool_and(i.indisvalid), false) FROM pg_index i
                          JOIN pg_class c ON c.oid = i.indexrelid
                          WHERE c.relname = 'documents_embedding_hnsw'""", fetch=True)[0][0]:
                bad.append("HNSW index missing or invalid after settle")
        return bad

    def state_equals_corrupted(self):
        now = snapshot()
        return all((now[i][1], now[i][2]) == (self.C[i][1], self.C[i][2]) for i in self.C)

    def settle(self):
        rc, res, err = run_heal()
        return rc, res, err


def ok(cond, label, failures):
    if not cond:
        failures.append(label)


def t_crash(point, injector, batch=None):
    def run(h):
        f = []
        h.fresh(injector)
        env = {"SELFHEAL_CRASH_AT": point}
        if batch:
            env["SELFHEAL_BATCH_SIZE"] = str(batch)
        rc, _, _ = run_heal(env)
        ok(rc == 137, f"healer did not die at {point} (rc={rc})", f)
        ok(sql("SELECT count(*) FROM maintenance_events WHERE status='RUNNING'", fetch=True)[0][0] == 1
           or point == "mid_index", "expected one orphaned RUNNING event", f)
        f += [f"after crash: {x}" for x in h.invariants(False) if "RUNNING" not in x]
        rc, res, err = h.settle()
        ok(rc == 0 and res is not None, f"follow-up heal crashed: {err[-200:]}", f)
        ok(res and res["outcome"] == "SUCCEEDED", f"follow-up outcome {res and res['outcome']}", f)
        ok(res and len(res["recovered"]) >= (0 if point == "mid_index" else 1), "orphan not recovered", f)
        f += [f"settled: {x}" for x in h.invariants(True)]
        return f, {"followup": res}
    return run


def t_recovery_is_exact(point, batch=None):
    """Recovery alone must restore the exact pre-heal (corrupted) state."""
    def run(h):
        f = []
        h.fresh(lambda: inject_model_drift(25.0, 42))
        env = {"SELFHEAL_CRASH_AT": point}
        if batch:
            env["SELFHEAL_BATCH_SIZE"] = str(batch)
        run_heal(env)
        partial = sql("SELECT count(*) FROM embedding_backups", fetch=True)[0][0]
        recovered = _recover_orphans()
        ok(len(recovered) == 1, "no orphan recovered", f)
        ok(h.state_equals_corrupted(), "recovery did not restore the exact pre-heal state", f)
        f += h.invariants(False)
        return f, {"backed_up_rows_before_recovery": partial}
    return run


def t_sigkill(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    proc = spawn({"SELFHEAL_SLEEP_AT": "mid_reembed:60", "SELFHEAL_BATCH_SIZE": "40"})
    ok(wait_for("SELECT count(*) > 0 FROM embedding_backups", 90), "healer never reached mid-repair", f)
    proc.kill()  # genuine SIGKILL from outside
    proc.communicate()
    ok(sql("SELECT count(*) FROM maintenance_events WHERE status='RUNNING'", fetch=True)[0][0] == 1, "no orphan", f)
    rc, res, err = h.settle()
    ok(res and res["outcome"] == "SUCCEEDED" and res["recovered"], f"follow-up {res}", f)
    f += h.invariants(True)
    return f, {"followup": res}


def t_concurrent(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    a = spawn({"SELFHEAL_SLEEP_AT": "after_open:8"})
    ok(wait_for("SELECT count(*) > 0 FROM maintenance_events WHERE status='RUNNING'", 90), "A never opened", f)
    rc_b, res_b, err_b = run_heal()
    rc_a, res_a, err_a = finish(a)
    ok(rc_b == 0 and res_b and res_b["outcome"] == "SKIPPED", f"second healer: rc={rc_b} {res_b} {err_b[-120:]}", f)
    ok(rc_a == 0 and res_a and res_a["outcome"] == "SUCCEEDED", f"first healer: {res_a}", f)
    ok(res_b and not res_b["recovered"], "second healer wrongly 'recovered' a live healer's event", f)
    f += h.invariants(True)
    return f, {"A": res_a, "B": res_b}


def t_db_event(kind):
    def run(h):
        f = []
        h.fresh(lambda: inject_model_drift(25.0, 42))
        proc = spawn({"SELFHEAL_SLEEP_AT": "mid_reembed:12", "SELFHEAL_BATCH_SIZE": "40"})
        ok(wait_for("SELECT count(*) > 0 FROM embedding_backups", 90), "never reached mid-repair", f)
        if kind == "restart":
            docker("restart", CONTAINER)
        else:
            conn = psycopg2.connect(DB_DSN)
            conn.autocommit = True
            conn.cursor().execute("""SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                                     WHERE datname = current_database() AND pid <> pg_backend_pid()""")
            conn.close()
        rc, res, err = finish(proc)
        ok(wait_db(), "database did not come back", f)
        ok(rc == 0 and res is not None, f"healer crashed instead of degrading gracefully: {err[-200:]}", f)
        ok(res and res["outcome"] == "INTERRUPTED", f"expected INTERRUPTED, got {res and res['outcome']}", f)
        rc2, res2, err2 = h.settle()
        ok(res2 and res2["outcome"] == "SUCCEEDED" and res2["recovered"], f"follow-up {res2}", f)
        f += h.invariants(True)
        return f, {"interrupted": res, "followup": res2}
    return run


def t_db_down_start(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    docker("stop", CONTAINER)
    rc, res, err = run_heal()
    docker("start", CONTAINER)
    ok(wait_db(), "database did not come back", f)
    ok(rc == 0 and res and res["outcome"] == "DB_UNAVAILABLE", f"rc={rc} {res} {err[-150:]}", f)
    rc2, res2, _ = h.settle()
    ok(res2 and res2["outcome"] == "SUCCEEDED", f"follow-up {res2}", f)
    f += h.invariants(True)
    return f, {"during_outage": res}


def t_monitor_survives_outage(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    proc = subprocess.Popen([sys.executable, "-u", "monitor.py", "--heal", "--interval", "2"], cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=dict(os.environ))
    time.sleep(8)
    docker("stop", CONTAINER)
    time.sleep(8)
    alive_during = proc.poll() is None
    docker("start", CONTAINER)
    wait_db()
    time.sleep(25)
    alive_after = proc.poll() is None
    proc.kill()
    out = proc.communicate()[0]
    ok(alive_during and alive_after, "monitor process died during the outage", f)
    ok("DB_UNAVAILABLE" in out, "outage not reported", f)
    ok(out.rstrip().count("Heal outcome") >= 3, "monitor did not resume cycling", f)
    f += h.invariants(True) if sql("SELECT count(*) FROM maintenance_events", fetch=True)[0][0] else []
    return f, {}


def t_writer_lock(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    stale_id = sql("SELECT id FROM documents WHERE embedding_model_version <> (SELECT embedding_model_version FROM documents ORDER BY id LIMIT 1 OFFSET 0) LIMIT 1", fetch=True)
    stale_id = stale_id[0][0] if stale_id else sql("SELECT id FROM documents ORDER BY id LIMIT 1", fetch=True)[0][0]
    writer = psycopg2.connect(DB_DSN)
    writer.cursor().execute("SELECT 1 FROM documents WHERE id = %s FOR UPDATE", (stale_id,))
    t0 = time.time()
    rc, res, err = run_heal()
    waited = time.time() - t0
    writer.rollback()
    writer.close()
    ok(rc == 0 and res and res["outcome"] == "FAILED", f"expected transient FAILED, got {res} {err[-150:]}", f)
    ok(h.state_equals_corrupted(), "failed repair left a torn state", f)
    ev = sql("SELECT status, parameters->>'transient' FROM maintenance_events ORDER BY id DESC LIMIT 1", fetch=True)[0]
    ok(ev == ("FAILED", "true"), f"event not marked transient: {ev}", f)
    rc2, res2, _ = h.settle()
    ok(res2 and res2["outcome"] == "SUCCEEDED", f"retry after lock release blocked/failed: {res2}", f)
    f += h.invariants(True)
    return f, {"waited_s": round(waited, 1)}


def t_user_update_survives_rollback(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    event = sql("""INSERT INTO maintenance_events (issues, diagnosis, status) VALUES ('{}', 'test', 'RUNNING')
                   RETURNING id""", fetch=True)[0][0]
    repaired = reembed_stale(event)
    victim = sql("SELECT document_id FROM embedding_backups WHERE event_id = %s ORDER BY 1 LIMIT 1", (event,), fetch=True)[0][0]
    custom = "[" + ",".join(["0.125"] * len(json.loads(sql("SELECT embedding::text FROM documents WHERE id=%s", (victim,), fetch=True)[0][0]))) + "]"
    sql("UPDATE documents SET embedding = %s::vector WHERE id = %s", (custom, victim))
    restored, skipped = rollback_reembed(event)
    kept = sql("SELECT embedding = %s::vector FROM documents WHERE id = %s", (custom, victim), fetch=True)[0][0]
    ok(kept, "rollback clobbered a user's later update", f)
    ok(skipped == 1 and restored == repaired - 1, f"restored={restored} skipped={skipped} repaired={repaired}", f)
    sql("UPDATE maintenance_events SET status='ROLLED_BACK' WHERE id=%s", (event,))
    return f, {"restored": restored, "skipped": skipped}


def t_edit_during_embedding(h):
    """A user edits a row while the healer is embedding (no lock held): the edit must
    survive, the healer must skip that row, and writers must not be blocked."""
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    victim = sql("SELECT id FROM documents WHERE embedding_model_version <> %s ORDER BY id LIMIT 1",
                 (h.P[min(h.P)][2],), fetch=True)[0][0]
    proc = spawn({"SELFHEAL_SLEEP_AT": "after_embed:10"})
    ok(wait_for("SELECT count(*) > 0 FROM maintenance_events WHERE status='RUNNING'", 90), "healer never started", f)
    time.sleep(3)  # healer is now inside its embed->write window
    t0 = time.time()
    sql("UPDATE documents SET body = body || ' user edit during repair' WHERE id = %s", (victim,))
    write_latency = time.time() - t0
    ok(write_latency < 1.0, f"user write blocked for {write_latency:.1f}s by the healer", f)
    rc, res, err = finish(proc)
    edited = sql("SELECT body LIKE '%%user edit during repair' FROM documents WHERE id = %s", (victim,), fetch=True)[0][0]
    ok(edited, "user's edit was lost", f)
    ok(res and res["outcome"] in ("SUCCEEDED", "ROLLED_BACK", "PARTIAL"), f"healer outcome {res}", f)
    # the skipped row is still stale; the next cycle must finish the job
    rc2, res2, _ = h.settle()
    ok(res2 and res2["outcome"] in ("SUCCEEDED", "NO_ACTION"), f"follow-up {res2}", f)
    still = sql("SELECT count(*) FROM documents WHERE embedding_model_version <> %s", (h.P[min(h.P)][2],), fetch=True)[0][0]
    ok(still == 0, f"{still} stale rows left after follow-up", f)
    return f, {"write_latency_s": round(write_latency, 3), "first": res, "followup": res2}


def t_failure_memory_persists(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    from diagnose import diagnose
    sig = diagnose(["LOW_RECALL", "VERSION_SKEW", "DISTANCE_DRIFT", "TABLE_BLOAT"]).signature
    sql("""INSERT INTO maintenance_events (issues, diagnosis, actions, status, parameters, finished_at)
           VALUES ('{}', 'earlier failure', '{}', 'ROLLED_BACK', %s::jsonb, now())""",
        (json.dumps({"signature": sig}),))
    rc, res, _ = run_heal()  # brand-new process: memory must come from the database
    ok(res and res["outcome"] == "ESCALATED", f"blocked plan was retried: {res}", f)
    ok(h.state_equals_corrupted(), "blocked plan still modified data", f)
    rc, res, _ = run_heal({"SELFHEAL_RETRY_COOLDOWN": "0"})  # cooldown expired
    ok(res and res["outcome"] == "SUCCEEDED", f"did not retry after cooldown: {res}", f)
    f += h.invariants(True)
    return f, {}


def t_idempotent(h):
    f = []
    h.fresh(lambda: inject_model_drift(25.0, 42))
    outs = [run_heal()[1]["outcome"] for _ in range(3)]
    ok(outs == ["SUCCEEDED", "NO_ACTION", "NO_ACTION"], f"outcomes {outs}", f)
    f += h.invariants(True)
    return f, {"outcomes": outs}


TESTS = [
    ("crash right after opening the event", t_crash("after_open", lambda: inject_model_drift(25.0, 42))),
    ("crash mid re-embed (after 1 of 3 batches)", t_crash("mid_reembed", lambda: inject_model_drift(25.0, 42), batch=40)),
    ("crash after repair, before verification", t_crash("after_repair", lambda: inject_model_drift(25.0, 42))),
    ("crash mid index build (leftover _new index)", t_crash("mid_index", fault_lab.drop_index)),
    ("recovery restores exact pre-heal state (mid re-embed)", t_recovery_is_exact("mid_reembed", batch=40)),
    ("recovery restores exact pre-heal state (after repair)", t_recovery_is_exact("after_repair")),
    ("genuine SIGKILL mid re-embed", t_sigkill),
    ("two healers at once", t_concurrent),
    ("postgres restart mid repair", t_db_event("restart")),
    ("backend terminated mid repair", t_db_event("terminate")),
    ("database down when healer starts", t_db_down_start),
    ("monitor loop survives an outage", t_monitor_survives_outage),
    ("concurrent writer holds a row lock", t_writer_lock),
    ("rollback never clobbers a user's later update", t_user_update_survives_rollback),
    ("user edits a row while healer is embedding", t_edit_during_embedding),
    ("failure memory survives restart and expires", t_failure_memory_persists),
    ("idempotent: repeated cycles", t_idempotent),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--only")
    args = parser.parse_args()
    h = Harness()
    results = []
    sql("ALTER TABLE documents SET (autovacuum_enabled = false)")
    try:
        for name, fn in TESTS:
            if args.only and args.only not in name:
                continue
            started = time.time()
            try:
                failures, info = fn(h)
            except Exception as exc:  # a harness/system exception is a failure, with the reason
                failures, info = [f"exception: {type(exc).__name__}: {exc}"], {}
            results.append({"test": name, "pass": not failures, "failures": failures,
                            "seconds": round(time.time() - started, 1), "info": info})
            print(f"{'PASS' if not failures else 'FAIL'}  {name}  ({results[-1]['seconds']}s)")
            for x in failures:
                print(f"        - {x}")
    finally:
        h.fresh()
        sql("ALTER TABLE documents RESET (autovacuum_enabled)")
    passed = sum(r["pass"] for r in results)
    print(f"\n{passed}/{len(results)} fault-tolerance tests passed")
    if args.out:
        json.dump(results, open(args.out, "w"), indent=2, default=str)


if __name__ == "__main__":
    main()
