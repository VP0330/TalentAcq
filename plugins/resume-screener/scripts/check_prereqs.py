"""Prerequisite checker for the resume-screener plugin.

Usage: check_prereqs.py [--folder PATH] [--json]
Exit code 0 if every required check passes, else 1.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import RESUME_EXTS  # noqa: E402,F401

REQUIREMENTS = Path(__file__).resolve().parent / "requirements.txt"

_WORD_PROBE = (
    "$w = New-Object -ComObject Word.Application; "
    "$w.Quit(); "
    "[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($w)"
)


# Cheap fallback when launching Word is too slow to finish inside the timeout:
# only checks that the Word.Application COM class is registered.
_WORD_REGISTERED = "if ([type]::GetTypeFromProgID('Word.Application')) { exit 0 } else { exit 1 }"


def _run_ps(exe: str, script: str) -> bool:
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


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
    for exe in ("powershell", "pwsh"):
        if not shutil.which(exe):
            continue
        try:
            if _run_ps(exe, _WORD_PROBE):
                return "word"
        except subprocess.TimeoutExpired:
            # Word can take ~5 s to cold-start; fall back to registration check.
            try:
                if _run_ps(exe, _WORD_REGISTERED):
                    return "word"
            except (subprocess.TimeoutExpired, OSError):
                pass
        except OSError:
            pass
        break
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
