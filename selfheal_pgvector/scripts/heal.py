"""The self-healing loop: check -> diagnose -> repair -> verify (or roll back).

heal_once() is one full cycle (what `monitor.py --heal` calls). Fault tolerance:
- an advisory lock admits one healer at a time; a second returns SKIPPED
- a crashed healer's orphaned RUNNING event is rolled back by the next cycle
- "this plan already failed" memory lives in maintenance_events, so it survives
  restarts, and expires after RETRY_COOLDOWN_SECONDS
- a lost database connection yields INTERRUPTED, never an unhandled crash
"""
import json

import psycopg2
from psycopg2 import errors

from config import DB_DSN, HEAL_LOCK_KEY, RETRY_COOLDOWN_SECONDS
from diagnose import (
    PRIMARY, REBUILD_INDEX, REEMBED_STALE, REPAIR_VECTORS, SCAN_VECTORS, SYMPTOMS, VACUUM,
    diagnose,
)
from eval_recall import main as run_health_check
from repair import (
    cleanup_partial_index, last_run_stats, crash_point, rebuild_index, reembed_stale, repair_vectors,
    rollback_reembed, vacuum_documents,
)

DB_LOST = (psycopg2.OperationalError, psycopg2.InterfaceError)
BLOCKING = ("FAILED", "ROLLED_BACK", "ESCALATED", "PARTIAL")


def _one(sql, params=None):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        row = cur.fetchone() if cur.description else None
        conn.commit()
        return row
    finally:
        cur.close()
        conn.close()


def _try_lock():
    """Session-level advisory lock held for the whole cycle; auto-released on death."""
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(%s)", (HEAL_LOCK_KEY,))
    if cur.fetchone()[0]:
        return conn
    conn.close()
    return None


def _open_event(issues, diagnosis, pre_snapshot_id, status="RUNNING"):
    return _one(
        """INSERT INTO maintenance_events
           (issues, diagnosis, actions, parameters, pre_snapshot_id, status)
           VALUES (%s, %s, %s, %s::jsonb, %s, %s) RETURNING id""",
        (
            issues,
            "; ".join(diagnosis.notes),
            list(diagnosis.actions),
            json.dumps({
                "signature": diagnosis.signature,
                "explained": sorted(diagnosis.explained),
                "investigating": sorted(diagnosis.investigating),
            }),
            pre_snapshot_id,
            status,
        ),
    )[0]


def _close_event(event_id, status, post_snapshot_id=None, affected=0, error=None, details=None):
    _one(
        """UPDATE maintenance_events
           SET status = %s, post_snapshot_id = %s, affected_rows = %s,
               error_message = %s, finished_at = now(),
               parameters = parameters || %s::jsonb
           WHERE id = %s""",
        (status, post_snapshot_id, affected, error, json.dumps(details or {}), event_id),
    )


def _recently_blocked(signature):
    """Did this exact plan already fail/escalate within the cooldown window?"""
    row = _one(
        """SELECT status FROM maintenance_events
           WHERE parameters->>'signature' = %s
             AND started_at > now() - make_interval(secs => %s)
             AND status <> 'RUNNING'
             AND coalesce(parameters->>'transient', 'false') <> 'true'
           ORDER BY id DESC LIMIT 1""",
        (signature, RETRY_COOLDOWN_SECONDS),
    )
    return bool(row and row[0] in BLOCKING)


def _mark_fault_healed(event_id):
    """A fully verified repair closes a still-ACTIVE fault-lab run as HEALED."""
    _one(
        """WITH healed AS (
               UPDATE fault_runs SET status = 'HEALED', restored_at = now()
               WHERE status = 'ACTIVE' RETURNING id)
           UPDATE maintenance_events SET fault_run_id = (SELECT id FROM healed)
           WHERE id = %s""",
        (event_id,),
    )


def _recover_orphans():
    """We hold the lock, so any RUNNING event belongs to a dead healer. Undo it."""
    recovered = []
    cleanup_partial_index()
    orphans = []
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("SELECT id FROM maintenance_events WHERE status = 'RUNNING' ORDER BY id")
    orphans = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    for event_id in orphans:
        restored, skipped = rollback_reembed(event_id)
        _close_event(
            event_id, "ROLLED_BACK",
            error="orphaned RUNNING event (healer died); rolled back on recovery",
            # transient: an interrupted repair is not evidence the plan is bad, so it must
            # not feed the "this plan already failed" memory.
            details={"recovered": True, "transient": True, "restored": restored,
                     "skipped_user_changed": skipped},
        )
        recovered.append(event_id)
    return recovered


def _run_action(action, event_id):
    """Run one action. Returns (rows_repaired, details)."""
    if action == REEMBED_STALE:
        rows = reembed_stale(event_id)
        return rows, {"skipped_concurrent_reembed": last_run_stats["skipped_concurrent"]}
    if action in (REPAIR_VECTORS, SCAN_VECTORS):
        scanned, repaired = repair_vectors(event_id)
        return repaired, {"scanned": scanned, "skipped_concurrent_scan": last_run_stats["skipped_concurrent"]}
    if action == REBUILD_INDEX:
        return rebuild_index(), {}
    if action == VACUUM:
        return vacuum_documents(), {}
    raise RuntimeError(f"unknown action {action}")


def _cycle(note):
    summary = {"before": None, "after": None, "event_id": None, "outcome": "NO_ACTION",
               "actions": (), "recovered": []}
    summary["recovered"] = _recover_orphans()

    before = run_health_check(note=f"{note}_pre")
    diagnosis = diagnose(before["issues"])
    summary.update(before=before, actions=diagnosis.actions)
    if not diagnosis.actionable:
        return summary

    if _recently_blocked(diagnosis.signature):
        summary["outcome"] = "ESCALATED"  # same plan failed/escalated recently: a human decides
        return summary

    try:
        event_id = _open_event(before["issues"], diagnosis, before["snapshot_id"])
    except errors.UniqueViolation:
        summary["outcome"] = "SKIPPED"
        return summary
    summary["event_id"] = event_id
    crash_point("after_open")

    wrote_data = False
    affected, details = 0, {}
    try:
        for action in diagnosis.actions:
            rows, info = _run_action(action, event_id)
            details.update(info)
            if action in (REEMBED_STALE, REPAIR_VECTORS, SCAN_VECTORS):
                wrote_data = wrote_data or rows > 0
                affected += rows
    except errors.LockNotAvailable as exc:
        # A concurrent writer held a row lock past lock_timeout. Transient: undo
        # and allow an immediate retry next cycle (not counted as a failed plan).
        try:
            if wrote_data:
                rollback_reembed(event_id)
            details["transient"] = True
            _close_event(event_id, "FAILED", affected=affected,
                         error=f"lock timeout: {str(exc).strip()[:120]}", details=details)
            summary.update(outcome="FAILED", error=str(exc))
        except DB_LOST:
            summary.update(outcome="INTERRUPTED", error=str(exc))
        return summary
    except DB_LOST as exc:
        # Connection lost mid-repair. Leave the event RUNNING; the next cycle's
        # recovery (which holds the lock) rolls it back deterministically.
        summary.update(outcome="INTERRUPTED", error=str(exc))
        return summary
    except Exception as exc:
        try:
            if wrote_data:
                rollback_reembed(event_id)
            _close_event(event_id, "FAILED", affected=affected, error=str(exc), details=details)
            summary.update(outcome="FAILED", error=str(exc))
        except DB_LOST:
            summary.update(outcome="INTERRUPTED", error=str(exc))
        return summary

    crash_point("after_repair")
    after = run_health_check(note=f"{note}_post_event_{event_id}")
    summary["after"] = after
    details["scan_repaired_rows"] = affected

    failed_primary = diagnosis.explained & set(after["issues"])
    if failed_primary:
        if wrote_data:
            rollback_reembed(event_id)
            vacuum_documents()
        _close_event(
            event_id, "ROLLED_BACK", post_snapshot_id=after["snapshot_id"], affected=affected,
            error=f"verification failed; still present: {sorted(failed_primary)}", details=details,
        )
        summary["outcome"] = "ROLLED_BACK"
        return summary

    residual = (set(before["issues"]) & SYMPTOMS) & set(after["issues"])
    if residual:
        # Our repairs held but a symptom persists with no further explanation.
        did_something = bool(diagnosis.explained) or affected > 0
        status = "PARTIAL" if did_something else "ESCALATED"
        details["residual"] = sorted(residual)
        _close_event(event_id, status, post_snapshot_id=after["snapshot_id"],
                     affected=affected, details=details)
        summary["outcome"] = status
        return summary

    _close_event(event_id, "SUCCEEDED", post_snapshot_id=after["snapshot_id"],
                 affected=affected, details=details)
    _mark_fault_healed(event_id)
    summary["outcome"] = "SUCCEEDED"
    return summary


def heal_once(note="heal"):
    """One locked, crash-recovering check/diagnose/repair/verify cycle."""
    try:
        lock = _try_lock()
    except DB_LOST as exc:
        return {"outcome": "DB_UNAVAILABLE", "error": str(exc), "actions": (), "recovered": [],
                "before": None, "after": None, "event_id": None}
    if lock is None:
        return {"outcome": "SKIPPED", "actions": (), "recovered": [],
                "before": None, "after": None, "event_id": None}
    try:
        return _cycle(note)
    except DB_LOST as exc:
        return {"outcome": "DB_UNAVAILABLE", "error": str(exc), "actions": (), "recovered": [],
                "before": None, "after": None, "event_id": None}
    finally:
        try:
            lock.close()
        except Exception:
            pass


def heal_until_stable(note="heal", max_cycles=6):
    """Repeat cycles while they make progress (staged diagnosis needs >1 cycle)."""
    history = []
    for i in range(max_cycles):
        out = heal_once(f"{note}_c{i + 1}")
        history.append(out)
        if out["outcome"] not in ("SUCCEEDED", "PARTIAL"):
            break
    return history
