"""Prerequisite checker for the resume-screener plugin.

Usage: check_prereqs.py [--folder PATH] [--json]
Exit code 0 if every required check passes, else 1.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import RESUME_EXTS  # noqa: E402,F401

REQUIREMENTS = Path(__file__).resolve().parent / "requirements.txt"


def _word_registered() -> bool:
    """True if the Word.Application COM ProgID is registered (no process started)."""
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            return True
    except OSError:
        return False


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str
    required: bool


def check_python() -> Check:
    ok = sys.version_info >= (3, 9)
    v = ".".join(str(p) for p in sys.version_info[:3])
    return Check(
        "Python 3.9+",
        ok,
        f"Python {v}",
        "" if ok else "Install Python 3.9 or newer from https://www.python.org/downloads/",
        True,
    )


def check_pymupdf() -> Check:
    ok = importlib.util.find_spec("fitz") is not None
    return Check(
        "PyMuPDF",
        ok,
        "installed" if ok else "not installed",
        "" if ok else f'python -m pip install --user -r "{REQUIREMENTS}"',
        True,
    )


def find_doc_converter() -> str | None:
    """Return "word", "libreoffice", or None."""
    if _word_registered():
        return "word"
    if shutil.which("soffice"):
        return "libreoffice"
    return None


def check_doc_converter(needed: bool) -> Check:
    converter = find_doc_converter()
    ok = converter is not None
    if ok:
        detail = f"found ({converter})"
    elif needed:
        detail = "no .doc converter found, but .doc files are present"
    else:
        detail = "no .doc converter found (not needed: no .doc files)"
    return Check(
        ".doc converter (Word or LibreOffice)",
        ok,
        detail,
        "" if ok else "Save the .doc files as .docx, or install LibreOffice (https://www.libreoffice.org/).",
        needed,
    )


def _has_doc_files(folder: Path | None) -> bool:
    if folder is None or not folder.is_dir():
        return False
    return any(p.is_file() and p.suffix.lower() == ".doc" for p in folder.iterdir())


def check_all(folder: Path | None) -> list[Check]:
    return [
        check_python(),
        check_pymupdf(),
        check_doc_converter(_has_doc_files(folder)),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check resume-screener prerequisites.")
    parser.add_argument("--folder", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.folder is not None and not args.folder.is_dir():
        msg = f"Folder not found: {args.folder}"
        if args.json:
            print(json.dumps([{"name": "Resume folder", "ok": False, "detail": msg,
                               "fix": "Pass an existing folder with --folder.", "required": True}], indent=2))
        else:
            print(f"MISSING: Resume folder - {msg}")
            print("  Fix: Pass an existing folder with --folder.")
        return 1

    checks = check_all(args.folder)
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        for c in checks:
            status = "OK" if c.ok else "MISSING"
            line = f"{status}: {c.name} - {c.detail}"
            if not c.ok and c.fix:
                line += f"\n  Fix: {c.fix}"
            print(line)
    return 0 if all(c.ok or not c.required for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
