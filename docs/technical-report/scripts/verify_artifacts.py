#!/usr/bin/env python3
"""Check the four offline documents; never imports the application or calls a service."""

import hashlib
import json
import os
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OfflineHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag in {"script", "link", "img", "iframe"}:
            for key in ("src", "href"):
                if attrs.get(key, "").startswith(("http:", "https:", "//")):
                    self.external.append(attrs[key])


def check_link(source, href):
    if href.startswith(("http:", "https:", "#", "mailto:", "tel:", "data:")):
        return
    target = source.parent / unquote(href.split("#")[0])
    if not target.exists():
        raise ValueError(f"Broken local link in {source.name}: {href}")


def main():
    docs = [ROOT / "技術報告.md"] + [
        ROOT / "manuals" / f"{name}手冊.md" for name in ("使用者", "管理者", "開發維運")
    ]
    result = {
        "checked_on": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "documents": [],
        "scope": "Offline document structure, local links, extractable Chinese and output hashes; visual review is separate.",
        "deployed": False,
        "live_model_test": False,
    }
    for md in docs:
        html = md.with_suffix(".html")
        pdf = md.with_suffix(".pdf")
        parser = OfflineHTML()
        parser.feed(html.read_text())
        if parser.external:
            raise ValueError(f"External active content: {parser.external}")
        for href in parser.links:
            check_link(html, href)
        pages = PdfReader(pdf).pages
        chinese = [len(re.findall(r"[\u4e00-\u9fff]", page.extract_text() or "")) for page in pages]
        if not pages or min(chinese) < 10:
            raise ValueError(f"Empty or missing Chinese page: {pdf.name}")
        result["documents"].append(
            {
                "source": str(md.relative_to(ROOT)),
                "pages": len(pages),
                "minimum_chinese_characters_per_page": min(chinese),
                "offline_html": True,
                "sha256": {p.suffix[1:]: sha(p) for p in (md, html, pdf)},
            }
        )
    for md in ROOT.rglob("*.md"):
        if any(part.startswith(".") for part in md.relative_to(ROOT).parts):
            continue
        for href in re.findall(r"\]\(([^)]+)\)", md.read_text()):
            check_link(md, href)
    if output := os.environ.get("DOC_VERIFICATION_OUTPUT"):
        destination = Path(output).resolve()
        if destination.is_relative_to(ROOT):
            raise ValueError(
                "Store one-off verification output outside the technical-report directory"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "documents": len(docs),
                "pdf_pages": sum(d["pages"] for d in result["documents"]),
                "status": "passed",
            }
        )
    )


if __name__ == "__main__":
    main()
