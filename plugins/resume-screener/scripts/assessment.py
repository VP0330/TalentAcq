"""Parse and validate reviewer assessments (output guards O1, O2, O3)."""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

from common import (  # noqa: E402
    AI_FLAG_COMMENT,
    BANNED_WORDS,
    FIT_LABELS,
    MANUAL_REVIEW_MARKER,
    REQUIRED_SECTIONS,
    STATUSES,
    text_dir,
    text_path_for,
)

SCREENING_FLAGS_HEADING = "## Screening flags"

_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_IMAGE_RE = re.compile(r"!\[")
_IMAGE_FULL_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)[^>]*>")
_HTML_TAGS = frozenset(
    "a img script iframe style svg object embed link meta div span p br b i u "
    "table form input video audio source".split()
)
_MET_RE = re.compile(r"^Requirements met:\s*(\d+)\s*/\s*(\d+)")
_PIPE_RE = re.compile(r"(?<!\\)\|")
_SEP_CELL_RE = re.compile(r"^:?-+:?$")
_QUOTE_RE = re.compile(r'"([^"]+)"|“([^”]+)”')


@dataclass
class Row:
    num: int
    requirement: str
    type: str
    status: str
    evidence: str


@dataclass
class Assessment:
    file: str = ""
    fit_label: str = ""
    ai_augmented: bool = False
    met: Optional[tuple] = None
    rows: list = field(default_factory=list)
    sections: set = field(default_factory=set)
    manual_review: bool = False
    strengths: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    bad_rows: list = field(default_factory=list)


def _plain(s: str) -> str:
    """Strip markdown emphasis decoration (* and _) and surrounding space."""
    return s.replace("*", "").replace("_", "").strip()


def _split_cells(line: str) -> list:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.replace("\\|", "|").strip() for c in _PIPE_RE.split(inner)]


def _fit_line(md: str) -> str:
    for line in md.splitlines():
        if _plain(line).startswith("Fit:"):
            return line
    return ""


def parse_assessment(md: str) -> Assessment:
    a = Assessment()
    a.manual_review = MANUAL_REVIEW_MARKER in md
    section = None
    fit_seen = False
    for line in md.splitlines():
        stripped = line.strip()
        plain_line = _plain(line)
        if line.startswith("# Candidate:"):
            a.file = line[len("# Candidate:"):].strip()
        elif plain_line.startswith("Fit:") and not fit_seen:
            fit_seen = True
            rest = plain_line[len("Fit:"):].strip()
            a.ai_augmented = AI_FLAG_COMMENT in line or "AI-augmented" in line
            a.fit_label = re.split(r"\s+[—–]\s+|\s+-\s+", rest, maxsplit=1)[0].strip()
        elif a.met is None and _MET_RE.match(plain_line):
            m = _MET_RE.match(plain_line)
            a.met = (int(m.group(1)), int(m.group(2)))
        elif line.startswith("## "):
            section = stripped
            a.sections.add(section)
        elif section == "## Requirements" and stripped.startswith("|"):
            cells = _split_cells(stripped)
            if cells and _plain(cells[0]) == "#":
                continue  # header
            if all(_SEP_CELL_RE.match(c.replace(" ", "")) for c in cells if c) and any(cells):
                continue  # separator
            num_txt = cells[0].replace("*", "").replace(".", "").strip() if cells else ""
            if len(cells) < 5 or not num_txt.isdigit():
                a.bad_rows.append(stripped)
                continue
            a.rows.append(
                Row(
                    int(num_txt),
                    cells[1],
                    _plain(cells[2]).lower(),
                    _plain(cells[3]),
                    " | ".join(cells[4:]).strip(),
                )
            )
        elif section == "## Strengths" and stripped.startswith("- "):
            a.strengths.append(stripped[2:].strip())
        elif section == "## Gaps" and stripped.startswith("- "):
            a.gaps.append(stripped[2:].strip())
    return a


def compute_fit(rows: list) -> str:
    musts = [r for r in rows if r.type == "must"]
    not_shown = sum(1 for r in musts if r.status == STATUSES[2])
    if all(r.status == STATUSES[0] for r in musts):
        return FIT_LABELS[0]
    if not_shown >= 2:
        return FIT_LABELS[2]
    return FIT_LABELS[1]


def extract_quotes(evidence: str) -> list:
    return [m.group(1) or m.group(2) for m in _QUOTE_RE.finditer(evidence)]


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("‘", "'").replace("’", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace("­", "")
    s = re.sub(r"-\s*\n\s*", "", s)
    s = re.sub(r"\s*\|\s*", " | ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


MIN_QUOTE_PART = 12  # a quote needs at least one fragment this long (normalised)


def quote_found(quote: str, resume_text: str) -> bool:
    """True if every fragment of ``quote`` (split on … or ...) is in the resume text
    and at least one fragment has MIN_QUOTE_PART or more normalised characters."""
    haystack = normalise(resume_text)
    parts = [normalise(part) for part in re.split(r"…|\.\.\.", quote)]
    if not any(len(p) >= MIN_QUOTE_PART for p in parts):
        return False
    for p in parts:
        if len(p) >= 3 and p not in haystack:
            return False
    return True


_REQ_LINE_RE = re.compile(r"^\s*(\d+)\.\s*\[(must|nice)\]\s*(.+?)\s*$", re.IGNORECASE)


def parse_requirements(md: str) -> list:
    """Parse ``_requirements.md`` lines ``N. [must|nice] text`` into (num, type, text)."""
    out = []
    for line in md.splitlines():
        m = _REQ_LINE_RE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2).lower(), m.group(3)))
    return out


def _req_norm(s: str) -> str:
    return normalise(s.replace("|", "/").replace("*", "").replace("_", ""))


def _check_requirements(rows: list, requirements: list) -> list:
    errors = []
    if len(rows) != len(requirements):
        errors.append(
            f"The requirements table has {len(rows)} rows but _requirements.md has "
            f"{len(requirements)} requirements. Use one row per requirement, in order."
        )
    for i, (row, (num, rtype, text)) in enumerate(zip(rows, requirements), 1):
        if row.num != num:
            errors.append(
                f"Row {row.num}: table row {i} is numbered {row.num} but requirement {i} in "
                f"_requirements.md is number {num}."
            )
        if row.type != rtype:
            errors.append(f"Row {row.num}: type '{row.type}' does not match _requirements.md ('{rtype}').")
        if _req_norm(row.requirement) != _req_norm(text):
            errors.append(
                f"Row {row.num}: requirement text does not match _requirements.md item {num} "
                f"('{text}'). Copy it exactly."
            )
    return errors


def _section_texts(md: str, headings: tuple) -> dict:
    """Body text of each ``## `` section named in ``headings``."""
    out: dict = {h: [] for h in headings}
    section = None
    for line in md.splitlines():
        if line.startswith("## "):
            section = line.strip()
        elif section in out:
            out[section].append(line)
    return {h: "\n".join(lines) for h, lines in out.items()}


_QUOTED_PROSE_SECTIONS = ("## Experience overview", "## Strengths")


def _banned_scan_text(md: str) -> str:
    """md with quoted strings removed only from the Evidence column of Shown /
    Partly shown rows (verbatim resume words that the validator verifies)."""
    out = []
    section = None
    for line in md.splitlines():
        if line.startswith("## "):
            section = line.strip()
        elif section == "## Requirements" and line.strip().startswith("|"):
            parts = _PIPE_RE.split(line)
            if len(parts) > 5 and _plain(parts[4].replace("\\|", "|")) in STATUSES[:2]:
                tail = "|".join(parts[5:])
                tail = _QUOTE_RE.sub("", tail)
                line = "|".join(parts[:5]) + "|" + tail
        out.append(line)
    return "\n".join(out)


def _scrub(md: str) -> list:
    errors = []
    rest = md
    for m in _IMAGE_FULL_RE.finditer(md):
        errors.append(f"Images are not allowed: {m.group(0)}")
    rest = _IMAGE_FULL_RE.sub("", rest)
    # A bare "![" with no full image syntax still counts as an image.
    for m in _IMAGE_RE.finditer(rest):
        errors.append("Images are not allowed: ![")
    rest = _IMAGE_RE.sub("", rest)
    for m in _LINK_RE.finditer(rest):
        errors.append(f"Markdown links are not allowed (put URLs in backticks): {m.group(0)}")
    for m in _HTML_RE.finditer(md):
        if m.group(1).lower() in _HTML_TAGS:
            errors.append(f"HTML tags are not allowed: {m.group(0)}")
    lower = _banned_scan_text(md).lower()
    for w in BANNED_WORDS:
        if w in lower:
            errors.append(f"Banned wording found: '{w}'. Describe evidence, do not make hiring decisions.")
    return errors


scrub = _scrub  # public name: links, images, HTML and banned wording checks only


def validate(md: str, resume_text, scan_entry, requirements: Optional[list] = None) -> list:
    """All output-guard errors for one assessment (empty list = passes).

    ``requirements``: parsed ``_requirements.md`` as (num, type, text); None skips that check.
    """
    errors = _scrub(md)
    a = parse_assessment(md)
    if a.manual_review:
        return errors

    for heading in REQUIRED_SECTIONS:
        if heading not in a.sections:
            errors.append(f"Missing required section: {heading}")
    if a.fit_label not in FIT_LABELS:
        errors.append(f"Fit label '{a.fit_label}' is not one of: {', '.join(FIT_LABELS)}.")
    for raw in a.bad_rows:
        errors.append(f"Malformed requirements table row (need 5 cells and a numeric #): {raw}")
    if not a.rows:
        errors.append("The requirements table has no valid rows.")
    for r in a.rows:
        if r.status not in STATUSES:
            errors.append(f"Row {r.num}: status '{r.status}' must be one of: {', '.join(STATUSES)}.")
        if r.type not in ("must", "nice"):
            errors.append(f"Row {r.num}: type '{r.type}' must be 'must' or 'nice'.")
    if requirements is not None:
        errors.extend(_check_requirements(a.rows, requirements))

    # O1: fit label recomputed from the table
    expected = compute_fit(a.rows)
    if a.fit_label != expected:
        errors.append(
            f"Fit label is '{a.fit_label}' but the requirements table gives '{expected}'. "
            "Fix the label or the table."
        )
    musts = [r for r in a.rows if r.type == "must"]
    shown = sum(1 for r in musts if r.status == STATUSES[0])
    if a.met != (shown, len(musts)):
        errors.append(
            f"'Requirements met' is {a.met[0]}/{a.met[1]}" if a.met else "'Requirements met' line is missing"
        )
        errors[-1] += f" but the table gives {shown}/{len(musts)}."

    # O2: quotes verified against the visible resume text
    if resume_text is None:
        errors.append("Resume text not found for verification.")
    else:
        for r in a.rows:
            if r.status not in (STATUSES[0], STATUSES[1]):
                continue
            quotes = extract_quotes(r.evidence)
            if not quotes:
                errors.append(f"Row {r.num}: status '{r.status}' needs at least one quoted piece of evidence.")
            for q in quotes:
                if not quote_found(q, resume_text):
                    errors.append(
                        f'Quote not found in resume text: "{q}". Use an exact quote of at least '
                        f"{MIN_QUOTE_PART} characters or change the status."
                    )
        for heading, body in _section_texts(md, _QUOTED_PROSE_SECTIONS).items():
            for q in extract_quotes(body):
                if not quote_found(q, resume_text):
                    errors.append(
                        f'{heading[3:]}: quote not found in resume text: "{q}". Text in quotation '
                        f"marks must be an exact resume quote of at least {MIN_QUOTE_PART} "
                        "characters; fix it or remove the quotation marks."
                    )

    if scan_entry and scan_entry.get("flagged"):
        if AI_FLAG_COMMENT not in _fit_line(md):
            errors.append("This resume was flagged for prompt injection: the Fit line must include the AI-augmented comment.")
        if SCREENING_FLAGS_HEADING not in a.sections:
            errors.append(f"This resume was flagged for prompt injection: add a '{SCREENING_FLAGS_HEADING}' section.")
    return errors


# ------------------------------------------------------------ per-file checks

SCAN_UNREADABLE = "Scan record unreadable — flag check could not run."
STUB_NOT_ALLOWED = (
    "This resume was extracted successfully, so a full assessment is required "
    "— a manual-review stub is only for files that could not be read."
)


def lookup(d, name: str):
    """``d[name]``, falling back to a case-insensitive key match (Windows paths)."""
    if not isinstance(d, dict):
        return None
    if name in d:
        return d[name]
    key = os.path.normcase(name)
    for k, v in d.items():
        if isinstance(k, str) and os.path.normcase(k) == key:
            return v
    return None


def load_requirements(adir: Path) -> Optional[list]:
    """Parsed ``<adir>/_requirements.md``, or None if absent or it has no numbered lines."""
    p = Path(adir) / "_requirements.md"
    try:
        reqs = parse_requirements(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return reqs or None


def load_scan_entry(adir: Path, name: str) -> tuple:
    """(entry or None, error or None). A missing scan file is not an error here."""
    scan_file = text_dir(Path(adir)) / "_scan.json"
    if not scan_file.is_file():
        return None, None
    try:
        scan = json.loads(scan_file.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None, SCAN_UNREADABLE
    if not isinstance(scan, dict):
        return None, SCAN_UNREADABLE
    entry = lookup(scan, name)
    if entry is not None and not isinstance(entry, dict):
        return None, SCAN_UNREADABLE
    return entry, None


def extracted_ok(adir: Path, name: str) -> bool:
    """True only if the extract manifest is readable and lists ``name`` with status ok."""
    try:
        manifest = json.loads(
            (text_dir(Path(adir)) / "_extract.json").read_text(encoding="utf-8", errors="replace")
        )
        return lookup(manifest["files"], name)["status"] == "ok"
    except (OSError, ValueError, KeyError, TypeError):
        return False


def check_assessment(adir: Path, name: str, md: str) -> list:
    """Every check for the assessment of resume ``name`` in ``adir`` (hook, guard, plan)."""
    adir = Path(adir)
    tp = text_path_for(adir, name)
    resume_text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else None
    scan_entry, scan_err = load_scan_entry(adir, name)
    if MANUAL_REVIEW_MARKER in md and extracted_ok(adir, name):
        errors = [STUB_NOT_ALLOWED]
    else:
        errors = validate(md, resume_text, scan_entry, load_requirements(adir))
    if scan_err:
        errors.append(scan_err)
    return errors
