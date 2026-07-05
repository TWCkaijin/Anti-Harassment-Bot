"""Materialize built-in DefaultRAG documents into data/documents/*.md.

This is a one-time convenience transform for the legacy in-code document seed.
New document sources should be edited directly under data/documents/.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.app.rag.default_rag import _DEFAULT_DOCUMENTS


def slugify(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "-", value).strip().replace(" ", "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write DefaultRAG seed docs to data/documents.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/documents"))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for item in _DEFAULT_DOCUMENTS:
        metadata = item["metadata"]
        source = metadata["source"]
        doc_type = metadata.get("type", "law")
        path = args.output_dir / f"{slugify(source)}.md"
        if path.exists() and not args.overwrite:
            print(f"skip existing {path}")
            continue
        path.write_text(
            "\n".join(
                [
                    "---",
                    f"source: {source}",
                    f"type: {doc_type}",
                    "jurisdiction: 台灣",
                    "---",
                    f"# {source}",
                    "",
                    item["content"],
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
