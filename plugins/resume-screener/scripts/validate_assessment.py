"""PostToolUse hook: validate assessment files. Exit 2 + stderr = blocking feedback."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assessment import validate  # noqa: E402
from common import resume_name_from_assessment, text_dir, text_path_for  # noqa: E402


def main() -> int:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    path = Path(payload["tool_input"]["file_path"])
    if path.parent.name != "assessments" or not path.name.endswith(".md") or path.name.startswith("_"):
        return 0
    if not path.is_file():
        return 0
    md = path.read_text(encoding="utf-8", errors="replace")
    adir = path.parent
    name = resume_name_from_assessment(path)

    tp = text_path_for(adir, name)
    resume_text = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else None

    scan_entry = None
    scan_file = text_dir(adir) / "_scan.json"
    if scan_file.is_file():
        scan = json.loads(scan_file.read_text(encoding="utf-8", errors="replace"))
        scan_entry = scan.get(name)

    errors = validate(md, resume_text, scan_entry)
    if errors:
        sys.stderr.write(f"Assessment check failed for {path.name}:\n- " + "\n- ".join(errors) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # non-blocking error
        sys.stderr.write(f"validate_assessment error: {exc!r}\n")
        sys.exit(1)
