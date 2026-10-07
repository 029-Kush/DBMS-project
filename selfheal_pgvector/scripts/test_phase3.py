"""Unit tests for deterministic, reversible Phase 3 transformations."""
import unittest

import numpy as np

from inject_fault import incompatible_transform, parse_vector, select_document_ids


class Phase3FaultTests(unittest.TestCase):
    def test_parse_vector(self):
        np.testing.assert_allclose(parse_vector("[0.1,-0.2,0.3]"), [0.1, -0.2, 0.3])

    def test_incompatible_transform_preserves_norm_and_changes_basis(self):
        vector = np.arange(1, 65, dtype=float)
        transformed = incompatible_transform(vector)
        self.assertFalse(np.array_equal(vector, transformed))
        self.assertAlmostEqual(np.linalg.norm(vector), np.linalg.norm(transformed))

    def test_selection_is_exact_and_reproducible(self):
        rows = [(value, "[]") for value in range(1, 101)]
        first = select_document_ids(rows, 25, seed=42)
        second = select_document_ids(rows, 25, seed=42)
        self.assertEqual(len(first), 25)
        self.assertEqual(first, second)
        self.assertTrue(all(type(value) is int for value in first))

    def test_invalid_percentage_is_rejected(self):
        with self.assertRaises(ValueError):
            select_document_ids([(1, "[]")], 0, seed=42)


if __name__ == "__main__":
    unittest.main()
