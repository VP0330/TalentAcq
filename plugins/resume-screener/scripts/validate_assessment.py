"""PostToolUse hook: validate assessment files. Exit 2 + stderr = blocking feedback."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from assessment import check_assessment  # noqa: E402
from common import resume_name_from_assessment, text_dir  # noqa: E402


def in_scope(raw_path: str) -> Path | None:
    """Resolved assessment path if this hook should check it, else None.

    Only ``<role>/assessments/<name>.md`` (not ``_``-prefixed) where
    ``<role>/assessments/.text/_extract.json`` exists, so unrelated
    ``assessments/`` folders are left alone. Compared case-insensitively on Windows.
    """
    path = Path(os.path.realpath(raw_path))
    name = path.name
    if os.path.normcase(path.parent.name) != os.path.normcase("assessments"):
        return None
    if not os.path.normcase(name).endswith(".md") or name.startswith("_"):
        return None
    if not (text_dir(path.parent) / "_extract.json").is_file():
        return None
    return path


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
    path = in_scope(payload["tool_input"]["file_path"])
    if path is None or not path.is_file():
        return 0
    md = path.read_text(encoding="utf-8", errors="replace")
    errors = check_assessment(path.parent, resume_name_from_assessment(path), md)
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
