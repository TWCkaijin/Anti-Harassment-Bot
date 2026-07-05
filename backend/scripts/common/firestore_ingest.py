"""Shared Firestore Vector Search ingestion helpers."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

sys.path.append(str(Path(__file__).resolve().parents[3]))

from firebase_admin import credentials, firestore, initialize_app
from google.cloud.firestore_v1.vector import Vector

from backend.app.core.config import get_settings
from backend.app.rag.embeddings import EmbeddingClient

settings = get_settings()

DataKind = Literal["document", "judgment", "remedy"]
DocumentBuilder = Callable[[Path], list["IngestDocument"]]


@dataclass(frozen=True)
class IngestDocument:
    doc_id: str
    collection_name: str
    content: str
    metadata: dict[str, Any]


def normalize_ws(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def stable_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", parts[0]).strip("-")[:48]
    return f"{slug}-{digest}" if slug else digest


def split_text(text: str, max_chars: int = 1600, overlap_chars: int = 180) -> list[str]:
    text = normalize_ws(text)
    if len(text) <= max_chars:
        return [text] if text else []

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + max_chars].strip())
                start += max_chars - overlap_chars
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current.strip())
            tail = current[-overlap_chars:] if overlap_chars and current else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
    if current:
        chunks.append(current.strip())
    return chunks


def infer_harassment_types(text: str) -> list[str]:
    rules = {
        "workplace": ["職場", "工作場所", "雇主", "受僱", "勞動", "勞青", "性別平等工作"],
        "campus": ["校園", "學校", "教育處", "性平教育", "性別平等教育"],
        "digital": ["私密影像", "性影像", "偷拍", "散布", "網路", "數位"],
        "stalking": ["跟蹤騷擾", "跟蹤"],
    }
    found = [
        name for name, keywords in rules.items() if any(keyword in text for keyword in keywords)
    ]
    return found or ["general"]


def infer_remedy_channels(text: str) -> list[str]:
    rules = {
        "hotline_113": ["113", "婦幼保護專線"],
        "police_110": ["110", "報案", "警局", "派出所", "警察"],
        "local_government": ["縣政府", "主管機關", "社會局"],
        "labor_department": ["勞青處", "勞動部", "職場性騷擾", "性別平等工作"],
        "education_department": ["教育處", "學校", "校園", "性平教育"],
        "one_stop_service": ["一站式服務"],
        "legal_aid": ["法律扶助", "法律服務"],
        "psychological_support": ["心理支持", "心理輔導", "諮詢協談", "心理諮商"],
        "social_welfare": ["社會扶助", "社會福利", "急難救助", "特殊境遇"],
        "medical_support": ["醫療補助", "醫療"],
        "digital_image_center": ["性影像處理中心", "私密影像", "性影像"],
        "online_reporting": ["通報", "申訴連結", "https://"],
    }
    return [
        name for name, keywords in rules.items() if any(keyword in text for keyword in keywords)
    ]


def _parse_simple_frontmatter(text: str) -> tuple[dict[str, str], str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')
    return metadata, text[end + 5 :]


def _first_markdown_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return fallback


def build_document_documents(path: Path) -> list[IngestDocument]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_simple_frontmatter(text)
    title = frontmatter.get("source") or _first_markdown_heading(body, path.stem)
    document_type = frontmatter.get("type", "law")
    jurisdiction = frontmatter.get("jurisdiction", "台灣")
    content = normalize_ws(body)
    chunks = split_text(content)

    documents: list[IngestDocument] = []
    for chunk_index, chunk in enumerate(chunks):
        chunk_content = (
            chunk
            if chunk.startswith(title) or chunk.startswith(f"# {title}")
            else normalize_ws(f"{title}\n{chunk}")
        )
        documents.append(
            IngestDocument(
                doc_id=stable_id("document", path.stem, str(chunk_index)),
                collection_name=settings.rag_collection_name,
                content=chunk_content,
                metadata={
                    "data_type": "document",
                    "source": title,
                    "source_file": str(path),
                    "document_type": document_type,
                    "jurisdiction": jurisdiction,
                    "harassment_types": infer_harassment_types(chunk_content),
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                },
            )
        )
    return documents


def build_judgment_documents(path: Path) -> list[IngestDocument]:
    if path.suffix.lower() in {".md", ".markdown"}:
        return build_judgment_markdown_documents(path)
    return build_judgment_csv_documents(path)


def build_judgment_csv_documents(path: Path) -> list[IngestDocument]:
    documents: list[IngestDocument] = []
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required_fields = {"id", "裁判字號", "裁判日期", "裁判案由", "裁判書內容"}
        missing = required_fields - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} missing required fields: {sorted(missing)}")
        for row in reader:
            source_id = row.get("id", "").strip()
            case_number = row.get("裁判字號", "").strip()
            judgment_date = row.get("裁判日期", "").strip()
            cause = row.get("裁判案由", "").strip()
            body = row.get("裁判書內容", "").strip()
            if not body:
                continue
            header = "\n".join(
                [
                    f"裁判字號：{case_number}",
                    f"裁判日期：{judgment_date}",
                    f"裁判案由：{cause}",
                ]
            )
            content = normalize_ws(
                "\n".join(
                    [
                        header,
                        "",
                        body,
                    ]
                )
            )
            chunks = split_text(content)
            for chunk_index, chunk in enumerate(chunks):
                chunk_content = (
                    chunk if "裁判字號：" in chunk else normalize_ws(f"{header}\n{chunk}")
                )
                documents.append(
                    IngestDocument(
                        doc_id=stable_id("judgment", source_id or case_number, str(chunk_index)),
                        collection_name=settings.rag_judgment_collection_name,
                        content=chunk_content,
                        metadata={
                            "data_type": "judgment",
                            "source": case_number or path.stem,
                            "source_file": str(path),
                            "source_row_id": source_id,
                            "case_number": case_number,
                            "judgment_date": judgment_date,
                            "cause": cause,
                            "jurisdiction": "屏東",
                            "harassment_types": infer_harassment_types(f"{cause}\n{body}"),
                            "chunk_index": chunk_index,
                            "chunk_count": len(chunks),
                        },
                    )
                )
    return documents


def build_judgment_markdown_documents(path: Path) -> list[IngestDocument]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_simple_frontmatter(text)
    source_id = frontmatter.get("id", "").strip()
    case_number = frontmatter.get("case_number") or _first_markdown_heading(body, path.stem)
    judgment_date = frontmatter.get("judgment_date", "").strip()
    cause = frontmatter.get("cause", "").strip()
    jurisdiction = frontmatter.get("jurisdiction", "屏東").strip()
    content = normalize_ws(body)
    if not content:
        return []

    header = "\n".join(
        part
        for part in [
            f"裁判字號：{case_number}" if case_number else "",
            f"裁判日期：{judgment_date}" if judgment_date else "",
            f"裁判案由：{cause}" if cause else "",
        ]
        if part
    )
    chunks = split_text(content)

    documents: list[IngestDocument] = []
    for chunk_index, chunk in enumerate(chunks):
        has_case_context = "裁判字號：" in chunk or (
            bool(case_number) and chunk.startswith(f"# {case_number}")
        )
        chunk_content = chunk if has_case_context else normalize_ws(f"{header}\n{chunk}")
        documents.append(
            IngestDocument(
                doc_id=stable_id(
                    "judgment", source_id or case_number or path.stem, str(chunk_index)
                ),
                collection_name=settings.rag_judgment_collection_name,
                content=chunk_content,
                metadata={
                    "data_type": "judgment",
                    "source": case_number or path.stem,
                    "source_file": str(path),
                    "source_row_id": source_id,
                    "case_number": case_number,
                    "judgment_date": judgment_date,
                    "cause": cause,
                    "jurisdiction": jurisdiction,
                    "harassment_types": infer_harassment_types(f"{cause}\n{content}"),
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunks),
                },
            )
        )
    return documents


def iter_markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "未分類救濟資源"
    current_lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return [(title, body) for title, body in sections if body.strip()]


def build_remedy_documents(path: Path) -> list[IngestDocument]:
    text = path.read_text(encoding="utf-8")
    documents: list[IngestDocument] = []
    for section_index, (title, body) in enumerate(iter_markdown_sections(text)):
        section_text = normalize_ws(f"{title}\n{body}")
        chunks = split_text(section_text)
        harassment_types = infer_harassment_types(section_text)
        remedy_channels = infer_remedy_channels(section_text)
        for chunk_index, chunk in enumerate(chunks):
            chunk_content = chunk if chunk.startswith(title) else normalize_ws(f"{title}\n{chunk}")
            documents.append(
                IngestDocument(
                    doc_id=stable_id("remedy", path.stem, title, str(chunk_index)),
                    collection_name=settings.rag_remedy_collection_name,
                    content=chunk_content,
                    metadata={
                        "data_type": "remedy",
                        "source": title,
                        "source_file": str(path),
                        "section_title": title,
                        "section_index": section_index,
                        "jurisdiction": "屏東",
                        "harassment_types": harassment_types,
                        "remedy_channels": remedy_channels,
                        "chunk_index": chunk_index,
                        "chunk_count": len(chunks),
                    },
                )
            )
    return documents


def collect_markdown_documents(data_dir: Path, builder: DocumentBuilder) -> list[IngestDocument]:
    documents: list[IngestDocument] = []
    for path in sorted(data_dir.glob("*.md")):
        documents.extend(builder(path))
    return documents


def collect_csv_documents(data_dir: Path, builder: DocumentBuilder) -> list[IngestDocument]:
    documents: list[IngestDocument] = []
    for path in sorted(data_dir.glob("*.csv")):
        documents.extend(builder(path))
    return documents


def collect_judgment_documents(data_dir: Path) -> list[IngestDocument]:
    documents: list[IngestDocument] = []
    for path in sorted([*data_dir.glob("*.md"), *data_dir.glob("*.csv")]):
        documents.extend(build_judgment_documents(path))
    return documents


def init_firestore():
    cred_path = str(settings.firebase_admin_credential_path)
    if os.path.exists(cred_path):
        initialize_app(credentials.Certificate(cred_path))
    else:
        initialize_app()
    return firestore.client()


async def embed_with_retries(
    embedding_client: EmbeddingClient,
    content: str,
    retry_count: int,
    retry_wait_seconds: float,
) -> list[float]:
    last_error: Exception | None = None
    for attempt in range(1, retry_count + 1):
        try:
            return await embedding_client.embed(content, mode="passage")
        except Exception as exc:
            last_error = exc
            if attempt >= retry_count:
                break
            print(
                f"Embedding failed on attempt {attempt}/{retry_count}; retrying in {retry_wait_seconds}s: {exc}",
                flush=True,
            )
            time.sleep(retry_wait_seconds)
    raise RuntimeError(
        f"Embedding failed after {retry_count} attempts: {last_error}"
    ) from last_error


async def upload_documents(
    documents: list[IngestDocument],
    retry_count: int,
    retry_wait_seconds: float,
    offset: int = 0,
) -> None:
    db = init_firestore()
    embedding_client = EmbeddingClient()
    for index, document in enumerate(documents[offset:], start=offset + 1):
        embedding = await embed_with_retries(
            embedding_client,
            document.content,
            retry_count=retry_count,
            retry_wait_seconds=retry_wait_seconds,
        )
        db.collection(document.collection_name).document(document.doc_id).set(
            {
                "content": document.content,
                "metadata": document.metadata,
                "embedding": Vector(embedding),
            },
            merge=True,
        )
        print(
            f"[{index}/{len(documents)}] uploaded {document.collection_name}/{document.doc_id}",
            flush=True,
        )


def print_summary(documents: list[IngestDocument]) -> None:
    by_collection: dict[str, int] = {}
    for document in documents:
        by_collection[document.collection_name] = by_collection.get(document.collection_name, 0) + 1
    print("準備匯入文件數：", len(documents), flush=True)
    for collection, count in sorted(by_collection.items()):
        print(f"- {collection}: {count}", flush=True)
    for document in documents[:5]:
        print(
            {
                "doc_id": document.doc_id,
                "collection": document.collection_name,
                "metadata": document.metadata,
                "preview": document.content[:120],
            },
            flush=True,
        )


def add_common_ingest_args(parser: argparse.ArgumentParser, default_data_dir: Path) -> None:
    parser.add_argument("--data-dir", default=default_data_dir, type=Path)
    parser.add_argument("--limit", type=int, default=None, help="Limit documents for testing.")
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many prepared documents before uploading. Useful for resuming.",
    )
    parser.add_argument("--retry-count", type=int, default=3)
    parser.add_argument("--retry-wait-seconds", type=float, default=3.0)
    parser.add_argument("--upload", action="store_true", help="Actually upload to Firestore.")


async def run_ingestion(documents: list[IngestDocument], args: argparse.Namespace) -> None:
    if args.limit is not None:
        documents = documents[: args.limit]
    print_summary(documents)
    if not args.upload:
        print("Dry run only. Add --upload to embed and write documents to Firestore.")
        return
    if args.offset:
        print(f"Resuming upload from document {args.offset + 1}/{len(documents)}.", flush=True)
    await upload_documents(
        documents,
        retry_count=args.retry_count,
        retry_wait_seconds=args.retry_wait_seconds,
        offset=args.offset,
    )


def run_async(coro) -> None:
    asyncio.run(coro)
