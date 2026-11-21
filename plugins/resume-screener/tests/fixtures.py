"""Fixture builders for extraction tests."""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import fitz

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path: Path, paragraphs: list[list[tuple[str, dict]]]) -> None:
    body = []
    for para in paragraphs:
        runs = []
        for text, props in para:
            rpr = ""
            if props.get("vanish"):
                rpr += "<w:vanish/>"
            if "color" in props:
                rpr += f'<w:color w:val="{props["color"]}"/>'
            if "sz" in props:
                rpr += f'<w:sz w:val="{props["sz"]}"/>'
            rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
            runs.append(f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>')
        body.append(f"<w:p>{''.join(runs)}</w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{"".join(body)}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)


def make_pdf(path: Path, lines: list[tuple[str, dict]]) -> None:
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    for text, props in lines:
        kwargs = {"fontsize": props.get("fontsize", 11)}
        if "color" in props:
            kwargs["color"] = props["color"]
        if "render_mode" in props:
            kwargs["render_mode"] = props["render_mode"]
        page.insert_text((72, y), text, **kwargs)
        y += 24
    doc.save(str(path))
    doc.close()


def make_image_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(100, 100, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    doc.save(str(path))
    doc.close()
