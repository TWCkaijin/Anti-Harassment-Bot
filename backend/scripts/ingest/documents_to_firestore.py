"""Vectorize and upload standard document Markdown files to Firestore.

Examples:
    python backend/scripts/ingest/documents_to_firestore.py
    python backend/scripts/ingest/documents_to_firestore.py --upload
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from backend.scripts.common.firestore_ingest import (
    add_common_ingest_args,
    build_document_documents,
    collect_markdown_documents,
    run_async,
    run_ingestion,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest data/documents/*.md into the Firestore law/document RAG collection."
    )
    add_common_ingest_args(parser, Path("data/documents"))
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    documents = collect_markdown_documents(args.data_dir, build_document_documents)
    await run_ingestion(documents, args)


if __name__ == "__main__":
    run_async(main())
