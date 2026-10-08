"""Phase 4/5 tests: diagnosis rules, health classification, and healer control
flow with every database call mocked. Live behaviour is covered by the
fault-tolerance and benchmark experiments."""
import unittest
from unittest.mock import patch

import heal
from diagnose import (
    REBUILD_INDEX, REEMBED_STALE, REPAIR_VECTORS, SCAN_VECTORS, VACUUM, diagnose,
)
from eval_recall import classify_health


def snap(issues, status, sid):
    return {"snapshot_id": sid, "status": status, "issues": issues}


class DiagnoseTests(unittest.TestCase):
    def test_healthy_has_no_plan(self):
        self.assertFalse(diagnose([]).actionable)

    def test_index_not_used_alone_is_ignored(self):
        self.assertFalse(diagnose(["INDEX_NOT_USED"]).actionable)

    def test_version_skew_reembeds_then_vacuums(self):
        self.assertEqual(diagnose(["VERSION_SKEW"]).actions, (REEMBED_STALE, VACUUM))

    def test_model_drift_signature_needs_no_investigation(self):
        d = diagnose(["LOW_RECALL", "VERSION_SKEW", "DISTANCE_DRIFT", "TABLE_BLOAT"])
        self.assertEqual(d.actions, (REEMBED_STALE, VACUUM))
        self.assertEqual(d.investigating, frozenset())

    def test_vector_mismatch_repairs_and_explains_symptoms(self):
        d = diagnose(["LOW_RECALL", "VECTOR_MISMATCH"])
        self.assertEqual(d.actions, (REPAIR_VECTORS, VACUUM))
        self.assertEqual(d.investigating, frozenset())

    def test_missing_index_rebuilds(self):
        self.assertEqual(diagnose(["INDEX_MISSING", "INDEX_NOT_USED"]).actions, (REBUILD_INDEX,))

    def test_degraded_index_rebuilds_and_explains_recall_loss(self):
        d = diagnose(["INDEX_DEGRADED", "LOW_RECALL", "DISTANCE_DRIFT"])
        self.assertEqual(d.actions, (REBUILD_INDEX,))
        self.assertEqual(d.investigating, frozenset())

    def test_order_is_data_then_index_then_vacuum(self):
        d = diagnose(["TABLE_BLOAT", "INDEX_MISSING", "VERSION_SKEW", "VECTOR_MISMATCH"])
        self.assertEqual(d.actions, (REEMBED_STALE, REPAIR_VECTORS, REBUILD_INDEX, VACUUM))

    def test_bloat_alone_vacuums_only(self):
        self.assertEqual(diagnose(["TABLE_BLOAT"]).actions, (VACUUM,))

    def test_unexplained_recall_loss_triggers_investigation_not_guesswork(self):
        d = diagnose(["LOW_RECALL", "DISTANCE_DRIFT"])
        self.assertIn(SCAN_VECTORS, d.actions)
        self.assertEqual(d.investigating, frozenset({"LOW_RECALL", "DISTANCE_DRIFT"}))
        self.assertEqual(d.explained, frozenset())

    def test_signature_is_stable_and_distinguishes_plans(self):
        self.assertEqual(diagnose(["VERSION_SKEW"]).signature, diagnose(["VERSION_SKEW", "LOW_RECALL"]).signature)
        self.assertNotEqual(diagnose(["VERSION_SKEW"]).signature, diagnose(["TABLE_BLOAT"]).signature)


class ClassifyTests(unittest.TestCase):
    def test_degraded_index_is_degraded_not_critical(self):
        status, issues = classify_health(1.0, 0, 0, 0, True, True, ann_recall=0.6)
        self.assertEqual((status, issues), ("DEGRADED", ["INDEX_DEGRADED"]))

    def test_sentinel_mismatch_flags_vector_mismatch(self):
        status, issues = classify_health(1.0, 0, 0, 0, True, True, 1.0, 3.0)
        self.assertEqual(issues, ["VECTOR_MISMATCH"])
        self.assertEqual(status, "DEGRADED")

    def test_invalid_or_absent_index_is_missing_even_if_ann_recall_low(self):
        _, issues = classify_health(1.0, 0, 0, 0, False, False, ann_recall=0.2)
        self.assertEqual(issues, ["INDEX_MISSING"])

    def test_ann_threshold_is_relative_to_healthy_baseline(self):
        # 0.96 is fine against a 0.97 baseline but degraded against a 1.0 baseline
        self.assertEqual(classify_health(1.0, 0, 0, 0, True, True, 0.96, 0.0, 0.97)[1], [])
        self.assertEqual(classify_health(1.0, 0, 0, 0, True, True, 0.96, 0.0, 1.0)[1], ["INDEX_DEGRADED"])

    def test_absolute_floor_applies_even_with_a_poor_baseline(self):
        self.assertEqual(classify_health(1.0, 0, 0, 0, True, True, 0.85, 0.0, 0.88)[1], ["INDEX_DEGRADED"])

    def test_defaults_keep_phase2_behaviour(self):
        self.assertEqual(classify_health(1.0, 0, 0, 0, True, True), ("HEALTHY", []))


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class FakeLock:
    def close(self):
        pass


class HealerFlowTests(unittest.TestCase):
    def patched(self, checks, run_action=None, blocked=False):
        it = iter(checks)
        self.closed, self.rollback, self.healed = Recorder(), Recorder(), Recorder()
        self.ran = []

        def default_action(action, event_id):
            self.ran.append(action)
            return (5 if action in ("REEMBED_STALE", "REPAIR_VECTORS", "SCAN_VECTORS") else 0), {}

        return patch.multiple(
            heal,
            _try_lock=lambda: FakeLock(),
            _recover_orphans=lambda: [],
            run_health_check=lambda note=None: next(it),
            _recently_blocked=lambda sig: blocked,
            _open_event=lambda *a, **k: 7,
            _close_event=self.closed,
            _mark_fault_healed=self.healed,
            _run_action=run_action or default_action,
            rollback_reembed=self.rollback,
            vacuum_documents=lambda: 0,
        )

    def statuses(self):
        return [c[0][1] for c in self.closed.calls]

    def test_success_when_issue_cleared(self):
        with self.patched([snap(["VERSION_SKEW"], "DEGRADED", 1), snap([], "HEALTHY", 2)]):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "SUCCEEDED")
        self.assertEqual(self.statuses(), ["SUCCEEDED"])
        self.assertEqual(len(self.healed.calls), 1)
        self.assertEqual(self.rollback.calls, [])

    def test_warning_after_repair_still_counts_as_success(self):
        with self.patched([snap(["VERSION_SKEW"], "DEGRADED", 1), snap(["INDEX_NOT_USED"], "WARNING", 2)]):
            self.assertEqual(heal.heal_once()["outcome"], "SUCCEEDED")

    def test_unresolved_primary_issue_rolls_back(self):
        with self.patched([snap(["VERSION_SKEW"], "DEGRADED", 1), snap(["VERSION_SKEW"], "DEGRADED", 2)]):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "ROLLED_BACK")
        self.assertEqual(len(self.rollback.calls), 1)
        self.assertEqual(self.healed.calls, [])

    def test_plan_that_failed_recently_is_not_retried(self):
        with self.patched([snap(["VERSION_SKEW"], "DEGRADED", 1)], blocked=True):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "ESCALATED")
        self.assertEqual(self.ran, [])

    def test_failed_action_rolls_back_data_repair(self):
        def boom(action, event_id):
            if action == VACUUM:
                raise RuntimeError("disk full")
            return 5, {}
        with self.patched([snap(["VERSION_SKEW"], "DEGRADED", 1)], run_action=boom):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "FAILED")
        self.assertEqual(len(self.rollback.calls), 1)

    def test_lost_connection_leaves_event_for_recovery(self):
        import psycopg2

        def dropped(action, event_id):
            raise psycopg2.OperationalError("server closed the connection")
        with self.patched([snap(["VERSION_SKEW"], "DEGRADED", 1)], run_action=dropped):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "INTERRUPTED")
        self.assertEqual(self.closed.calls, [])  # still RUNNING; next cycle recovers it

    def test_healthy_runs_nothing(self):
        with self.patched([snap([], "HEALTHY", 1)]):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "NO_ACTION")
        self.assertEqual(self.ran, [])

    def test_scan_that_finds_nothing_escalates_without_claiming_a_repair(self):
        def scan_nothing(action, event_id):
            self.ran.append(action)
            return 0, {"scanned": 480}
        checks = [snap(["LOW_RECALL"], "CRITICAL", 1), snap(["LOW_RECALL"], "CRITICAL", 2)]
        with self.patched(checks, run_action=scan_nothing):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "ESCALATED")
        self.assertEqual(self.healed.calls, [])
        self.assertEqual(self.rollback.calls, [])

    def test_scan_that_repairs_and_clears_symptom_succeeds(self):
        checks = [snap(["LOW_RECALL"], "CRITICAL", 1), snap([], "HEALTHY", 2)]
        with self.patched(checks):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "SUCCEEDED")

    def test_repair_held_but_symptom_remains_is_partial_and_keeps_work(self):
        checks = [snap(["VERSION_SKEW", "LOW_RECALL"], "CRITICAL", 1), snap(["LOW_RECALL"], "CRITICAL", 2)]
        with self.patched(checks):
            out = heal.heal_once()
        self.assertEqual(out["outcome"], "PARTIAL")
        self.assertEqual(self.rollback.calls, [])
        self.assertEqual(self.healed.calls, [])  # fault not fully healed

    def test_second_healer_is_skipped(self):
        with patch.object(heal, "_try_lock", lambda: None):
            self.assertEqual(heal.heal_once()["outcome"], "SKIPPED")

    def test_heal_until_stable_follows_staged_progress_then_stops(self):
        seq = iter(["PARTIAL", "SUCCEEDED", "NO_ACTION", "SUCCEEDED"])
        with patch.object(heal, "heal_once", lambda note=None: {"outcome": next(seq)}):
            hist = heal.heal_until_stable(max_cycles=6)
        self.assertEqual([h["outcome"] for h in hist], ["PARTIAL", "SUCCEEDED", "NO_ACTION"])


if __name__ == "__main__":
    unittest.main()
