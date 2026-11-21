"""Structural checks for the Claude Code components (hooks, agent, skill, commands)."""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from assessment import compute_fit, parse_assessment  # noqa: E402
from common import (  # noqa: E402
    AI_FLAG_COMMENT,
    FIT_LABELS,
    MANUAL_REVIEW_MARKER,
    REQUIRED_SECTIONS,
    STATUSES,
)

HOOKS = ROOT / "hooks" / "hooks.json"
AGENT = ROOT / "agents" / "resume-reviewer.md"
SKILL_DIR = ROOT / "skills" / "resume-screening"
SKILL = SKILL_DIR / "SKILL.md"
TEMPLATE = SKILL_DIR / "references" / "assessment-template.md"
FIT_DOC = SKILL_DIR / "references" / "fit-labels.md"
SETUP = ROOT / "commands" / "setup.md"
SCREEN = ROOT / "commands" / "screen.md"

TABLE_HEADER = "| # | Requirement (from JD) | Type | Status | Evidence from resume |"
SCRIPT_RE = re.compile(r"scripts/([A-Za-z0-9_]+\.py)")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def frontmatter(p: Path) -> tuple[dict, str]:
    """Tiny stdlib parser for a leading '---' block of 'key: value' lines."""
    text = read(p)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError(f"{p.name}: no frontmatter")
    meta: dict = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        if not line.strip() or line.startswith((" ", "\t")):
            continue
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"').strip("'")
    raise AssertionError(f"{p.name}: unterminated frontmatter")


def fenced_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:markdown|md)?\n(.*?)```", text, flags=re.S)


class HooksTests(unittest.TestCase):
    def test_hooks_json_parses_and_scripts_exist(self):
        data = json.loads(read(HOOKS))
        hooks = data["hooks"]
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "Read|Write|Edit")
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "Write|Edit")
        seen = set()
        for event in ("PreToolUse", "PostToolUse"):
            for entry in hooks[event]:
                for h in entry["hooks"]:
                    self.assertEqual(h["type"], "command")
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}", h["command"])
                    for script in SCRIPT_RE.findall(h["command"]):
                        self.assertTrue((ROOT / "scripts" / script).is_file(), script)
                        seen.add(script)
        self.assertEqual(seen, {"guard_reviewer.py", "validate_assessment.py"})


class AgentTests(unittest.TestCase):
    def test_frontmatter(self):
        meta, body = frontmatter(AGENT)
        self.assertEqual(meta["name"], "resume-reviewer")
        self.assertEqual(meta["tools"], "Read, Write")
        self.assertEqual(meta["model"], "inherit")
        self.assertEqual(meta["skills"], "resume-screener:resume-screening")
        self.assertTrue(meta.get("description"))
        self.assertNotIn("hooks", meta)
        self.assertNotIn("permissionMode", meta)

    def test_body_carries_guard_rules(self):
        _, body = frontmatter(AGENT)
        self.assertIn(AI_FLAG_COMMENT, body)
        self.assertIn("## Screening flags", body)
        self.assertIn("flagged: yes", body)
        self.assertIn("data", body.lower())


class SkillTests(unittest.TestCase):
    def test_frontmatter_and_references(self):
        meta, body = frontmatter(SKILL)
        self.assertEqual(meta["name"], "resume-screening")
        self.assertTrue(meta["description"].startswith("This skill"))
        self.assertIn("references/assessment-template.md", body)
        self.assertIn("references/fit-labels.md", body)
        self.assertTrue(TEMPLATE.is_file())
        self.assertTrue(FIT_DOC.is_file())

    def test_template_has_exact_sections_and_flag_comment(self):
        text = read(TEMPLATE)
        lines = {ln.rstrip() for ln in text.splitlines()}
        for heading in REQUIRED_SECTIONS:
            self.assertIn(heading, lines, f"heading must appear alone on a line: {heading}")
        self.assertIn("## Screening flags", lines)
        self.assertIn(AI_FLAG_COMMENT, text)
        self.assertIn(MANUAL_REVIEW_MARKER, lines)
        self.assertIn(TABLE_HEADER, lines)
        for status in STATUSES:
            self.assertIn(status, text)

    def test_template_examples_are_self_consistent(self):
        examples = [b for b in fenced_blocks(read(TEMPLATE)) if b.startswith("# Candidate:")]
        full = [b for b in examples if MANUAL_REVIEW_MARKER not in b]
        self.assertGreaterEqual(len(full), 2)  # normal + flagged variant
        for block in full:
            a = parse_assessment(block)
            self.assertIn(a.fit_label, FIT_LABELS)
            self.assertEqual(a.fit_label, compute_fit(a.rows))
            musts = [r for r in a.rows if r.type == "must"]
            shown = sum(1 for r in musts if r.status == STATUSES[0])
            self.assertEqual(a.met, (shown, len(musts)))
            for heading in REQUIRED_SECTIONS:
                self.assertIn(heading, a.sections)
        self.assertTrue(any(AI_FLAG_COMMENT in b for b in full))

    def test_fit_labels_doc(self):
        text = read(FIT_DOC)
        for label in FIT_LABELS:
            self.assertIn(label, text)


class CommandTests(unittest.TestCase):
    def _scripts(self, p: Path) -> set:
        return set(SCRIPT_RE.findall(read(p)))

    def test_commands_reference_only_existing_scripts(self):
        for cmd in (SETUP, SCREEN):
            scripts = self._scripts(cmd)
            self.assertTrue(scripts, cmd.name)
            for s in scripts:
                self.assertTrue((ROOT / "scripts" / s).is_file(), f"{cmd.name} -> {s}")

    def test_setup_frontmatter(self):
        meta, body = frontmatter(SETUP)
        self.assertTrue(meta.get("description"))
        self.assertEqual(meta["allowed-tools"], "Bash(python:*)")
        self.assertIn("check_prereqs.py", body)

    def test_screen_frontmatter_and_steps(self):
        meta, body = frontmatter(SCREEN)
        self.assertTrue(meta.get("description"))
        self.assertEqual(meta["argument-hint"], "<role-folder> [--fresh]")
        self.assertEqual(
            meta["allowed-tools"], "Bash(python:*), Read, Write, Glob, Agent, AskUserQuestion"
        )
        for s in ("check_prereqs.py", "extract_text.py", "scan_injection.py",
                  "guard_reviewer.py", "build_summary.py"):
            self.assertIn(s, self._scripts(SCREEN))
        self.assertIn("resume-reviewer", body)
        # Manual-review stubs are written before the lock; the lock is always cleared.
        self.assertLess(body.index(MANUAL_REVIEW_MARKER), body.index("--set-lock"))
        self.assertLess(body.index("--set-lock"), body.rindex("--clear-lock"))
        self.assertIn("/resume-screener:setup", body)


if __name__ == "__main__":
    unittest.main()
