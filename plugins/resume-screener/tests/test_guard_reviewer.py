from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import guard_reviewer as g  # noqa: E402

SCRIPT = Path(g.__file__).resolve()


class GuardTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()
        self.cwd = self.tmp
        self.root = self.tmp / "plugin"
        (self.root / "skills").mkdir(parents=True)
        self.role = self.tmp / "role"
        self.ass = self.role / "assessments"
        (self.ass / ".text").mkdir(parents=True)
        self.lock = {"role_folder": str(self.role), "assessments_dir": str(self.ass)}
        # Path rules only here; content checks are covered by ContentGuardTests.
        patcher = mock.patch.object(g, "_content_errors", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, tool, path, **extra):
        inp = {"tool_name": tool, "tool_input": {"file_path": str(path)}, "cwd": str(self.cwd)}
        inp.update(extra)
        return g.decide(inp, self.lock, self.root)[0]

    def test_no_lock_allows_everything(self):
        hi = {"tool_name": "Read", "tool_input": {"file_path": "C:/anything"}, "cwd": str(self.cwd)}
        self.assertEqual(g.decide(hi, None, self.root), ("allow", ""))

    def test_reviewer_can_read_own_text_and_requirements(self):
        self.assertEqual(self.call("Read", self.ass / ".text" / "a.pdf.txt"), "allow")
        self.assertEqual(self.call("Read", self.ass / "_requirements.md"), "allow")
        self.assertEqual(self.call("Read", self.root / "skills" / "x" / "SKILL.md"), "allow")

    def test_reviewer_cannot_read_hidden_or_outside(self):
        self.assertEqual(self.call("Read", self.ass / ".text" / "a.pdf.hidden.txt"), "deny")
        self.assertEqual(self.call("Read", self.role / "a.pdf"), "deny")
        self.assertEqual(self.call("Read", "C:/Users/x/secrets.txt"), "deny")

    def test_reviewer_cannot_read_raw_scan_or_manifest(self):
        self.assertEqual(self.call("Read", self.ass / ".text" / "_scan.json"), "deny")
        self.assertEqual(self.call("Read", self.ass / ".text" / "_extract.json"), "deny")

    def test_reviewer_can_read_flags_summary(self):
        self.assertEqual(self.call("Read", self.ass / ".text" / "_flags_summary.json"), "allow")

    def test_reviewer_write_rules(self):
        self.assertEqual(self.call("Write", self.ass / "a.pdf.md"), "allow")
        self.assertEqual(self.call("Edit", self.ass / "a.pdf.md"), "allow")
        self.assertEqual(self.call("Write", self.ass / "_summary.md"), "deny")
        self.assertEqual(self.call("Write", self.ass / ".text" / "x.txt"), "deny")
        self.assertEqual(self.call("Write", self.role / "other.md"), "deny")
        self.assertEqual(self.call("Write", self.ass / "a.txt"), "deny")

    def test_deny_reason_names_path_and_rule(self):
        hi = {"tool_name": "Write", "tool_input": {"file_path": str(self.role / "other.md")}, "cwd": str(self.cwd)}
        decision, reason = g.decide(hi, self.lock, self.root)
        self.assertEqual(decision, "deny")
        self.assertIn("other.md", reason)

    def test_main_session_unaffected_when_agent_type_present(self):
        self.assertEqual(self.call("Read", "C:/Users/x/secrets.txt", agent_type="general-purpose"), "allow")
        self.assertEqual(
            self.call("Read", "C:/Users/x/secrets.txt", agent_type="resume-screener:resume-reviewer"), "deny")

    def test_other_tools_allowed(self):
        hi = {"tool_name": "Grep", "tool_input": {"pattern": "x"}, "cwd": str(self.cwd)}
        self.assertEqual(g.decide(hi, self.lock, self.root)[0], "allow")

    def test_fallback_when_agent_type_absent(self):
        self.assertTrue(g.is_reviewer({"tool_name": "Read"}))
        self.assertEqual(self.call("Read", self.role / "a.pdf"), "deny")

    def test_relative_path_resolved_against_cwd(self):
        hi = {"tool_name": "Write", "tool_input": {"file_path": "a.pdf.md"}, "cwd": str(self.ass)}
        self.assertEqual(g.decide(hi, self.lock, self.root)[0], "allow")
        hi["cwd"] = str(self.role)
        self.assertEqual(g.decide(hi, self.lock, self.root)[0], "deny")

    def test_dotdot_traversal_denied(self):
        self.assertEqual(self.call("Read", self.ass / ".text" / ".." / ".." / "a.pdf"), "deny")

    def test_missing_file_path_denied_under_lock(self):
        hi = {"tool_name": "Read", "tool_input": {}, "cwd": str(self.cwd)}
        self.assertEqual(g.decide(hi, self.lock, self.root)[0], "deny")

    def test_paths_with_spaces(self):
        role = self.tmp / "Role (Sales) Q4"
        ass = role / "assessments"
        (ass / ".text").mkdir(parents=True)
        (self.tmp / "Role (Sales) Q5").mkdir()
        lock = {"role_folder": str(role), "assessments_dir": str(ass)}

        def d(tool, p):
            return g.decide({"tool_name": tool, "tool_input": {"file_path": str(p)}, "cwd": str(self.cwd)},
                            lock, self.root)[0]

        self.assertEqual(d("Write", ass / "José Pérez (1).pdf.md"), "allow")
        self.assertEqual(d("Read", ass / ".text" / "José Pérez (1).pdf.txt"), "allow")
        self.assertEqual(d("Write", self.tmp / "Role (Sales) Q5" / "José Pérez (1).pdf.md"), "deny")

    def _run(self, stdin, **kw):
        return subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True,
                              text=True, encoding="utf-8", cwd=str(self.cwd), **kw)

    def test_hook_prints_deny_json(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(self.role / "a.pdf")},
                   "cwd": str(self.cwd), "hook_event_name": "PreToolUse"}
        g.set_lock(self.cwd, self.role)
        r = self._run(json.dumps(payload), env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(self.root)})
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertEqual(out["hookEventName"], "PreToolUse")

    def test_hook_allows_silently_without_lock(self):
        payload = {"tool_name": "Read", "tool_input": {"file_path": "C:/x"}, "cwd": str(self.cwd)}
        r = self._run(json.dumps(payload))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hook_malformed_input_without_lock_allows(self):
        r = self._run("not json")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hook_malformed_input_with_lock_denies(self):
        g.set_lock(self.cwd, self.role)
        r = self._run("not json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_lock_round_trip(self):
        p = g.set_lock(self.cwd, self.role)
        self.assertEqual(p, self.cwd / g.LOCK_REL)
        lock = g.load_lock(self.cwd)
        self.assertEqual(lock["role_folder"], str(self.role.resolve()))
        self.assertEqual(lock["assessments_dir"], str((self.role / "assessments").resolve()))
        g.clear_lock(self.cwd)
        self.assertIsNone(g.load_lock(self.cwd))
        g.clear_lock(self.cwd)  # idempotent

    def test_cli_set_and_clear_lock(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--set-lock", str(self.role)],
                           capture_output=True, text=True, encoding="utf-8", cwd=str(self.cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNotNone(g.load_lock(self.cwd))
        r = subprocess.run([sys.executable, str(SCRIPT), "--clear-lock"],
                           capture_output=True, text=True, encoding="utf-8", cwd=str(self.cwd))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(g.load_lock(self.cwd))


GOOD_MD = (
    "# Candidate: a.pdf\nFit: Strong match\nRequirements met: 1/1 must-haves shown\n\n"
    "## Requirements\n| # | Requirement (from JD) | Type | Status | Evidence from resume |\n"
    "|---|---|---|---|---|\n| 1 | R | must | Shown | \"Sold software daily\" |\n\n"
    "## Experience overview\nx\n\n## Strengths\n- a\n\n## Gaps\n- b\n\n"
    "## Suggested screen questions\n- q\n"
)
RESUME = "I Sold software daily for years"


class ContentGuardTests(unittest.TestCase):
    """F3a: reviewer Write/Edit content is checked before it reaches disk."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()
        self.root = self.tmp / "plugin"
        (self.root / "skills").mkdir(parents=True)
        self.role = self.tmp / "role"
        self.ass = self.role / "assessments"
        (self.ass / ".text").mkdir(parents=True)
        (self.ass / ".text" / "a.pdf.txt").write_text(RESUME, encoding="utf-8")
        (self.ass / ".text" / "_extract.json").write_text(
            json.dumps({"files": {"a.pdf": {"status": "ok"}}}), encoding="utf-8")
        self.lock = {"role_folder": str(self.role), "assessments_dir": str(self.ass)}

    def write(self, content):
        hi = {"tool_name": "Write", "agent_type": "resume-screener:resume-reviewer", "cwd": str(self.tmp),
              "tool_input": {"file_path": str(self.ass / "a.pdf.md"), "content": content}}
        return g.decide(hi, self.lock, self.root)

    def edit(self, old, new, replace_all=False):
        ti = {"file_path": str(self.ass / "a.pdf.md"), "old_string": old, "new_string": new}
        if replace_all:
            ti["replace_all"] = True
        hi = {"tool_name": "Edit", "agent_type": "resume-screener:resume-reviewer", "cwd": str(self.tmp),
              "tool_input": ti}
        return g.decide(hi, self.lock, self.root)

    def test_valid_write_allowed(self):
        self.assertEqual(self.write(GOOD_MD), ("allow", ""))

    def test_write_with_link_denied(self):
        d, reason = self.write(GOOD_MD.replace("- a", "- see [site](http://x.example)"))
        self.assertEqual(d, "deny")
        self.assertIn("links are not allowed", reason)

    def test_write_with_banned_word_denied(self):
        d, reason = self.write(GOOD_MD.replace("- b", "- reject this one"))
        self.assertEqual(d, "deny")
        self.assertIn("Banned wording", reason)

    def test_write_failing_full_validation_denied(self):
        d, reason = self.write(GOOD_MD.replace("Fit: Strong match", "Fit: Weak match"))
        self.assertEqual(d, "deny")
        self.assertIn("Fit", reason)

    def test_edit_introducing_html_denied(self):
        (self.ass / "a.pdf.md").write_text(GOOD_MD, encoding="utf-8")
        d, reason = self.edit("- a", "- <script>x</script>")
        self.assertEqual(d, "deny")
        self.assertIn("HTML", reason)

    def test_edit_with_verified_banned_quote_in_evidence_allowed(self):
        (self.ass / ".text" / "a.pdf.txt").write_text(RESUME + "\nReduced claim rejection rate", encoding="utf-8")
        (self.ass / "a.pdf.md").write_text(GOOD_MD, encoding="utf-8")
        d, _ = self.edit('"Sold software daily"', '"Reduced claim rejection rate"')
        self.assertEqual(d, "allow")

    def test_edit_fragment_scrubbed_when_file_missing(self):
        d, _ = self.edit("x", "![img](http://x)")
        self.assertEqual(d, "deny")


class NoLockReviewerTests(unittest.TestCase):
    """F6: a reviewer with no lock in place is denied file access."""

    def test_reviewer_denied_without_lock(self):
        for tool in ("Read", "Write", "Edit"):
            hi = {"tool_name": tool, "agent_type": "resume-screener:resume-reviewer",
                  "tool_input": {"file_path": "C:/x/a.md"}}
            d, reason = g.decide(hi, None, Path("."))
            self.assertEqual(d, "deny", tool)
            self.assertIn("lock", reason)

    def test_non_reviewer_and_untyped_allowed_without_lock(self):
        hi = {"tool_name": "Read", "tool_input": {"file_path": "C:/x"}}
        self.assertEqual(g.decide(hi, None, Path("."))[0], "allow")
        hi["agent_type"] = "general-purpose"
        self.assertEqual(g.decide(hi, None, Path("."))[0], "allow")

    def test_lock_found_under_project_dir_env(self):
        with tempfile.TemporaryDirectory() as proj, tempfile.TemporaryDirectory() as other:
            role = Path(proj) / "role"
            (role / "assessments" / ".text").mkdir(parents=True)
            g.set_lock(Path(proj), role)
            payload = {"tool_name": "Read", "agent_type": "resume-screener:resume-reviewer",
                       "tool_input": {"file_path": str(role / "a.pdf")}, "cwd": other}
            r = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                               capture_output=True, text=True, encoding="utf-8", cwd=other,
                               env={**os.environ, "CLAUDE_PROJECT_DIR": proj})
            out = json.loads(r.stdout)["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertIn("may not access", out["permissionDecisionReason"])

    def test_hook_denies_reviewer_without_lock(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"tool_name": "Read", "agent_type": "resume-screener:resume-reviewer",
                       "tool_input": {"file_path": "C:/x"}, "cwd": d}
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
            r = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                               capture_output=True, text=True, encoding="utf-8", cwd=d, env=env)
            self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
