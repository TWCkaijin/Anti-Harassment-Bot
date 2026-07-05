"""Compatibility dispatcher for Firestore Vector Search ingestion.

Prefer the split entrypoints:
    python backend/scripts/ingest/documents_to_firestore.py
    python backend/scripts/ingest/judgments_to_firestore.py
    python backend/scripts/ingest/remedies_to_firestore.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from backend.scripts.common.firestore_ingest import (
    add_common_ingest_args,
    build_document_documents,
    build_remedy_documents,
    collect_judgment_documents,
    collect_markdown_documents,
    run_async,
    run_ingestion,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest standardized data/* RAG sources.")
    parser.add_argument(
        "--kind",
        default="all",
        choices=["all", "document", "judgment", "remedy"],
        help="Which standardized data source to ingest.",
    )
    add_common_ingest_args(parser, Path("data"))
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    base_dir = args.data_dir
    documents = []

    if args.kind in {"all", "document"}:
        documents.extend(
            collect_markdown_documents(base_dir / "documents", build_document_documents)
        )
    if args.kind in {"all", "judgment"}:
        documents.extend(collect_judgment_documents(base_dir / "judgments"))
    if args.kind in {"all", "remedy"}:
        documents.extend(collect_markdown_documents(base_dir / "remedy", build_remedy_documents))

    await run_ingestion(documents, args)


if __name__ == "__main__":
    run_async(main())
