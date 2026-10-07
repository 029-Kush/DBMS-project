"""Run the complete, self-cleaning Phase 3 model-drift experiment."""
import argparse

import psycopg2

from config import DB_DSN
from eval_recall import main as run_health_check
from inject_fault import (
    active_run,
    inject_model_drift,
    restore_active_fault,
)


def update_run_snapshots(run_id, degraded_id=None, recovered_id=None):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE fault_runs
               SET degraded_snapshot_id = COALESCE(%s, degraded_snapshot_id),
                   recovered_snapshot_id = COALESCE(%s, recovered_snapshot_id)
               WHERE id = %s""",
            (degraded_id, recovered_id, run_id),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def vacuum_documents():
    # VACUUM cannot run inside a transaction block.
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("VACUUM ANALYZE documents")
    finally:
        cur.close()
        conn.close()


def has_active_fault():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        return active_run(cur) is not None
    finally:
        cur.close()
        conn.close()


def run_experiment(percentage, seed):
    run_id = None
    restored = False
    try:
        print("\n===== 1. PRE-FAULT BASELINE =====")
        before = run_health_check(note="phase3_pre_fault")

        print("\n===== 2. INJECT INCOMPATIBLE EMBEDDINGS =====")
        run_id = inject_model_drift(percentage, seed)

        print("\n===== 3. MEASURE DEGRADATION =====")
        degraded = run_health_check(note=f"phase3_degraded_run_{run_id}")
        update_run_snapshots(run_id, degraded_id=degraded["snapshot_id"])

        print("\n===== 4. RESTORE EXACT EMBEDDINGS =====")
        restore_active_fault()
        restored = True

        print("\n===== 5. CLEAN EXPERIMENT CHURN =====")
        vacuum_documents()

        print("\n===== 6. VERIFY FULL RECOVERY =====")
        recovered = run_health_check(note=f"phase3_recovered_run_{run_id}")
        update_run_snapshots(run_id, recovered_id=recovered["snapshot_id"])

        print("\n===== EXPERIMENT SUMMARY =====")
        print(f"fault run:       #{run_id}")
        print(f"rows affected:   {percentage:.1f}%")
        print(
            f"recall@10:       {before['recall_at_k']:.3f} -> "
            f"{degraded['recall_at_k']:.3f} -> {recovered['recall_at_k']:.3f}"
        )
        print(
            f"distance shift:  {before['distance_shift_pct']:+.1f}% -> "
            f"{degraded['distance_shift_pct']:+.1f}% -> "
            f"{recovered['distance_shift_pct']:+.1f}%"
        )
        print(
            f"status:          {before['status']} -> "
            f"{degraded['status']} -> {recovered['status']}"
        )
        return run_id
    except Exception:
        # Never deliberately leave the demo database corrupted after an
        # orchestration failure. Preserve the original exception for diagnosis.
        if run_id is not None and not restored and has_active_fault():
            try:
                restore_active_fault()
                vacuum_documents()
            except Exception as cleanup_error:
                print(f"EMERGENCY CLEANUP FAILED: {cleanup_error}")
        raise


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--percentage", type=float, default=25.0)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run_experiment(arguments.percentage, arguments.seed)
