#!/usr/bin/env python3
"""Render each generated PDF locally for visual review; never calls a service."""

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tool = (
    Path(os.environ["RUNTIME_BIN_DIR"]) / "pdftoppm"
    if os.environ.get("RUNTIME_BIN_DIR")
    else shutil.which("pdftoppm")
)
if not tool:
    raise SystemExit("pdftoppm is required; set RUNTIME_BIN_DIR or PATH")
jobs = [(ROOT / "技術報告.pdf", "doc-pages/report", 90)]
jobs += [
    (ROOT / "manuals" / f"{name}手冊.pdf", f"doc-pages/{folder}", 90)
    for name, folder in (("使用者", "user"), ("管理者", "admin"), ("開發維運", "developer"))
]
RENDER_ROOT = Path(os.environ.get("DOC_RENDER_DIR", "/tmp/rag-delivery-doc-pages"))
for source, relative, dpi in jobs:
    output = RENDER_ROOT / relative
    output.mkdir(parents=True, exist_ok=True)
    for previous in output.glob("page-*.png"):
        previous.unlink()
    subprocess.run(
        [str(tool), "-r", str(dpi), "-png", str(source), str(output / "page")], check=True
    )
    print(f"{source.name}: {len(list(output.glob('page-*.png')))} pages")
