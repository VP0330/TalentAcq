from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from common import AI_FLAG_COMMENT, MANUAL_REVIEW_MARKER  # noqa: E402
from build_summary import build_summary  # noqa: E402

SHOWN = "Shown"
NOT = "Not shown in the resume"
RESUME = "Account Executive, 2018-2023\nManaged key accounts across EMEA."
EV = '"Account Executive, 2018-2023"'
TODAY = date(2026, 11, 21)


def make_md(fit, rows, flagged=False, strength="Strong account work"):
    musts = [r for r in rows if r[0] == "must"]
    shown = sum(1 for r in musts if r[1] == SHOWN)
    fit_line = f"Fit: {fit}" + (f" — {AI_FLAG_COMMENT}" if flagged else "")
    lines = [
        "# Candidate: x",
        fit_line,
        f"Requirements met: {shown}/{len(musts)} must-haves shown",
        "",
        "## Requirements",
        "| # | Requirement (from JD) | Type | Status | Evidence from resume |",
        "|---|---|---|---|---|",
    ]
    for i, (t, s, e) in enumerate(rows, 1):
        lines.append(f"| {i} | Req {i} | {t} | {s} | {e} |")
    lines += [
        "", "## Experience overview", "Eight years.", "",
        "## Strengths", f"- {strength}", "",
        "## Gaps", "- No SaaS experience", "",
        "## Suggested screen questions", "- Tell me about EMEA.", "",
    ]
    if flagged:
        lines += ["## Screening flags", "- Hidden text found", ""]
    return "\n".join(lines)


def rows_for(n_shown, n_total):
    return [("must", SHOWN if i < n_shown else NOT, EV if i < n_shown else "—") for i in range(n_total)]


class BuildSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / ".text").mkdir()
        self.files = {}

    def add(self, name, md, flagged=False, text=RESUME, status="ok"):
        (self.dir / f"{name}.md").write_text(md, encoding="utf-8")
        (self.dir / ".text" / f"{name}.txt").write_text(text, encoding="utf-8")
        self.files[name] = {"status": status, "role": "resume"}
        if flagged:
            scan = self.dir / ".text" / "_scan.json"
            data = json.loads(scan.read_text()) if scan.exists() else {}
            data[name] = {"flagged": True, "flags": [{"kind": "x", "text": "y"}]}
            scan.write_text(json.dumps(data), encoding="utf-8")

    def finish(self):
        manifest = {"jd": "jd.pdf", "files": dict(self.files)}
        manifest["files"]["jd.pdf"] = {"status": "ok", "role": "jd"}
        (self.dir / ".text" / "_extract.json").write_text(json.dumps(manifest), encoding="utf-8")
        return build_summary(self.dir, TODAY)

    def test_sorted_by_label_then_shown_then_name(self):
        self.add("weak.pdf", make_md("Weak match", rows_for(1, 3)))
        self.add("strong.pdf", make_md("Strong match", rows_for(3, 3)))
        self.add("possible.pdf", make_md("Possible match", rows_for(2, 3)))
        md, counts = self.finish()
        self.assertEqual(counts["assessed"], 3)
        order = [l for l in md.splitlines() if l.startswith("| ") and ".pdf" in l]
        self.assertEqual([l.split("|")[1].strip() for l in order], ["strong.pdf", "possible.pdf", "weak.pdf"])
        self.assertIn("| Candidate (file) | Fit | Must-haves shown | ⚠ | Top strength | Top gap |", md)
        self.assertIn("# Screening summary — jd.pdf — 2026-11-21", md)

    def test_ai_augmented_column_and_count(self):
        self.add("a.pdf", make_md("Strong match", rows_for(2, 2), flagged=True), flagged=True)
        self.add("b.pdf", make_md("Strong match", rows_for(2, 2)))
        md, counts = self.finish()
        self.assertEqual(counts["ai_augmented"], 1)
        row_a = next(l for l in md.splitlines() if l.startswith("| a.pdf"))
        row_b = next(l for l in md.splitlines() if l.startswith("| b.pdf"))
        self.assertIn("AI-augmented", row_a)
        self.assertNotIn("AI-augmented", row_b)

    def test_missing_and_unreadable_listed(self):
        self.files["scan.pdf"] = {"status": "no_text_layer", "reason": "image only", "role": "resume"}
        self.files["lost.docx"] = {"status": "ok", "role": "resume"}
        md, counts = self.finish()
        section = md.split("## Needs manual review")[1]
        self.assertIn("scan.pdf", section)
        self.assertIn("image only", section)
        self.assertIn("lost.docx — no assessment produced", section)
        self.assertEqual(sorted(counts["manual_review"]), ["lost.docx", "scan.pdf"])

    def test_manual_review_stub_listed(self):
        self.add("stub.pdf", f"# Candidate: stub.pdf\n{MANUAL_REVIEW_MARKER}\n")
        md, counts = self.finish()
        self.assertEqual(counts["manual_review"], ["stub.pdf"])
        self.assertEqual(counts["assessed"], 0)
        self.assertIn("stub.pdf", md.split("## Needs manual review")[1])

    def test_failed_check_excluded(self):
        bad = make_md("Strong match", [("must", SHOWN, '"Led a team of 40"')])
        self.add("bad.pdf", bad)
        self.add("ok.pdf", make_md("Strong match", rows_for(1, 1)))
        md, counts = self.finish()
        self.assertEqual(counts["failed_checks"], ["bad.pdf"])
        table, rest = md.split("## Needs manual review")
        self.assertNotIn("bad.pdf", table)
        self.assertIn("ok.pdf", table)
        self.assertIn("bad.pdf", rest.split("## Failed automatic checks")[1])

    def test_no_links_in_summary(self):
        self.add("link.pdf", make_md("Strong match", rows_for(1, 1), strength="Built [site](http://x) big"))
        md, _ = self.finish()
        self.assertNotIn("](", md)

    def test_strength_scrubbed_and_truncated(self):
        self.add("a.pdf", make_md("Strong match", rows_for(1, 1),
                                  strength="Built [site] (http x) a > b | pipe " + "z" * 200))
        md, _ = self.finish()
        row = next(l for l in md.splitlines() if l.startswith("| a.pdf"))
        cells = row.split("|")
        self.assertEqual(len(cells), 8)  # 6 cells, no stray pipes
        strength = cells[5].strip()
        self.assertLessEqual(len(strength), 80)
        for ch in "[]()<>":
            self.assertNotIn(ch, strength)


if __name__ == "__main__":
    unittest.main()
