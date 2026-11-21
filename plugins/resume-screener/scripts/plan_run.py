"""Plan step 7 of /resume-screener:screen without putting resume-derived text in front of the main session.

Usage: plan_run.py <role_folder> [--fresh]

For every resume in the extract manifest it decides an action and carries out the file work:

- ``stub``   no text could be extracted, or the file name starts with ``_``: a manual-review
             stub is written here (so the main session never writes resume-derived text).
- ``review`` launch a reviewer: a task file ``.text/_tasks/<N>.txt`` holds the paths the
             reviewer needs; any existing assessment (or a copy in ``.failed/``) is moved
             to ``.previous/``.
- ``skip``   a finished assessment exists and still passes every check (not with --fresh).

Output (tab-separated): ``assessments_dir <A>``, ``counts review=R stub=S skip=K``, then one
``<action> <N> <display name> <scan flags>`` line per resume. Display names that look like
prompt injection are withheld; scan flags are kind counts only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assessment import check_assessment, lookup  # noqa: E402
from common import MANUAL_REVIEW_MARKER, text_dir  # noqa: E402
from scan_injection import INJECTION_PATTERNS, MARKUP_PATTERNS  # noqa: E402

PREVIOUS_DIR = ".previous"
FAILED_DIR = ".failed"
TASKS_DIR = "_tasks"
_UNSAFE = re.compile(r"[\x00-\x1f\x7f\[\]()<>`|*_#!\\]")

STUB_REASONS = {
    "no_text_layer": "This PDF is a scanned image with no text — please review manually.",
    "error": "The text of this file could not be extracted ({reason}) — please review manually.",
    "underscore": "The file name starts with an underscore — rename and re-run.",
}


def name_looks_injected(name: str) -> bool:
    """True if a file name (as is, or with _ - . as spaces) matches an injection pattern."""
    variants = (name, re.sub(r"[_\-.]+", " ", name))
    return any(p.search(v) for v in variants for p in INJECTION_PATTERNS + MARKUP_PATTERNS)


def display_name(index: int, name: str) -> str:
    if name_looks_injected(name):
        return f"resume #{index} (name withheld — flagged)"
    shown = re.sub(r"\s+", " ", _UNSAFE.sub(" ", name)).strip()[:80]
    return f"resume #{index} ({shown})"


def flags_text(entry) -> str:
    if not isinstance(entry, dict) or not entry.get("flagged"):
        return "none"
    kinds = entry.get("kinds") if isinstance(entry.get("kinds"), dict) else {}
    parts = [f"{k}={c}" for k, c in sorted(kinds.items()) if isinstance(k, str) and re.fullmatch(r"\w+", k)]
    return "flagged: yes; " + ", ".join(parts) if parts else "flagged: yes"


def decide_action(adir: Path, name: str, info: dict, fresh: bool) -> str:
    if info.get("status") != "ok" or name.startswith("_"):
        return "stub"
    if fresh:
        return "review"
    md_path = adir / f"{name}.md"
    if not md_path.is_file():
        return "review"
    md = md_path.read_text(encoding="utf-8", errors="replace")
    if MANUAL_REVIEW_MARKER in md:
        return "review"  # retry stubs
    return "skip" if not check_assessment(adir, name, md) else "review"


def _to_previous(src: Path, adir: Path, name: str) -> None:
    prev = adir / PREVIOUS_DIR
    prev.mkdir(exist_ok=True)
    os.replace(src, prev / f"{name}.md")


def stub_text(name: str, info: dict) -> str:
    status = "underscore" if name.startswith("_") else str(info.get("status"))
    reason = re.sub(r"\s+", " ", str(info.get("reason") or "unknown error")).strip()[:200]
    template = STUB_REASONS.get(status, STUB_REASONS["error"])
    return f"# Candidate: {name}\n{MANUAL_REVIEW_MARKER}\nReason: {template.format(reason=reason)}\n"


def plan(role: Path, fresh: bool) -> tuple[Path, list]:
    adir = (Path(role) / "assessments").resolve()
    tdir = text_dir(adir)
    manifest = json.loads((tdir / "_extract.json").read_text(encoding="utf-8"))
    try:
        summary = json.loads((tdir / "_flags_summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        summary = {}
    tasks = tdir / TASKS_DIR
    shutil.rmtree(tasks, ignore_errors=True)
    tasks.mkdir()
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}

    rows = []
    index = 0
    for name, info in files.items():
        if not isinstance(info, dict) or info.get("role") == "jd":
            continue
        index += 1
        action = decide_action(adir, name, info, fresh)
        md_path = adir / f"{name}.md"
        failed_path = adir / FAILED_DIR / f"{name}.md"
        flags = flags_text(lookup(summary, name))
        if action != "skip":
            if failed_path.is_file():
                _to_previous(failed_path, adir, name)
            if md_path.is_file():
                _to_previous(md_path, adir, name)
        if action == "stub":
            md_path.write_text(stub_text(name, info), encoding="utf-8")
        elif action == "review":
            (tasks / f"{index}.txt").write_text(
                f"Resume text: {tdir / (name + '.txt')}\n"
                f"Requirements: {adir / '_requirements.md'}\n"
                f"Scan flags: {flags}\n"
                f"Output path: {md_path}\n",
                encoding="utf-8",
            )
        rows.append({"index": index, "name": name, "action": action, "flags": flags})
    return adir, rows


def main(argv: list) -> int:
    args = [a for a in argv if a != "--fresh"]
    if len(args) != 1:
        sys.stderr.write("usage: plan_run.py <role_folder> [--fresh]\n")
        return 2
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    adir, rows = plan(Path(args[0]), "--fresh" in argv)
    counts = {a: sum(r["action"] == a for r in rows) for a in ("review", "stub", "skip")}
    print("assessments_dir", adir, sep="\t")
    print("counts", " ".join(f"{k}={v}" for k, v in counts.items()), sep="\t")
    for r in rows:
        print(r["action"], r["index"], display_name(r["index"], r["name"]), r["flags"], sep="\t")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
