"""PostToolUse hook: validate assessment files. Exit 2 + stderr = blocking feedback."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assessment import validate  # noqa: E402
from common import MANUAL_REVIEW_MARKER, resume_name_from_assessment, text_dir, text_path_for  # noqa: E402


def _extracted_ok(adir: Path, name: str) -> bool:
    """True only if the extract manifest is readable and lists ``name`` with status ok."""
    try:
        manifest = json.loads(
            (text_dir(adir) / "_extract.json").read_text(encoding="utf-8", errors="replace")
        )
        return manifest["files"][name]["status"] == "ok"
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _emit(msg: str) -> None:
    """Write feedback as UTF-8 bytes; the Windows console codepage would garble it."""
    buf = getattr(sys.stderr, "buffer", None)
    if buf is None:
        sys.stderr.write(msg)
        return
    sys.stderr.flush()
    buf.write(msg.encode("utf-8", errors="replace"))
    buf.flush()


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

    if MANUAL_REVIEW_MARKER in md and _extracted_ok(adir, name):
        _emit(
            "This resume was extracted successfully, so a full assessment is required "
            "— a manual-review stub is only for files that could not be read.\n"
        )
        return 2

    errors = validate(md, resume_text, scan_entry)
    if errors:
        _emit(f"Assessment check failed for {path.name}:\n- " + "\n- ".join(errors) + "\n")
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # non-blocking error
        sys.stderr.write(f"validate_assessment error: {exc!r}\n")
        sys.exit(1)
