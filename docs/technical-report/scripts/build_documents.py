#!/usr/bin/env python3
"""Offline Markdown/diagram publisher. Requires reportlab; no service calls."""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
SOURCES = [
    ROOT / "技術報告.md",
    *(ROOT / "manuals" / f"{name}手冊.md" for name in ("使用者", "管理者", "開發維運")),
]
INK = "#193A4B"
TEAL = "#177F71"


def font_path(variable, candidates):
    if os.environ.get(variable):
        return os.environ[variable]
    for name in candidates:
        if Path(name).exists():
            return name
    raise SystemExit(f"Set {variable} to a Traditional Chinese TrueType font file")


def register_fonts():
    regular = font_path(
        "DOC_FONT_REGULAR",
        ["/System/Library/Fonts/STHeiti Light.ttc", "/usr/share/fonts/truetype/arphic/uming.ttc"],
    )
    bold = font_path("DOC_FONT_BOLD", ["/System/Library/Fonts/STHeiti Medium.ttc", regular])
    pdfmetrics.registerFont(
        TTFont("DocSans", regular, subfontIndex=int(os.getenv("DOC_FONT_INDEX", "0")))
    )
    pdfmetrics.registerFont(
        TTFont("DocSansBold", bold, subfontIndex=int(os.getenv("DOC_FONT_INDEX", "0")))
    )
    pdfmetrics.registerFontFamily(
        "DocSans", normal="DocSans", bold="DocSansBold", italic="DocSans", boldItalic="DocSansBold"
    )


def inline(text, pdf=False):
    text = html.escape(text)
    text = re.sub(
        r"`([^`]+)`", r'<font color="#177F71">\1</font>' if pdf else r"<code>\1</code>", text
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)

    def link(match):
        label, href = match.group(1), html.unescape(match.group(2))
        if pdf:
            return (
                label
                if not href.startswith("https://")
                else f'<a href="{html.escape(href, quote=True)}" color="#177F71">{label}</a>'
            )
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, text)


def blocks(text):
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.startswith("```"):
            language = line[3:]
            content = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                content.append(lines[i])
                i += 1
            yield "code", (language, "\n".join(content))
        elif re.match(r"^#{1,4} ", line):
            level, title = line.split(" ", 1)
            yield "heading", (len(level), title)
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.match(r"^:?-+:?$", c) for c in cells):
                    rows.append(cells)
                i += 1
            yield "table", rows
            continue
        elif match := re.fullmatch(r"!\[([^\]]*)\]\(([^)]+)\)", line):
            yield "image", (match[1], match[2])
        elif line.startswith("- ") or re.match(r"^\d+\. ", line):
            yield "list", line
        elif line.startswith("> "):
            yield "quote", line[2:]
        else:
            para = [line]
            while (
                i + 1 < len(lines)
                and lines[i + 1].strip()
                and not re.match(r"^(#{1,4} |\||```|!\[|- |\d+\. |> )", lines[i + 1])
            ):
                i += 1
                para.append(lines[i].strip())
            yield "paragraph", " ".join(para)
        i += 1


def diagrams():
    for file in sorted(ASSETS.glob("*.diagram.json")):
        diagram = json.loads(file.read_text())
        w, h = diagram["width"], diagram["height"]
        nodes = {n["id"]: n for n in diagram["nodes"]}
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(diagram["title"])}">',
            '<defs><marker id="tip" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#5B7282"/></marker></defs>',
            '<rect width="100%" height="100%" fill="white"/>',
        ]
        mmd = ["flowchart TD"]
        for n in nodes.values():
            mmd.append(f'    {n["id"]}["{n["label"].replace(chr(10), "<br/>")}"]')
        for edge in diagram["edges"]:
            a, b = nodes[edge[0]], nodes[edge[1]]
            x1, y1, x2, y2 = endpoints(a, b)
            svg.append(
                f'<path d="M{x1},{y1} L{x2},{y2}" stroke="#5B7282" fill="none" stroke-width="2" marker-end="url(#tip)"/>'
            )
            mmd.append(f"    {edge[0]} --> {edge[1]}")
        for n in nodes.values():
            fill = (
                "#E9F4F0"
                if n.get("kind") == "accent"
                else "#FFF3D8"
                if n.get("kind") == "decision"
                else "#F3F6F8"
            )
            svg.append(
                f'<rect x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" rx="6" fill="{fill}" stroke="#BECDD3"/>'
            )
            rows = n["label"].split("\n")
            for j, label in enumerate(rows):
                y = n["y"] + n["h"] / 2 + (j - (len(rows) - 1) / 2) * 24 + 7
                svg.append(
                    f'<text x="{n["x"] + n["w"] / 2}" y="{y}" text-anchor="middle" font-family="Heiti TC, Noto Sans CJK TC, sans-serif" font-size="20" fill="{INK}">{html.escape(label)}</text>'
                )
        svg.append("</svg>")
        stem = file.name.replace(".diagram.json", "")
        (ASSETS / f"{stem}.svg").write_text("\n".join(svg))
        (ASSETS / f"{stem}.mmd").write_text("\n".join(mmd) + "\n")


def endpoints(a, b):
    if abs(a["y"] - b["y"]) < 30:
        right = b["x"] > a["x"]
        return (
            a["x"] + a["w"] if right else a["x"],
            a["y"] + a["h"] / 2,
            b["x"] if right else b["x"] + b["w"],
            b["y"] + b["h"] / 2,
        )
    if b["y"] < a["y"]:
        return a["x"] + a["w"] / 2, a["y"], b["x"] + b["w"] / 2, b["y"] + b["h"]
    return a["x"] + a["w"] / 2, a["y"] + a["h"], b["x"] + b["w"] / 2, b["y"]


class Diagram(Flowable):
    def __init__(self, data, width):
        super().__init__()
        self.data = data
        self.width = width
        self.height = width * data["height"] / data["width"]

    def draw(self):
        c = self.canv
        d = self.data
        scale = self.width / d["width"]
        c.saveState()
        c.scale(scale, scale)
        nodes = {n["id"]: n for n in d["nodes"]}
        for e in d["edges"]:
            x1, y1, x2, y2 = endpoints(nodes[e[0]], nodes[e[1]])
            c.setStrokeColor(colors.HexColor("#5B7282"))
            c.setLineWidth(1.5)
            c.line(x1, d["height"] - y1, x2, d["height"] - y2)
            import math

            angle = math.atan2(y2 - y1, x2 - x1)
            for delta in (-0.5, 0.5):
                c.line(
                    x2,
                    d["height"] - y2,
                    x2 - 9 * math.cos(angle + delta),
                    d["height"] - y2 + 9 * math.sin(angle + delta),
                )
        for n in nodes.values():
            fill = (
                "#E9F4F0"
                if n.get("kind") == "accent"
                else "#FFF3D8"
                if n.get("kind") == "decision"
                else "#F3F6F8"
            )
            c.setFillColor(colors.HexColor(fill))
            c.setStrokeColor(colors.HexColor("#BECDD3"))
            c.roundRect(n["x"], d["height"] - n["y"] - n["h"], n["w"], n["h"], 6, stroke=1, fill=1)
            rows = n["label"].split("\n")
            c.setFillColor(colors.HexColor(INK))
            c.setFont("DocSans", 20)
            for j, line in enumerate(rows):
                y = n["y"] + n["h"] / 2 + (j - (len(rows) - 1) / 2) * 24 + 7
                c.drawCentredString(n["x"] + n["w"] / 2, d["height"] - y, line)
        c.restoreState()


CSS = """
:root{color-scheme:light}*{box-sizing:border-box}body{overflow-wrap:anywhere;margin:0;color:#193a4b;background:#f3f6f8;font:17px/1.85 'Heiti TC','Noto Sans CJK TC',system-ui,sans-serif}main{max-width:1050px;margin:0 auto;background:white;padding:60px 72px 90px}h1{font-size:36px;line-height:1.4;border-bottom:4px solid #177f71;padding-bottom:20px}h2{font-size:27px;margin-top:48px;border-top:1px solid #becdd3;padding-top:24px}h3{font-size:21px;margin-top:30px}p{margin:14px 0}table{table-layout:fixed;border-collapse:collapse;width:100%;margin:20px 0;font-size:15px}th,td{border:1px solid #becdd3;padding:10px 12px;text-align:left;vertical-align:top}th{background:#177f71;color:white}tr:nth-child(even){background:#f3f6f8}code{font-size:.9em;background:#eef4f3;padding:2px 4px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:18px;font-size:14px}blockquote{border-left:4px solid #a87515;padding:12px 20px;background:#fff3d8;margin:20px 0}figure{margin:28px 0}figure svg{width:100%;height:auto}figcaption{font-size:14px;color:#5b7282}a{color:#177f71}li{margin:8px 0}nav{font-size:16px;border-bottom:1px solid #becdd3;padding:10px 0 24px}footer{font-size:13px;color:#5b7282;margin-top:55px}@media(max-width:700px){main{padding:26px 20px}h1{font-size:28px}table{font-size:13px}th,td{padding:6px}}@media print{body,main{background:white}main{padding:0;max-width:none}h2,h3{break-after:avoid}table,figure,pre{break-inside:avoid}a{color:inherit}}
"""


def publish(source):
    source_text = source.read_text()
    parsed = list(blocks(source_text))
    version_match = re.search(r"版本日期：(\d{4}-\d{2}-\d{2})", source_text)
    version = version_match.group(1) if version_match else ""
    title = next(value[1] for kind, value in parsed if kind == "heading")
    h = []
    toc = []
    body = ParagraphStyle(
        "BodyCJK",
        fontName="DocSans",
        fontSize=10.8,
        leading=17,
        wordWrap="CJK",
        textColor=colors.HexColor(INK),
        spaceAfter=8,
    )
    headstyles = {
        1: ParagraphStyle(
            "TitleCJK", parent=body, fontName="DocSansBold", fontSize=23, leading=33, spaceAfter=22
        ),
        2: ParagraphStyle(
            "H2CJK",
            parent=body,
            fontName="DocSansBold",
            fontSize=16,
            leading=23,
            spaceBefore=20,
            spaceAfter=12,
            keepWithNext=True,
        ),
        3: ParagraphStyle(
            "H3CJK",
            parent=body,
            fontName="DocSansBold",
            fontSize=12.5,
            leading=19,
            spaceBefore=13,
            spaceAfter=8,
            keepWithNext=True,
        ),
        4: ParagraphStyle(
            "H4CJK",
            parent=body,
            fontName="DocSansBold",
            fontSize=11.5,
            leading=18,
            spaceBefore=10,
            keepWithNext=True,
        ),
    }
    cell = ParagraphStyle("CellCJK", parent=body, fontSize=9.1, leading=14, spaceAfter=0)
    white = ParagraphStyle(
        "HeaderCellCJK", parent=cell, fontName="DocSansBold", textColor=colors.white
    )
    code = ParagraphStyle(
        "CodeCJK",
        parent=body,
        fontSize=8.9,
        leading=14,
        backColor=colors.HexColor("#F3F6F8"),
        borderPadding=8,
        spaceBefore=7,
        spaceAfter=12,
    )
    story = []
    width = 174 * mm
    for kind, value in parsed:
        if kind == "heading":
            level, text = value
            anchor = f"section-{len(toc) + 1}" if level == 2 else ""
            if level == 2:
                toc.append((anchor, text))
            h.append(
                f"<h{level}{f' id={chr(34)}{anchor}{chr(34)}' if anchor else ''}>{inline(text)}</h{level}>"
            )
            story.append(Paragraph(inline(text, True), headstyles[level]))
        elif kind in {"paragraph", "quote", "list"}:
            tag = "blockquote" if kind == "quote" else "p"
            h.append(f"<{tag}>{inline(value)}</{tag}>")
            story.append(Paragraph(inline(value, True), body))
        elif kind == "code":
            language, content = value
            h.append(f"<pre><code>{html.escape(content)}</code></pre>")
            code_text = "<br/>".join(
                html.escape(line or " ").replace(" ", "&#160;") for line in content.splitlines()
            )
            story.append(KeepTogether([Paragraph(code_text, code)]))
        elif kind == "table":
            rows = value
            count = max(map(len, rows))
            rows = [r + [""] * (count - len(r)) for r in rows]
            h.append(
                "<table>"
                + "".join(
                    "<tr>"
                    + "".join(
                        f"<{'th' if i == 0 else 'td'}>{inline(v)}</{'th' if i == 0 else 'td'}>"
                        for v in row
                    )
                    + "</tr>"
                    for i, row in enumerate(rows)
                )
                + "</table>"
            )
            weights = [1] * count
            if count == 2:
                weights = [0.32, 0.68]
            if count == 3:
                weights = [0.22, 0.35, 0.43]
            if count == 4:
                weights = [0.17, 0.25, 0.29, 0.29]
            col_widths = [width * x / sum(weights) for x in weights]
            cells = [
                [Paragraph(inline(v, True), white if i == 0 else cell) for v in row]
                for i, row in enumerate(rows)
            ]
            table = Table(cells, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TEAL)),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#F3F6F8")],
                        ),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BECDD3")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.extend([Spacer(1, 6), table, Spacer(1, 12)])
        elif kind == "image":
            label, target = value
            asset = (source.parent / target).resolve()
            if asset.suffix == ".svg":
                svg = asset.read_text()
                h.append(f"<figure>{svg}<figcaption>{inline(label)}</figcaption></figure>")
                spec = asset.with_suffix(".diagram.json")
                if spec.exists():
                    story.extend(
                        [
                            Diagram(json.loads(spec.read_text()), width),
                            Paragraph(inline(label, True), cell),
                            Spacer(1, 15),
                        ]
                    )
                else:
                    raise ValueError(f"Diagram source missing: {spec.relative_to(ROOT)}")
    nav = "<nav>" + "<br>".join(f'<a href="#{a}">{inline(t)}</a>' for a, t in toc) + "</nav>"
    document = (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + html.escape(title)
        + "</title><style>"
        + CSS
        + "</style></head><body><main>"
        + h[0]
        + nav
        + "\n".join(h[1:])
        + "<footer>本文件可離線閱讀。範圍與驗證狀態以文件內說明及遷移報告為準。</footer></main></body></html>"
    )
    source.with_suffix(".html").write_text(document)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#BECDD3"))
        canvas.line(18 * mm, 16 * mm, 192 * mm, 16 * mm)
        canvas.setFont("DocSans", 8)
        canvas.setFillColor(colors.HexColor("#5B7282"))
        canvas.drawString(18 * mm, 11 * mm, f"性騷擾防治智能 AI　技術文件　{version}")
        canvas.drawRightString(192 * mm, 11 * mm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(source.with_suffix(".pdf")),
        pagesize=(210 * mm, 297 * mm),
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=23 * mm,
        title=title,
        author="RAG_HARASS_BOT project",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(
        json.dumps(
            {"source": str(source.relative_to(ROOT)), "html": True, "pdf": True}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    register_fonts()
    diagrams()
    for source in SOURCES:
        if source.exists():
            publish(source)
