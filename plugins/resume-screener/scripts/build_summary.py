"""Build the ranked _summary.md deterministically from reviewer assessments (output guard O4).

The main Claude session reads only this file, never the resume-derived assessments.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assessment import parse_assessment, validate  # noqa: E402
from common import (  # noqa: E402
    FIT_LABELS,
    STATUSES,
    resume_name_from_assessment,
    text_dir,
    text_path_for,
)

HEADER = "| Candidate (file) | Fit | Must-haves shown | ⚠ | Top strength | Top gap |"
_SCRUB_CHARS = re.compile(r"[\[\]()<>]")


def _clean(s: str, limit: int) -> str:
    """Make resume-derived text safe for a markdown table cell."""
    s = _SCRUB_CHARS.sub("", s)
    s = s.replace("|", "/").replace("`", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _load_json(path: Path):
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def build_summary(assessments_dir: Path, today: date) -> tuple[str, dict]:
    assessments_dir = Path(assessments_dir)
    manifest = _load_json(text_dir(assessments_dir) / "_extract.json")
    scan = _load_json(text_dir(assessments_dir) / "_scan.json")
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    jd_name = _clean(str(manifest.get("jd") or "unknown JD"), 120)

    table = []  # (sort key, row)
    manual = {}  # name -> reason
    failed = {}  # name -> first error
    ai_count = 0
    seen = set()

    for md_path in sorted(assessments_dir.glob("*.md"), key=lambda p: p.name.lower()):
        if md_path.name.startswith("_"):
            continue
        name = resume_name_from_assessment(md_path)
        seen.add(name)
        md = md_path.read_text(encoding="utf-8", errors="replace")
        a = parse_assessment(md)
        if a.manual_review:
            manual[name] = "reviewer marked this resume for manual review"
            continue
        tp = text_path_for(assessments_dir, name)
        resume_text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else None
        scan_entry = scan.get(name) if isinstance(scan.get(name), dict) else None
        errors = validate(md, resume_text, scan_entry)
        if errors:
            failed[name] = _clean(errors[0], 200)
            continue
        shown = sum(1 for r in a.rows if r.type == "must" and r.status == STATUSES[0])
        total = sum(1 for r in a.rows if r.type == "must")
        if a.ai_augmented:
            ai_count += 1
        strength = _clean(a.strengths[0], 80) if a.strengths else ""
        gap = _clean(a.gaps[0], 80) if a.gaps else ""
        row = "| {} | {} | {}/{} | {} | {} | {} |".format(
            _clean(name, 120), a.fit_label, shown, total,
            "AI-augmented" if a.ai_augmented else "", strength, gap,
        )
        table.append(((FIT_LABELS.index(a.fit_label), -shown, name.lower()), row))

    # Manifest entries: unreadable files and resumes with no assessment.
    for name, info in files.items():
        if not isinstance(info, dict) or info.get("role") == "jd":
            continue
        status = info.get("status")
        reason = _clean(str(info.get("reason") or ""), 160)
        if status in ("error", "no_text_layer"):
            manual[name] = f"{status}: {reason}" if reason else str(status)
        elif name not in seen:
            manual[name] = "no assessment produced"

    table.sort(key=lambda t: t[0])
    lines = [
        f"# Screening summary — {jd_name} — {today.isoformat()}",
        "",
        "This table is a reading aid for the recruiter, not a decision.",
        "",
        f"Assessed: {len(table)}   AI-augmented flags: {ai_count}   Needs manual review: {len(manual)}",
        "",
        HEADER,
        "|---|---|---|---|---|---|",
    ]
    lines += [row for _, row in table]
    lines += ["", "## Needs manual review", ""]
    if manual:
        lines += [f"- {_clean(n, 120)} — {r}" for n, r in sorted(manual.items(), key=lambda kv: kv[0].lower())]
    else:
        lines.append("None.")
    if failed:
        lines += ["", "## Failed automatic checks", ""]
        lines += [f"- {_clean(n, 120)} — {e}" for n, e in sorted(failed.items(), key=lambda kv: kv[0].lower())]
    lines.append("")

    counts = {
        "assessed": len(table),
        "ai_augmented": ai_count,
        "manual_review": sorted(manual, key=str.lower),
        "failed_checks": sorted(failed, key=str.lower),
    }
    return "\n".join(lines), counts


def main(argv: list) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: build_summary.py <role_folder>\n")
        return 2
    role = Path(argv[1])
    adir = role / "assessments" if (role / "assessments").is_dir() else role
    md, counts = build_summary(adir, date.today())
    (adir / "_summary.md").write_text(md, encoding="utf-8")
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
