"""Extract visible text from resumes/JDs and separate hidden text.

Hidden text (white on a light background, tiny, invisible, vanished incl. via DOCX styles,
off-page) is written apart so the
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
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
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

def _hex_rgb(val: str):
    val = (val or "").strip()
    if len(val) != 6:
        return None
    try:
        return int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
    except ValueError:
        return None


def _near_white(val: str) -> bool:
    rgb = _hex_rgb(val)
    return rgb is not None and min(rgb) >= _NEAR_WHITE


def _luminance(r: float, g: float, b: float) -> float:
    """Relative luminance (0..1) of 0..1 RGB components."""
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _dark_shd(shd) -> bool:
    """True if a ``w:shd`` element paints a dark background (luminance < 0.5)."""
    if shd is None:
        return False
    val = (shd.get(f"{_W}val") or "").lower()
    fill = shd.get(f"{_W}color") if val == "solid" else shd.get(f"{_W}fill")
    rgb = _hex_rgb(fill or "")
    return rgb is not None and _luminance(*(c / 255 for c in rgb)) < 0.5


def _on(el) -> bool:
    return el.get(f"{_W}val", "true").lower() not in ("0", "false", "off")


class _Styles:
    """Run properties defined in ``word/styles.xml`` (docDefaults + basedOn chains)."""

    def __init__(self, xml: bytes | None):
        self.by_id: dict = {}
        self.defaults = None
        if not xml:
            return
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return
        for st in root.iter(f"{_W}style"):
            sid = st.get(f"{_W}styleId")
            if sid:
                self.by_id[sid] = st
        self.defaults = root.find(f"{_W}docDefaults/{_W}rPrDefault/{_W}rPr")

    def chain(self, sid: str | None) -> list:
        """Style elements from the root of the basedOn chain down to ``sid``."""
        out, seen = [], set()
        while sid and sid in self.by_id and sid not in seen:
            seen.add(sid)
            st = self.by_id[sid]
            out.append(st)
            based = st.find(f"{_W}basedOn")
            sid = based.get(f"{_W}val") if based is not None else None
        return out[::-1]


def _apply_rpr(props: dict, rpr) -> None:
    if rpr is None:
        return
    for child in rpr:
        tag = child.tag
        if tag == f"{_W}vanish":
            props["vanish"] = _on(child)
        elif tag == f"{_W}color":
            props["color"] = child.get(f"{_W}val", "")
        elif tag == f"{_W}sz":
            try:
                props["sz"] = int(child.get(f"{_W}val", ""))
            except ValueError:
                pass
        elif tag == f"{_W}shd":
            props["shd_dark"] = _dark_shd(child)


def _run_props(run: ET.Element, para_style: str | None, styles: _Styles) -> dict:
    """Effective run properties: docDefaults < paragraph style < character style < direct."""
    props: dict = {}
    _apply_rpr(props, styles.defaults)
    for st in styles.chain(para_style):
        _apply_rpr(props, st.find(f"{_W}rPr"))
    rpr = run.find(f"{_W}rPr")
    if rpr is not None:
        rstyle = rpr.find(f"{_W}rStyle")
        if rstyle is not None:
            for st in styles.chain(rstyle.get(f"{_W}val")):
                _apply_rpr(props, st.find(f"{_W}rPr"))
    _apply_rpr(props, rpr)
    return props


def _run_hidden(run: ET.Element, para_style: str | None = None, styles: _Styles | None = None,
                dark_background: bool = False) -> bool:
    props = _run_props(run, para_style, styles or _Styles(None))
    if props.get("vanish"):
        return True
    if props.get("sz", 99) < 8:
        return True
    if _near_white(props.get("color", "")) and not (dark_background or props.get("shd_dark")):
        return True
    return False


def _run_text(run: ET.Element) -> str:
    parts = []
    for child in run:
        if child.tag == f"{_W}t":
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
        elif child.tag in (f"{_W}p", _MC + "Fallback"):
            continue
        else:
            yield from _own_runs(child)


def _para_background(p: ET.Element, para_style: str | None, styles: _Styles, parents: dict) -> bool:
    """True if the paragraph, its style chain, or its enclosing table cell has dark shading."""
    if _dark_shd(p.find(f"{_W}pPr/{_W}shd")):
        return True
    shds = [st.find(f"{_W}pPr/{_W}shd") for st in styles.chain(para_style)]
    shds = [s for s in shds if s is not None]
    if shds and _dark_shd(shds[-1]):
        return True
    el = parents.get(p)
    while el is not None:
        if el.tag == f"{_W}tc":
            return _dark_shd(el.find(f"{_W}tcPr/{_W}shd"))
        el = parents.get(el)
    return False


def extract_docx(path: Path) -> Extracted:
    with zipfile.ZipFile(path) as z:
        data = z.read("word/document.xml")
        try:
            styles_xml = z.read("word/styles.xml")
        except KeyError:
            styles_xml = None
    root = ET.fromstring(data)
    styles = _Styles(styles_xml)
    parents = {child: parent for parent in root.iter() for child in parent}
    visible_paras: list[str] = []
    hidden: list[str] = []
    for p in root.iter(f"{_W}p"):
        pstyle_el = p.find(f"{_W}pPr/{_W}pStyle")
        para_style = pstyle_el.get(f"{_W}val") if pstyle_el is not None else None
        dark = _para_background(p, para_style, styles, parents)
        vis, hid = [], []
        for run in _own_runs(p):
            deleted = "".join(d.text or "" for d in run.findall(f"{_W}delText"))
            if deleted:
                hid.append(deleted)  # tracked deletion: not part of the final document
            text = _run_text(run)
            if not text:
                continue
            (hid if _run_hidden(run, para_style, styles, dark) else vis).append(text)
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


def _trace_hidden_chars(page) -> list[tuple[str, "object"]]:
    """(char, bbox) for every glyph drawn invisibly (render mode 3 / opacity 0)."""
    import fitz

    out: list[tuple[str, object]] = []
    try:
        trace = page.get_texttrace()
    except Exception:
        return out
    for entry in trace:
        if entry.get("type") == 3 or entry.get("opacity") == 0:
            for ch in entry.get("chars", ()):
                c = ch[0] if isinstance(ch[0], str) else chr(ch[0])
                out.append((c, fitz.Rect(ch[3])))
    return out


def _dark_backgrounds(page) -> list:
    """Rects of dark filled drawings (luminance < 0.5) and of images on the page."""
    import fitz

    rects = []
    try:
        for d in page.get_drawings():
            fill = d.get("fill")
            if fill and len(fill) >= 3 and _luminance(*fill[:3]) < 0.5:
                rects.append(fitz.Rect(d["rect"]))
    except Exception:
        pass
    try:
        rects += [fitz.Rect(info["bbox"]) for info in page.get_image_info()]
    except Exception:
        pass
    return rects


def _on_dark(bbox, backgrounds) -> bool:
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in backgrounds)


def _char_in_hidden(char: dict, hidden_chars) -> bool:
    c = char.get("c", "")
    x0, y0, x1, y1 = char["bbox"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return any(hc == c and r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for hc, r in hidden_chars)


def extract_pdf(path: Path) -> Extracted:
    import fitz  # imported lazily so DOCX-only use works without pymupdf

    doc = fitz.open(str(path))
    try:
        if doc.needs_pass:
            raise ValueError("PDF is password protected")
        blocks_out: list[str] = []
        hidden: list[str] = []
        for page in doc:
            hidden_chars = _trace_hidden_chars(page)
            backgrounds = _dark_backgrounds(page)
            rect = page.rect
            for block in page.get_text("rawdict").get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                lines_out: list[str] = []
                for line in block.get("lines", []):
                    vis_spans: list[str] = []
                    for span in line.get("spans", []):
                        chars = span.get("chars", [])
                        text = "".join(c.get("c", "") for c in chars)
                        if not text.strip():
                            vis_spans.append(text)
                            continue
                        if (
                            (_rgb_near_white(span.get("color", 0))
                             and not _on_dark(span["bbox"], backgrounds))
                            or span.get("size", 12) < 4
                            or not fitz.Rect(span["bbox"]).intersects(rect)
                        ):
                            hidden.append(text.strip())
                            continue
                        vis, hid = [], []
                        for ch in chars:
                            (hid if _char_in_hidden(ch, hidden_chars) else vis).append(ch.get("c", ""))
                        if hid:
                            hidden.append("".join(hid).strip())
                        vis_spans.append("".join(vis))
                    line_text = "".join(vis_spans).strip()
                    if line_text:
                        lines_out.append(line_text)
                if lines_out:
                    blocks_out.append(chr(10).join(lines_out))
        return Extracted((chr(10) * 2).join(blocks_out), hidden, "pdf")
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
        if p.is_file()
        and p.suffix.lower() in RESUME_EXTS
        and os.path.normcase(p.name) != os.path.normcase(jd_name)
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
