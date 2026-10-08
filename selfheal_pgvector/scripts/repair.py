"""Targeted, crash-safe repair executors for the healer.

Design rules:
- Repairs use only the document text and the *current* model; they never read
  fault_embedding_backups (the fault lab's answer key). That would be
  restoring, not healing.
- Data repairs run in batches, one transaction each, and back up what they
  overwrite (old vector + the vector written) to embedding_backups under the
  maintenance event id. After a crash, rollback_reembed(event) reverts exactly
  the rows still holding the healer's value, never a user's later update.
- Index rebuild builds a new index CONCURRENTLY and swaps by rename, so the old
  index keeps serving queries (and writes keep flowing) during the rebuild.
"""
import os

import numpy as np
import psycopg2
from psycopg2.extras import execute_values

from config import CURRENT_MODEL_VERSION, DB_DSN, HEAL_BATCH_SIZE, SENTINEL_TOLERANCE
from embed_model import load_embedder
from load_data import vec_to_pg

INDEX_NAME = "documents_embedding_hnsw"
NEW_INDEX_NAME = INDEX_NAME + "_new"
CANONICAL_INDEX_SQL = "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"


def crash_point(name):
    """Test hooks (no effect unless the env vars are set):
    SELFHEAL_CRASH_AT=<name>        kill the process dead here (like SIGKILL)
    SELFHEAL_SLEEP_AT=<name>:<sec>  pause here, e.g. to restart the database."""
    if os.environ.get("SELFHEAL_CRASH_AT") == name:
        os._exit(137)
    pause = os.environ.get("SELFHEAL_SLEEP_AT", "")
    if pause.startswith(name + ":"):
        import time
        time.sleep(float(pause.split(":")[1]))


def _connect():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("SET lock_timeout = '5s'")  # fail fast instead of hanging on a writer
    conn.commit()
    return conn, cur


def _write_batch(cur, event_id, ids, vectors):
    """Back up and overwrite the given rows in the caller's transaction."""
    values = [(int(i), vec_to_pg(v)) for i, v in zip(ids, vectors)]
    version = cur.mogrify("%s", (CURRENT_MODEL_VERSION,)).decode()
    execute_values(
        cur,
        f"""INSERT INTO embedding_backups
            (event_id, document_id, embedding, embedding_model_version, healed_embedding)
            SELECT {int(event_id)}, d.id, d.embedding, d.embedding_model_version, v.new::vector
            FROM documents d JOIN (VALUES %s) AS v(id, new) ON v.id = d.id""",
        values, template="(%s, %s)", page_size=len(values),
    )
    execute_values(
        cur,
        f"""UPDATE documents d
            SET embedding = v.new::vector, embedding_model_version = {version},
                last_reindexed_at = now()
            FROM (VALUES %s) AS v(id, new) WHERE d.id = v.id""",
        values, template="(%s, %s)", page_size=len(values),
    )
    if cur.rowcount != len(values):
        raise RuntimeError(f"batch update touched {cur.rowcount} of {len(values)} rows")


def _require_nonzero(vectors):
    if any(not np.any(np.abs(v) > 1e-12) for v in vectors):
        raise RuntimeError("a document embeds to the zero vector; refusing to write it")


last_run_stats = {"skipped_concurrent": 0}


def _lock_and_filter(cur, rows):
    """Short write transaction step: lock the rows we are about to overwrite and
    keep only those unchanged since we read them (optimistic concurrency).

    rows are (id, body, body_md5, vector_md5, ...). Embedding runs *outside* any
    lock, so writers are never blocked by embedding time; a row a user touched in
    the meantime is skipped and left for the next cycle.
    """
    ids = [r[0] for r in rows]
    cur.execute(
        """SELECT id, md5(body), md5(embedding::text) FROM documents
           WHERE id = ANY(%s) ORDER BY id FOR UPDATE""",
        (ids,),
    )
    current = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    keep = [i for i, r in enumerate(rows) if current.get(r[0]) == (r[2], r[3])]
    return keep


RETRY_PASSES = 3  # a row a user touched mid-repair is retried a few times, then left


def _reembed_pass(conn, cur, model, event_id, batch):
    done, skipped = 0, set()
    while True:
        cur.execute(
            """SELECT id, body, md5(body), md5(embedding::text) FROM documents d
               WHERE embedding_model_version <> %s
                 AND NOT (id = ANY(%s))
                 AND NOT EXISTS (SELECT 1 FROM embedding_backups b
                                 WHERE b.event_id = %s AND b.document_id = d.id)
               ORDER BY id LIMIT %s""",
            (CURRENT_MODEL_VERSION, list(skipped), event_id, batch),
        )
        rows = cur.fetchall()
        conn.commit()  # release the read snapshot; no locks held while embedding
        if not rows:
            return done, skipped
        vectors = model.embed([r[1] for r in rows])
        _require_nonzero(vectors)
        crash_point("after_embed")  # test hook: window where a user can edit a row
        keep = _lock_and_filter(cur, rows)
        skipped |= {r[0] for i, r in enumerate(rows) if i not in set(keep)}
        if keep:
            _write_batch(cur, event_id, [rows[i][0] for i in keep], vectors[keep])
        conn.commit()
        done += len(keep)
        crash_point("mid_reembed")


def reembed_stale(event_id, batch=None):
    """Re-embed every row labelled with a non-current model. Returns rows rewritten."""
    batch = batch or HEAL_BATCH_SIZE
    model = load_embedder()
    conn, cur = _connect()
    total = 0
    try:
        for _attempt in range(RETRY_PASSES):
            done, skipped = _reembed_pass(conn, cur, model, event_id, batch)
            total += done
            if not skipped:
                break
        last_run_stats["skipped_concurrent"] = len(skipped)
        return total
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _scan_rows(conn, cur, model, event_id, rows):
    """Compare a batch of rows with fresh embeddings; rewrite the mismatched.
    Returns (repaired, still_skipped_ids)."""
    fresh = model.embed([r[1] for r in rows])
    stored = np.array([np.fromstring(r[4].strip("[]"), sep=",") for r in rows])
    cosine = 1.0 - np.sum(fresh * stored, axis=1) / (
        np.linalg.norm(fresh, axis=1) * np.linalg.norm(stored, axis=1)
    )
    bad = [i for i, d in enumerate(cosine) if d > SENTINEL_TOLERANCE]
    crash_point("after_embed")
    repaired, skipped = 0, []
    if bad:
        _require_nonzero(fresh[bad])
        suspects = [rows[i] for i in bad]
        keep = _lock_and_filter(cur, suspects)
        skipped = [suspects[i][0] for i in range(len(suspects)) if i not in set(keep)]
        if keep:
            _write_batch(cur, event_id, [suspects[i][0] for i in keep], fresh[[bad[i] for i in keep]])
        repaired = len(keep)
    conn.commit()
    return repaired, skipped


_SCAN_COLUMNS = """SELECT id, body, md5(body), md5(embedding::text), embedding::text FROM documents d
                   WHERE embedding_model_version = %s
                     AND NOT EXISTS (SELECT 1 FROM embedding_backups b
                                     WHERE b.event_id = %s AND b.document_id = d.id)"""


def repair_vectors(event_id, batch=None):
    """Exhaustive scan: re-embed current-labelled rows, rewrite only the mismatched.

    Returns (rows_scanned, rows_repaired). Cost note: localising silent
    corruption needs one embedding per row, so compute is O(corpus) even though
    writes are O(corrupted rows). No locks are held while embedding; rows a user
    modified mid-repair are retried a few times.
    """
    batch = batch or HEAL_BATCH_SIZE
    model = load_embedder()
    conn, cur = _connect()
    scanned = repaired = last_id = 0
    retry = []
    try:
        while True:
            cur.execute(_SCAN_COLUMNS + " AND id > %s ORDER BY id LIMIT %s",
                        (CURRENT_MODEL_VERSION, event_id, last_id, batch))
            rows = cur.fetchall()
            conn.commit()
            if not rows:
                break
            fixed, skipped = _scan_rows(conn, cur, model, event_id, rows)
            repaired += fixed
            retry += skipped
            scanned += len(rows)
            last_id = rows[-1][0]
            crash_point("mid_reembed")
        for _attempt in range(RETRY_PASSES):
            if not retry:
                break
            cur.execute(_SCAN_COLUMNS + " AND id = ANY(%s) ORDER BY id",
                        (CURRENT_MODEL_VERSION, event_id, retry))
            rows = cur.fetchall()
            conn.commit()
            fixed, retry = _scan_rows(conn, cur, model, event_id, rows) if rows else (0, [])
            repaired += fixed
        last_run_stats["skipped_concurrent"] = len(retry)
        return scanned, repaired
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def rollback_reembed(event_id):
    """Undo a data repair from the healer's own backup. Returns (restored, skipped).

    Only rows that still hold the healer's value are reverted; rows a user
    changed since are left alone (skipped).
    """
    conn, cur = _connect()
    try:
        cur.execute("SELECT count(*) FROM embedding_backups WHERE event_id = %s", (event_id,))
        total = cur.fetchone()[0]
        cur.execute(
            """UPDATE documents AS d
               SET embedding = b.embedding,
                   embedding_model_version = b.embedding_model_version
               FROM embedding_backups AS b
               WHERE b.event_id = %s AND d.id = b.document_id
                 AND d.embedding = b.healed_embedding""",
            (event_id,),
        )
        restored = cur.rowcount
        conn.commit()
        return restored, total - restored
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def cleanup_partial_index():
    """Drop a leftover half-built replacement index from an interrupted rebuild."""
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(f"DROP INDEX IF EXISTS {NEW_INDEX_NAME}")
    finally:
        cur.close()
        conn.close()


def rebuild_index():
    """Build a canonical HNSW index concurrently, then swap it in by rename."""
    cleanup_partial_index()
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True  # CREATE INDEX CONCURRENTLY cannot run in a transaction
    cur = conn.cursor()
    try:
        cur.execute(f"CREATE INDEX CONCURRENTLY {NEW_INDEX_NAME} ON documents {CANONICAL_INDEX_SQL}")
        crash_point("mid_index")
        cur.execute("BEGIN")
        cur.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
        cur.execute(f"ALTER INDEX {NEW_INDEX_NAME} RENAME TO {INDEX_NAME}")
        cur.execute("COMMIT")
        return 0
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        raise
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
        return 0
    finally:
        cur.close()
        conn.close()
