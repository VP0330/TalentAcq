"""PreToolUse guard (I3): confine resume-reviewer subagents during a screening run.

Hook mode (no args): read the hook JSON from stdin; print a deny decision or nothing.
CLI mode: ``--set-lock <role_folder>`` / ``--clear-lock`` manage the run lock file.
With no lock file the hook allows everything.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import is_within, norm_path  # noqa: E402

LOCK_REL = Path(".claude") / "resume-screener.lock"
GUARDED_TOOLS = ("Read", "Write", "Edit")
RAW_MANIFESTS = (os.path.normcase("_scan.json"), os.path.normcase("_extract.json"))


def set_lock(cwd: Path, role_folder: Path) -> Path:
    role = Path(os.path.abspath(role_folder))
    lock_path = Path(cwd) / LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "role_folder": str(role.resolve()),
        "assessments_dir": str((role / "assessments").resolve()),
    }
    lock_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return lock_path


def clear_lock(cwd: Path) -> None:
    try:
        (Path(cwd) / LOCK_REL).unlink()
    except FileNotFoundError:
        pass


def load_lock(cwd: Path) -> dict | None:
    """Return the lock dict, or None if absent or unreadable/invalid."""
    try:
        data = json.loads((Path(cwd) / LOCK_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(data, dict)
        and isinstance(data.get("role_folder"), str)
        and isinstance(data.get("assessments_dir"), str)
    ):
        return data
    return None


def is_reviewer(hook_input: dict) -> bool:
    if "agent_type" in hook_input:
        return str(hook_input["agent_type"] or "").endswith("resume-reviewer")
    return True  # fallback: enforce for every call while a lock exists


def _deny(path: str, rule: str) -> tuple[str, str]:
    return ("deny", f"resume-reviewer may not access '{path}': {rule}")


def decide(hook_input: dict, lock: dict | None, plugin_root: Path) -> tuple[str, str]:
    if not lock or not is_reviewer(hook_input):
        return ("allow", "")
    tool = hook_input.get("tool_name")
    if tool not in GUARDED_TOOLS:
        return ("allow", "")

    tool_input = hook_input.get("tool_input")
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw:
        return _deny(str(raw), "no file_path supplied; cannot verify access during screening")

    p = Path(raw)
    if not p.is_absolute():
        p = Path(hook_input.get("cwd") or os.getcwd()) / p
    target = Path(norm_path(p))
    assessments = Path(lock["assessments_dir"])

    if tool == "Read":
        if is_within(target, assessments / ".text") and target != Path(norm_path(assessments / ".text")):
            if target.name.endswith(".hidden.txt"):
                return _deny(raw, "hidden-text files are off limits to reviewers")
            if os.path.normcase(target.name) in RAW_MANIFESTS:
                return _deny(
                    raw, "raw scan/extract manifests are off limits; use _flags_summary.json"
                )
            return ("allow", "")
        if target == Path(norm_path(assessments / "_requirements.md")):
            return ("allow", "")
        if is_within(target, Path(plugin_root) / "skills"):
            return ("allow", "")
        return _deny(
            raw,
            "reviewers may only read <assessments>/.text/ (not *.hidden.txt), "
            "<assessments>/_requirements.md, or plugin skill files",
        )

    # Write / Edit
    in_dir = Path(norm_path(target.parent)) == Path(norm_path(assessments))
    if in_dir and target.suffix == ".md" and not target.name.startswith("_"):
        return ("allow", "")
    return _deny(
        raw,
        "reviewers may only write <assessments>/<name>.md directly in the assessments "
        "folder (names starting with '_' are reserved)",
    )


def _emit_deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def _hook_main() -> int:
    plugin_root = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        hook_input = json.loads(raw)
        if not isinstance(hook_input, dict):
            raise ValueError("hook input is not an object")
    except Exception:
        # Unparseable input: fail closed only while a screening lock exists.
        if (Path(os.getcwd()) / LOCK_REL).exists():
            _emit_deny("resume-screener guard could not parse hook input during a screening run")
        return 0

    cwd = Path(hook_input.get("cwd") or os.getcwd())
    lock = load_lock(cwd)
    if lock is None:
        if (cwd / LOCK_REL).exists():  # lock present but corrupt: fail closed
            if hook_input.get("tool_name") in GUARDED_TOOLS and is_reviewer(hook_input):
                _emit_deny("resume-screener lock file is unreadable; refusing file access")
        return 0
    try:
        decision, reason = decide(hook_input, lock, plugin_root)
    except Exception as exc:  # never crash; fail closed for guarded tools
        if hook_input.get("tool_name") in GUARDED_TOOLS and is_reviewer(hook_input):
            _emit_deny(f"resume-screener guard error: {exc}")
        return 0
    if decision == "deny":
        _emit_deny(reason)
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return _hook_main()
    if argv[0] == "--set-lock" and len(argv) == 2:
        print(set_lock(Path(os.getcwd()), Path(argv[1])))
        return 0
    if argv == ["--clear-lock"]:
        clear_lock(Path(os.getcwd()))
        return 0
    print("usage: guard_reviewer.py [--set-lock <role_folder> | --clear-lock]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
