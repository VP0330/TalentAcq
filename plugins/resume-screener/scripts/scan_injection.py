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
        r"\byou\s+are\s+(now\s+)?(an?\s+)?(ai|language model|chatbot|llm|ai assistant)\b",
        r"\b(ignore|reveal|override|print|show|new|your|the)\s+system\s+prompt\b",
        r"\b(rate|score|rank|mark|classify)\s+(this|me)\s+(candidate|applicant|resume)\b",
        r"\b(rate|score|rank|mark|classify|label)\s+(this|me|this candidate|the candidate|the applicant)\s+(as\s+)?(an?\s+)?(strong|perfect|top|best|excellent)\s+(match|fit|candidate)\b",
        r"\bas\s+an\s+ai\b",
        r"\b(note|message|instruction)s?\s+to\s+(the\s+)?(ai|assistant|screener|recruit\w*\s+bot|model)\b",
        r"\b(ai|assistant|model|llm|screener|chatgpt|gpt|claude)\b.*\b(read|open|include|fetch|visit)\b.*\b(file|url|link|http)",
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
_WRAP_MAX = 300
_SPLIT = re.compile(r"[,|•;]")


@dataclass
class Flag:
    kind: str
    text: str


def _trunc(s: str) -> str:
    s = s.strip()
    return s if len(s) <= _MAX_TEXT else s[:_MAX_TEXT]


def _span_flags(text: str) -> list[Flag]:
    """Pattern matches in a multi-line normalised string, as snippet flags."""
    flags: list[Flag] = []
    for kind, pats in (("injection_phrase", INJECTION_PATTERNS), ("markup_token", MARKUP_PATTERNS)):
        for p in pats:
            for m in p.finditer(text):
                if len(m.group(0)) <= _WRAP_MAX:
                    flags.append(Flag(kind, _trunc(m.group(0))))
    return flags


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
    # Catch injections split across a line wrap.
    lines = [" ".join(x.split()) for x in visible.splitlines() if x.strip()]
    joined = " ".join(lines)
    for f in _span_flags(joined):
        if not any(f.text in ln for ln in lines):
            flags.append(f)
    for entry in hidden:
        if not entry.strip():
            continue
        flags.extend(_pattern_flags(entry))
        flags.append(Flag("hidden_text", _trunc(entry)))
    seen: set = set()
    unique: list[Flag] = []
    for f in flags:
        if (f.kind, f.text) not in seen:
            seen.add((f.kind, f.text))
            unique.append(f)
    return unique


def is_flagged(flags: list[Flag]) -> bool:
    return any(f.kind != "keyword_run" for f in flags)


def summarize_flags(scan: dict) -> dict:
    """Counts per flag kind for each entry; carries no resume-derived text."""
    out: dict = {}
    for name, entry in scan.items():
        kinds: dict = {}
        for f in entry.get("flags", []):
            k = f.get("kind")
            if isinstance(k, str):
                kinds[k] = kinds.get(k, 0) + 1
        out[name] = {"flagged": bool(entry.get("flagged")), "kinds": kinds}
    return out


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
    (tdir / "_flags_summary.json").write_text(
        json.dumps(summarize_flags(result), ensure_ascii=False, indent=2), encoding="utf-8"
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
