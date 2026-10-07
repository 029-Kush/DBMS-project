"""Small unit tests for Phase 2 diagnosis logic; no database required."""
import unittest
from unittest.mock import Mock

from eval_recall import classify_health, recall_at_k
from search import search_documents


class Phase2HealthTests(unittest.TestCase):
    def test_recall_at_k_counts_expected_overlap(self):
        self.assertEqual(recall_at_k([1, 2, 3, 4], [2, 4, 8, 9]), 0.5)

    def test_healthy_measurements_have_no_issues(self):
        status, issues = classify_health(1.0, 0.0, 0.0, 0.0, True, True)
        self.assertEqual(status, "HEALTHY")
        self.assertEqual(issues, [])

    def test_missing_index_is_critical(self):
        status, issues = classify_health(1.0, 0.0, 0.0, 0.0, False, False)
        self.assertEqual(status, "CRITICAL")
        self.assertIn("INDEX_MISSING", issues)

    def test_small_table_seq_scan_is_only_a_warning(self):
        status, issues = classify_health(1.0, 0.0, 0.0, 0.0, True, False)
        self.assertEqual(status, "WARNING")
        self.assertEqual(issues, ["INDEX_NOT_USED"])

    def test_multiple_degradation_causes_are_preserved(self):
        status, issues = classify_health(1.0, 33.0, 30.0, 25.0, True, True)
        self.assertEqual(status, "DEGRADED")
        self.assertEqual(
            issues, ["VERSION_SKEW", "TABLE_BLOAT", "DISTANCE_DRIFT"]
        )

    def test_zero_query_embedding_is_rejected_before_database_search(self):
        model = Mock()
        model.embed.return_value = [[0.0, 0.0, 0.0]]
        cursor = Mock()

        with self.assertRaisesRegex(ValueError, "no vocabulary known"):
            search_documents(cursor, model, "completely unseen vocabulary")
        cursor.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
