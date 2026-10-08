"""Inject and exactly restore controlled Phase 3 vector-database faults."""
import argparse
import json

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

from config import DB_DSN
from load_data import vec_to_pg

INCOMPATIBLE_VERSION = "simulated-incompatible-v0"
# Fault types whose original vectors live in fault_embedding_backups.
RESTORABLE = ("INCOMPATIBLE_EMBEDDINGS", "CROSS_MODEL", "VECTOR_NOISE")


def parse_vector(value):
    """Convert pgvector's text representation into a NumPy vector."""
    if not isinstance(value, str):
        value = str(value)
    return np.fromstring(value.strip("[]"), sep=",", dtype=float)


def incompatible_transform(vector):
    """Deterministically change the embedding coordinate system.

    A different embedding model uses a different latent coordinate basis. A
    fixed permutation and sign change simulates that incompatibility while
    preserving vector length and norm.
    """
    transformed = np.roll(vector, 17).copy()
    transformed[::2] *= -1
    return transformed


def select_document_ids(rows, percentage, seed):
    if not 0 < percentage <= 100:
        raise ValueError("percentage must be greater than 0 and at most 100")
    count = max(1, round(len(rows) * percentage / 100))
    rng = np.random.default_rng(seed)
    return {
        int(value)
        for value in rng.choice(
            [row[0] for row in rows], size=count, replace=False
        )
    }


def latest_snapshot_id(cur):
    cur.execute("SELECT max(id) FROM health_snapshots")
    return cur.fetchone()[0]


def active_run(cur):
    cur.execute(
        """SELECT id, fault_type, parameters, affected_rows, started_at
           FROM fault_runs WHERE status = 'ACTIVE' ORDER BY id DESC LIMIT 1"""
    )
    return cur.fetchone()


def inject_model_drift(percentage, seed):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        existing = active_run(cur)
        if existing:
            raise RuntimeError(
                f"fault run #{existing[0]} ({existing[1]}) is still ACTIVE; "
                "restore it before injecting another fault"
            )

        cur.execute("SELECT id, embedding::text FROM documents ORDER BY id")
        rows = cur.fetchall()
        selected_ids = select_document_ids(rows, percentage, seed)
        selected_rows = [row for row in rows if row[0] in selected_ids]
        parameters = {"percentage": percentage, "seed": seed, "shift": 17}

        cur.execute(
            """INSERT INTO fault_runs
               (fault_type, parameters, affected_rows, pre_fault_snapshot_id)
               VALUES ('INCOMPATIBLE_EMBEDDINGS', %s::jsonb, %s, %s)
               RETURNING id""",
            (json.dumps(parameters), len(selected_rows), latest_snapshot_id(cur)),
        )
        run_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO fault_embedding_backups
               (run_id, document_id, embedding, embedding_model_version)
               SELECT %s, id, embedding, embedding_model_version
               FROM documents WHERE id = ANY(%s)""",
            (run_id, list(selected_ids)),
        )

        updates = [
            (
                document_id,
                vec_to_pg(incompatible_transform(parse_vector(embedding))),
                INCOMPATIBLE_VERSION,
            )
            for document_id, embedding in selected_rows
        ]
        execute_values(
            cur,
            """UPDATE documents AS d
               SET embedding = changed.embedding::vector,
                   embedding_model_version = changed.model_version
               FROM (VALUES %s) AS changed(id, embedding, model_version)
               WHERE d.id = changed.id""",
            updates,
            template="(%s, %s, %s)",
        )
        conn.commit()

        print(f"Injected INCOMPATIBLE_EMBEDDINGS fault run #{run_id}")
        print(f"Affected rows: {len(selected_rows)}/{len(rows)} ({percentage:.1f}%)")
        print(f"Backup rows:   {len(selected_rows)}")
        print("Next: run monitor.py, then restore with: python inject_fault.py restore")
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def restore_active_fault():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        run = active_run(cur)
        if not run:
            raise RuntimeError("there is no active fault to restore")
        run_id, fault_type, _parameters, affected_rows, _started_at = run

        if fault_type in RESTORABLE:
            cur.execute(
                """UPDATE documents AS d
                   SET embedding = backup.embedding,
                       embedding_model_version = backup.embedding_model_version
                   FROM fault_embedding_backups AS backup
                   WHERE backup.run_id = %s AND d.id = backup.document_id""",
                (run_id,),
            )
            if cur.rowcount != affected_rows:
                raise RuntimeError(
                    f"restore expected {affected_rows} rows but updated {cur.rowcount}"
                )
        else:
            raise RuntimeError(f"restore is not implemented for {fault_type}")

        cur.execute(
            """UPDATE fault_runs
               SET status = 'RESTORED', restored_at = now()
               WHERE id = %s""",
            (run_id,),
        )
        conn.commit()
        print(f"Restored fault run #{run_id}: {fault_type} ({affected_rows} rows)")
        print("Next: run monitor.py to verify recovery.")
        return run_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def show_status():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        run = active_run(cur)
        if not run:
            print("No active fault.")
            return
        run_id, fault_type, parameters, affected_rows, started_at = run
        print(f"ACTIVE fault run #{run_id}")
        print(f"  type:          {fault_type}")
        print(f"  affected rows: {affected_rows}")
        print(f"  parameters:    {parameters}")
        print(f"  started at:    {started_at}")
    finally:
        cur.close()
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    drift = subparsers.add_parser(
        "model-drift", help="make a percentage of embeddings incompatible"
    )
    drift.add_argument("--percentage", type=float, default=25.0)
    drift.add_argument("--seed", type=int, default=42)

    subparsers.add_parser("restore", help="exactly restore the active fault")
    subparsers.add_parser("status", help="show the active fault")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "model-drift":
        inject_model_drift(args.percentage, args.seed)
    elif args.command == "restore":
        restore_active_fault()
    elif args.command == "status":
        show_status()


if __name__ == "__main__":
    main()
