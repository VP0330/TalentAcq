from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_assessment.py"

HEAD = (
    "# Candidate: a.pdf\nFit: {fit}\nRequirements met: 1/1 must-haves shown\n\n"
    "## Requirements\n| # | Requirement (from JD) | Type | Status | Evidence from resume |\n"
    "|---|---|---|---|---|\n| 1 | R | must | Shown | \"Sold software daily\" |\n\n"
    "## Experience overview\nx\n\n## Strengths\n- a\n\n## Gaps\n- b\n\n"
    "## Suggested screen questions\n- q\n"
)


STUB = "# Candidate: a.pdf\nStatus: Needs manual review\n"


def run(payload):
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class HookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "role"
        self.adir = self.root / "assessments"
        (self.adir / ".text").mkdir(parents=True)
        (self.adir / ".text" / "a.pdf.txt").write_text("I Sold software daily for years", encoding="utf-8")
        self._manifest("ok")
        self.path = self.adir / "a.pdf.md"

    def payload(self, p=None, tool="Write"):
        p = str(p or self.path)
        if tool == "Edit":
            ti = {"file_path": p, "old_string": "x", "new_string": "y"}
        else:
            ti = {"file_path": p, "content": "ignored"}
        return {"tool_name": tool, "tool_input": ti, "cwd": str(self.root)}

    def test_hook_blocks_invalid_with_exit_2(self):
        self.path.write_text(HEAD.format(fit="Weak match"), encoding="utf-8")
        r = run(self.payload())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Assessment check failed", r.stderr)

    def test_hook_ignores_other_files(self):
        p = self.root / "notes.md"
        p.write_text("[x](http://a)", encoding="utf-8")
        self.assertEqual(run(self.payload(p)).returncode, 0)

    def test_hook_ignores_underscore_files(self):
        p = self.adir / "_summary.md"
        p.write_text("[x](http://a)", encoding="utf-8")
        self.assertEqual(run(self.payload(p)).returncode, 0)

    def test_hook_reads_file_from_disk_for_edit(self):
        self.path.write_text(HEAD.format(fit="Strong match"), encoding="utf-8")
        self.assertEqual(run(self.payload(tool="Edit")).returncode, 0)
        self.path.write_text(HEAD.format(fit="Weak match"), encoding="utf-8")
        self.assertEqual(run(self.payload(tool="Edit")).returncode, 2)


    def _manifest(self, status):
        (self.adir / ".text" / "_extract.json").write_text(
            json.dumps({"files": {"a.pdf": {"status": status}}}), encoding="utf-8")

    def test_stub_rejected_for_readable_resume(self):
        self._manifest("ok")
        self.path.write_text(STUB, encoding="utf-8")
        r = run(self.payload())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("extracted successfully", r.stderr)
        self.assertIn("required — a manual-review stub", r.stderr)

    def test_failure_feedback_is_utf8_with_non_ascii_quote(self):
        quote = "“José’s team” — led"
        md = HEAD.format(fit="Strong match").replace('"Sold software daily"', f'"{quote}"')
        self.path.write_text(md, encoding="utf-8")
        r = run(self.payload())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Assessment check failed", r.stderr)
        self.assertIn(quote, r.stderr)

    def test_ignores_assessments_folder_without_extract_manifest(self):
        (self.adir / ".text" / "_extract.json").unlink()
        self.path.write_text(HEAD.format(fit="Weak match"), encoding="utf-8")
        self.assertEqual(run(self.payload()).returncode, 0)

    def test_path_resolved_and_case_insensitive(self):
        self.path.write_text(HEAD.format(fit="Weak match"), encoding="utf-8")
        odd = str(self.adir / ".text" / ".." / "a.pdf.md")
        self.assertEqual(run(self.payload(odd)).returncode, 2)
        if sys.platform == "win32":
            upper = str(self.root / "ASSESSMENTS" / "a.pdf.md")
            self.assertEqual(run(self.payload(upper)).returncode, 2)

    def test_malformed_scan_json_blocks_visibly(self):
        self.path.write_text(HEAD.format(fit="Strong match"), encoding="utf-8")
        (self.adir / ".text" / "_scan.json").write_text("{broken", encoding="utf-8")
        r = run(self.payload())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("scan record unreadable", r.stderr.lower())

    def test_requirements_file_enforced(self):
        self.path.write_text(HEAD.format(fit="Strong match"), encoding="utf-8")
        (self.adir / "_requirements.md").write_text("1. [must] R\n", encoding="utf-8")
        self.assertEqual(run(self.payload()).returncode, 0)
        (self.adir / "_requirements.md").write_text("1. [must] Something else\n", encoding="utf-8")
        r = run(self.payload())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Row 1: requirement text", r.stderr)

    def test_stub_allowed_for_unreadable_resume(self):
        self._manifest("no_text_layer")
        self.path.write_text(STUB, encoding="utf-8")
        r = run(self.payload())
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
