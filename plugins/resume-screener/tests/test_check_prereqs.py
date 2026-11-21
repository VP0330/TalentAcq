from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_prereqs  # noqa: E402


class CheckPrereqsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_python_ok(self) -> None:
        self.assertIs(check_prereqs.check_python().ok, True)

    def test_pymupdf_missing_reports_fix(self) -> None:
        with mock.patch("importlib.util.find_spec", return_value=None):
            check = check_prereqs.check_pymupdf()
        self.assertFalse(check.ok)
        self.assertIn("requirements.txt", check.fix)
        self.assertTrue(check.required)

    def test_doc_converter_not_required_without_doc_files(self) -> None:
        (self.tmp / "a.pdf").write_bytes(b"%PDF")
        with mock.patch.object(check_prereqs, "find_doc_converter", return_value=None):
            checks = check_prereqs.check_all(self.tmp)
            code = check_prereqs.main(["--folder", str(self.tmp)])
        doc = [c for c in checks if "converter" in c.name.lower()][0]
        self.assertFalse(doc.required)
        self.assertEqual(code, 0)

    def test_doc_converter_required_with_doc_files(self) -> None:
        (self.tmp / "old.doc").write_bytes(b"x")
        with mock.patch.object(check_prereqs, "find_doc_converter", return_value=None):
            code = check_prereqs.main(["--folder", str(self.tmp)])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
