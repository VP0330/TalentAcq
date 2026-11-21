"""Print the JD's prompt-injection flags as ``<kind>: <text>`` lines (step 4 of /resume-screener:screen).

Usage: jd_flags.py <role_folder>
Reads only the JD's entry of ``.text/_scan.json``; never prints resume-derived text.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import text_dir  # noqa: E402


def jd_flags(role: Path) -> list:
    tdir = text_dir(Path(role) / "assessments")
    manifest = json.loads((tdir / "_extract.json").read_text(encoding="utf-8"))
    scan = json.loads((tdir / "_scan.json").read_text(encoding="utf-8"))
    entry = scan.get(manifest.get("jd") or "", {})
    flags = entry.get("flags", []) if isinstance(entry, dict) else []
    return [f"{f.get('kind')}: {f.get('text')}" for f in flags if isinstance(f, dict)]


def main(argv: list) -> int:
    if len(argv) != 1:
        sys.stderr.write("usage: jd_flags.py <role_folder>\n")
        return 2
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in jd_flags(Path(argv[0])):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
