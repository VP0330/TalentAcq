from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import common  # noqa: E402
from common import (  # noqa: E402
    FIT_LABELS,
    assessment_path_for,
    is_within,
    resume_name_from_assessment,
)


class CommonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_is_within_true_for_child(self) -> None:
        self.assertTrue(is_within(self.tmp / "a" / "b.txt", self.tmp / "a"))

    def test_is_within_false_for_sibling_prefix(self) -> None:
        self.assertFalse(is_within(self.tmp / "ab" / "x", self.tmp / "a"))

    def test_paths_round_trip(self) -> None:
        md = assessment_path_for(self.tmp, "José (1).pdf")
        self.assertEqual(resume_name_from_assessment(md), "José (1).pdf")

    def test_fit_labels_exact(self) -> None:
        self.assertEqual(FIT_LABELS, ("Strong match", "Possible match", "Weak match"))


if __name__ == "__main__":
    unittest.main()
