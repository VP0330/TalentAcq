"""Extract visible text from resumes/JDs and separate hidden text.

Hidden text (white, tiny, invisible, vanished, off-page) is written apart so the
AI reviewer never sees it and it can be flagged as possible prompt injection.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import RESUME_EXTS, text_dir  # noqa: E402
from check_prereqs import find_doc_converter  # noqa: E402

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NEAR_WHITE = 0xF0
_MIN_CHARS = 50
_DOC_TIMEOUT = 60

_WORD_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$w=New-Object -ComObject Word.Application;"
    "try{"
    "$w.Visible=$false;$w.DisplayAlerts=0;$w.AutomationSecurity=3;"
    "$d=$w.Documents.Open($env:RS_IN,$false,$true);"
    "try{$d.SaveAs2($env:RS_OUT,16)}finally{$d.Close(0)}"
    "}catch{[Console]::Error.WriteLine($_.Exception.Message);exit 1}"
    "finally{$w.Quit()}"
)


@dataclass
class Extracted:
    visible: str
    hidden: list[str] = field(default_factory=list)
    method: str = ""
    status: str = "ok"  # "ok" | "no_text_layer" | "error"
    reason: str = ""


# ---------------------------------------------------------------- DOCX

def _near_white(val: str) -> bool:
    val = val.strip()
    if len(val) != 6:
        return False
    try:
        r, g, b = int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
    except ValueError:
        return False
    return min(r, g, b) >= _NEAR_WHITE


def _run_hidden(run: ET.Element) -> bool:
    rpr = run.find(f"{_W}rPr")
    if rpr is None:
        return False
    vanish = rpr.find(f"{_W}vanish")
    if vanish is not None and vanish.get(f"{_W}val", "true").lower() not in ("0", "false", "off"):
        return True
    color = rpr.find(f"{_W}color")
    if color is not None and _near_white(color.get(f"{_W}val", "")):
        return True
    sz = rpr.find(f"{_W}sz")
    if sz is not None:
        try:
            if int(sz.get(f"{_W}val", "")) < 8:
                return True
        except ValueError:
            pass
    return False


def _run_text(run: ET.Element) -> str:
    parts = []
    for child in run:
        if child.tag == f"{_W}t" or child.tag == f"{_W}delText":
            parts.append(child.text or "")
        elif child.tag == f"{_W}tab":
            parts.append("\t")
        elif child.tag in (f"{_W}br", f"{_W}cr"):
            parts.append(" ")
        elif child.tag == f"{_W}noBreakHyphen":
            parts.append("-")
    return "".join(parts)


def _own_runs(el: ET.Element):
    """Yield runs under ``el`` without descending into nested paragraphs."""
    for child in el:
        if child.tag == f"{_W}r":
            yield child
        elif child.tag != f"{_W}p":
            yield from _own_runs(child)


def extract_docx(path: Path) -> Extracted:
    with zipfile.ZipFile(path) as z:
        data = z.read("word/document.xml")
    root = ET.fromstring(data)
    visible_paras: list[str] = []
    hidden: list[str] = []
    for p in root.iter(f"{_W}p"):
        vis, hid = [], []
        for run in _own_runs(p):
            text = _run_text(run)
            if not text:
                continue
            (hid if _run_hidden(run) else vis).append(text)
        if vis:
            visible_paras.append("".join(vis))
        hidden_text = "".join(hid).strip()
        if hidden_text:
            hidden.append(hidden_text)
    return Extracted("\n".join(visible_paras), hidden, "docx")


# ---------------------------------------------------------------- PDF

def _rgb_near_white(color: int) -> bool:
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return min(r, g, b) >= _NEAR_WHITE


def _trace_hidden_texts(page) -> set[str]:
    hidden: set[str] = set()
    try:
        trace = page.get_texttrace()
    except Exception:
        return hidden
    for entry in trace:
        if entry.get("type") == 3 or entry.get("opacity") == 0:
            text = "".join(ch[0] if isinstance(ch[0], str) else chr(ch[0])
                           for ch in entry.get("chars", ()))
            text = text.strip()
            if text:
                hidden.add(text)
    return hidden


def extract_pdf(path: Path) -> Extracted:
    import fitz  # imported lazily so DOCX-only use works without pymupdf

    doc = fitz.open(str(path))
    try:
        if doc.needs_pass:
            raise ValueError("PDF is password protected")
        blocks_out: list[str] = []
        hidden: list[str] = []
        for page in doc:
            trace_hidden = _trace_hidden_texts(page)
            rect = page.rect
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                lines_out: list[str] = []
                for line in block.get("lines", []):
                    vis_spans: list[str] = []
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            vis_spans.append(text)
                            continue
                        is_hidden = (
                            _rgb_near_white(span.get("color", 0))
                            or span.get("size", 12) < 4
                            or not fitz.Rect(span["bbox"]).intersects(rect)
                            or text.strip() in trace_hidden
                        )
                        if is_hidden:
                            hidden.append(text.strip())
                        else:
                            vis_spans.append(text)
                    line_text = "".join(vis_spans).strip()
                    if line_text:
                        lines_out.append(line_text)
                if lines_out:
                    blocks_out.append("\n".join(lines_out))
        return Extracted("\n\n".join(blocks_out), hidden, "pdf")
    finally:
        doc.close()


# ---------------------------------------------------------------- DOC

def extract_doc(path: Path, tmp_dir: Path) -> Extracted:
    converter = find_doc_converter()
    if converter is None:
        return Extracted("", [], "doc", "error", "no .doc converter available (Word or LibreOffice)")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"{uuid.uuid4().hex}.docx"
    try:
        if converter == "word":
            exe = shutil.which("powershell") or shutil.which("pwsh")
            if not exe:
                return Extracted("", [], "doc-word", "error", "PowerShell not found")
            env = dict(os.environ, RS_IN=str(path.resolve()), RS_OUT=str(out))
            try:
                proc = subprocess.run(
                    [exe, "-NoProfile", "-Command", _WORD_SCRIPT],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=_DOC_TIMEOUT, env=env,
                )
            except subprocess.TimeoutExpired:
                return Extracted("", [], "doc-word", "error",
                                 f"Word conversion timed out after {_DOC_TIMEOUT}s")
            method = "doc-word"
            err = (proc.stderr or "").strip()
        else:
            try:
                proc = subprocess.run(
                    ["soffice", "--headless", "--convert-to", "docx", "--outdir", str(tmp_dir),
                     str(path)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=_DOC_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                return Extracted("", [], "doc-libreoffice", "error",
                                 f"LibreOffice conversion timed out after {_DOC_TIMEOUT}s")
            method = "doc-libreoffice"
            err = (proc.stderr or "").strip()
            produced = tmp_dir / f"{path.stem}.docx"
            if produced.exists():
                produced.replace(out)
        if not out.exists():
            reason = err.splitlines()[0] if err else "conversion produced no output"
            return Extracted("", [], method, "error", f"could not convert .doc: {reason}")
        result = extract_docx(out)
        result.method = method
        return result
    finally:
        try:
            out.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- plain / dispatch

def extract_plain(path: Path) -> Extracted:
    text = path.read_text(encoding="utf-8", errors="replace")
    return Extracted(text.lstrip("﻿"), [], "plain")


def _first_line(e: BaseException) -> str:
    lines = str(e).strip().splitlines()
    return lines[0] if lines else type(e).__name__


def extract_file(path: Path, tmp_dir: Path) -> Extracted:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            result = extract_pdf(path)
        elif suffix == ".docx":
            result = extract_docx(path)
        elif suffix == ".doc":
            result = extract_doc(path, tmp_dir)
            if result.status == "error":
                return result
        else:
            result = extract_plain(path)
            if not result.visible.strip():
                result.status, result.reason = "error", "no readable text"
            return result
    except Exception as e:  # noqa: BLE001 - per-file problems go in the manifest
        return Extracted("", [], suffix.lstrip(".") or "unknown", "error", _first_line(e))

    if len("".join(result.visible.split())) < _MIN_CHARS:
        if suffix == ".pdf":
            result.status, result.reason = "no_text_layer", "no text layer (scanned or image-only PDF?)"
        else:
            result.status, result.reason = "error", "no readable text"
    return result


# ---------------------------------------------------------------- folder

def extract_folder(role_folder: Path, jd_name: str) -> dict:
    role_folder = Path(role_folder)
    out_dir = text_dir(role_folder / "assessments")
    out_dir.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[str, str]] = [(jd_name, "jd")]
    resumes = sorted(
        p.name for p in role_folder.iterdir()
        if p.is_file() and p.suffix.lower() in RESUME_EXTS and p.name != jd_name
    )
    targets += [(name, "resume") for name in resumes]

    manifest: dict = {"jd": jd_name, "files": {}}
    with tempfile.TemporaryDirectory() as tmp:
        for name, role in targets:
            path = role_folder / name
            if path.is_file():
                result = extract_file(path, Path(tmp))
            else:
                result = Extracted("", [], "", "error", "file not found")
            (out_dir / f"{name}.txt").write_text(result.visible, encoding="utf-8")
            hidden_path = out_dir / f"{name}.hidden.txt"
            if result.hidden:
                lines = [" ".join(h.split()) for h in result.hidden]
                hidden_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif hidden_path.exists():
                hidden_path.unlink()
            manifest["files"][name] = {
                "status": result.status,
                "method": result.method,
                "reason": result.reason,
                "hidden_count": len(result.hidden),
                "role": role,
            }
    (out_dir / "_extract.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract resume/JD text with hidden-text separation.")
    ap.add_argument("role_folder")
    ap.add_argument("--jd", required=True, help="JD file name inside the role folder")
    args = ap.parse_args(argv)
    manifest = extract_folder(Path(args.role_folder), args.jd)
    files = manifest["files"].values()
    ok = sum(1 for f in files if f["status"] == "ok")
    nt = sum(1 for f in files if f["status"] == "no_text_layer")
    er = sum(1 for f in files if f["status"] == "error")
    print(f"extracted={ok} no_text_layer={nt} errors={er}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
