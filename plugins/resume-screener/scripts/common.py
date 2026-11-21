"""Shared constants and path helpers for the resume-screener plugin."""
from __future__ import annotations

import os
from pathlib import Path

FIT_LABELS: tuple[str, str, str] = ("Strong match", "Possible match", "Weak match")

STATUSES = ("Shown", "Partly shown", "Not shown in the resume")

AI_FLAG_COMMENT = (
    '⚠ AI-augmented: this resume contains text aimed at automated screeners '
    '(see "Screening flags"). The label was computed from the evidence table '
    'and was not influenced by that text.'
)

BANNED_WORDS = ("reject", "not suitable", "do not proceed", "disqualif")

RESUME_EXTS = (".pdf", ".docx", ".doc")

REQUIRED_SECTIONS = (
    "## Requirements",
    "## Experience overview",
    "## Strengths",
    "## Gaps",
    "## Suggested screen questions",
)

MANUAL_REVIEW_MARKER = "Status: Needs manual review"


def norm_path(p: str | Path) -> str:
    """Return a case- and symlink-normalized absolute path string for comparison."""
    return os.path.normcase(os.path.realpath(p))


def is_within(child: str | Path, parent: str | Path) -> bool:
    """True if ``child`` is ``parent`` or lies beneath it (component-wise, not string prefix)."""
    child_n = norm_path(child)
    parent_n = norm_path(parent)
    try:
        return os.path.commonpath([child_n, parent_n]) == parent_n
    except ValueError:  # different drives on Windows
        return False


def text_dir(assessments_dir: Path) -> Path:
    return assessments_dir / ".text"


def text_path_for(assessments_dir: Path, resume_name: str) -> Path:
    return text_dir(assessments_dir) / f"{resume_name}.txt"


def assessment_path_for(assessments_dir: Path, resume_name: str) -> Path:
    return assessments_dir / f"{resume_name}.md"


def resume_name_from_assessment(md_path: Path) -> str:
    name = md_path.name
    if name.endswith(".md"):
        name = name[: -len(".md")]
    return name
