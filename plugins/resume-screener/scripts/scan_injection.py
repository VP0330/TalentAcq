"""Guard I1: flag resumes (and the JD) containing text aimed at AI screeners."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import text_dir  # noqa: E402

_I = re.IGNORECASE

INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(p, _I)
    for p in (
        r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions|prompts?|rules)",
        r"disregard\s+(all\s+)?(previous|prior|above)",
        r"\byou\s+are\s+(now\s+)?(an?\s+)?(ai|assistant|language model|chatbot|llm)\b",
        r"\bsystem\s+prompt\b",
        r"\b(rate|score|rank|mark|classify)\s+(this|me|the)\s+(candidate|applicant|resume)\b",
        r"\b(strong|perfect|best)\s+(match|fit|candidate)\b.*\b(rate|label|mark|output|respond)\b",
        r"\bas\s+an\s+ai\b",
        r"\b(note|message|instruction)s?\s+to\s+(the\s+)?(ai|assistant|screener|recruit\w*\s+bot|model)\b",
        r"\b(read|open|include|fetch)\b.*\b(file|url|link|http)\b.*\b(and|then)\b",
    )
]

MARKUP_PATTERNS: list[re.Pattern] = [
    re.compile(r"<\s*/?\s*(system|assistant|user|instructions?)\s*>", _I),
    re.compile(r"\[/?INST\]", _I),
    re.compile(r"^\s*#{2,}\s*(system|instruction)", _I | re.MULTILINE),
    re.compile(r"<\|im_(start|end)\|>", _I),
]

KEYWORD_RUN_MIN = 50
_MAX_TEXT = 200
_SPLIT = re.compile(r"[,|•;]")


@dataclass
class Flag:
    kind: str
    text: str


def _trunc(s: str) -> str:
    s = s.strip()
    return s if len(s) <= _MAX_TEXT else s[:_MAX_TEXT]


def _pattern_flags(line: str) -> list[Flag]:
    flags: list[Flag] = []
    if any(p.search(line) for p in INJECTION_PATTERNS):
        flags.append(Flag("injection_phrase", _trunc(line)))
    if any(p.search(line) for p in MARKUP_PATTERNS):
        flags.append(Flag("markup_token", _trunc(line)))
    return flags


def scan_text(visible: str, hidden: list[str]) -> list[Flag]:
    flags: list[Flag] = []
    for line in visible.splitlines():
        if not line.strip():
            continue
        flags.extend(_pattern_flags(line))
        items = [x for x in _SPLIT.split(line) if x.strip()]
        if len(items) >= KEYWORD_RUN_MIN:
            flags.append(Flag("keyword_run", _trunc(line)))
    for entry in hidden:
        if not entry.strip():
            continue
        flags.extend(_pattern_flags(entry))
        flags.append(Flag("hidden_text", _trunc(entry)))
    return flags


def is_flagged(flags: list[Flag]) -> bool:
    return any(f.kind != "keyword_run" for f in flags)


def scan_folder(assessments_dir: Path) -> dict:
    tdir = text_dir(Path(assessments_dir))
    manifest = json.loads((tdir / "_extract.json").read_text(encoding="utf-8"))
    result: dict = {}
    for name, info in manifest.get("files", {}).items():
        if info.get("status") != "ok":
            continue
        visible = (tdir / f"{name}.txt").read_text(encoding="utf-8", errors="replace")
        hp = tdir / f"{name}.hidden.txt"
        hidden = (
            hp.read_text(encoding="utf-8", errors="replace").splitlines() if hp.exists() else []
        )
        flags = scan_text(visible, hidden)
        result[name] = {
            "flagged": is_flagged(flags),
            "flags": [{"kind": f.kind, "text": f.text} for f in flags],
        }
    (tdir / "_scan.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scan extracted resume/JD text for prompt injection.")
    ap.add_argument("role_folder")
    args = ap.parse_args(argv)
    assessments = Path(args.role_folder) / "assessments"
    result = scan_folder(assessments)
    manifest = json.loads(
        (text_dir(assessments) / "_extract.json").read_text(encoding="utf-8")
    )
    jd_flagged = bool(result.get(manifest.get("jd", ""), {}).get("flagged", False))
    flagged = sum(1 for v in result.values() if v["flagged"])
    print(f"scanned={len(result)} flagged={flagged} jd_flagged={str(jd_flagged).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
