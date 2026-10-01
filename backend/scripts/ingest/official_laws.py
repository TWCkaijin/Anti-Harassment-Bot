"""Offline law maintenance and explicitly selected, reversible Firestore imports.

All commands except `fetch`, `snapshot`, `apply`, and `rollback` are offline.
`fetch` downloads only public MOJ data. No cloud mutation is the default.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from backend.scripts.common.legal_corpus import (
    OFFICIAL_URL,
    apply_current_supplements,
    audit_legacy,
    build_corpus,
    digest,
    plan_import,
    read_bulk,
)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def read_rollback_journal(path) -> tuple[list[dict], bool]:
    """Recover durable records after an interrupted final journal append.

    A Firestore write happens only after its complete JSON line and newline have
    been flushed and fsynced. An incomplete final line therefore cannot describe
    an already-started write; earlier complete records remain usable. Corruption
    anywhere else is rejected instead of silently discarding backup data.
    """
    raw = Path(path).read_bytes()
    lines = raw.split(b"\n")
    records = []
    ignored_incomplete_tail = False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if index == len(lines) - 1 and not raw.endswith(b"\n") and records:
                ignored_incomplete_tail = True
                break
            raise ValueError("Corrupt rollback journal; no rollback was attempted") from exc
        if not isinstance(record, dict):
            raise ValueError("Rollback journal records must be objects")
        records.append(record)
    if not records or records[0].get("kind") != "header":
        raise ValueError("Rollback journal has no complete header")
    return records, ignored_incomplete_tail


def write_json(path, data, overwrite=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if overwrite else "x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def encode_firestore(value):
    from google.api_core.datetime_helpers import DatetimeWithNanoseconds
    from google.cloud.firestore_v1.vector import Vector

    if isinstance(value, Vector):
        return {"$firestore_vector": list(value)}
    if isinstance(value, DatetimeWithNanoseconds):
        return {"$firestore_timestamp": value.rfc3339()}
    if isinstance(value, datetime):
        return {"$firestore_timestamp": value.isoformat()}
    if isinstance(value, dict):
        return {key: encode_firestore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode_firestore(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Unsupported snapshot value type: {type(value).__name__}")


def decode_firestore(value):
    from google.api_core.datetime_helpers import DatetimeWithNanoseconds
    from google.cloud.firestore_v1.vector import Vector

    if isinstance(value, dict):
        if set(value) == {"$firestore_vector"}:
            return Vector(value["$firestore_vector"])
        if set(value) == {"$firestore_timestamp"}:
            stamp = value["$firestore_timestamp"]
            # Firestore's native nanosecond timestamps use RFC3339 UTC. Keep
            # compatibility with older backup files containing ISO offsets.
            if stamp.endswith("Z"):
                return DatetimeWithNanoseconds.from_rfc3339(stamp)
            return datetime.fromisoformat(stamp)
        return {key: decode_firestore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_firestore(item) for item in value]
    return value


def firestore_client(project):
    # Read credentials only when an operator explicitly selects a cloud action.
    import firebase_admin
    from firebase_admin import credentials, firestore

    from backend.app.core.config import get_settings

    app_name = "legal-corpus-maintenance"
    try:
        app = firebase_admin.get_app(app_name)
    except ValueError:
        path = get_settings().firebase_admin_credential_path
        credential = credentials.Certificate(str(path)) if Path(path).exists() else None
        app = firebase_admin.initialize_app(credential, {"projectId": project}, name=app_name)
    client = firestore.client(app)
    if client.project != project:
        raise ValueError("Firestore project does not match the explicitly selected project")
    return client


def verify_plan(plan):
    expected = plan.get("plan_sha256")
    actual = digest({key: value for key, value in plan.items() if key != "plan_sha256"})
    if actual != expected:
        raise ValueError("Import plan digest mismatch; regenerate and review the plan")


async def apply_plan(plan, *, db, embed, backup_path):
    """Journal each prior value before a compare-and-set transaction.

    Partial failures are recoverable. No blind set/delete is used on rollback.
    `db` and `embed` are injected so offline tests never initialize services.
    """
    from google.cloud import firestore
    from google.cloud.firestore_v1.vector import Vector

    verify_plan(plan)
    if db.project != plan["project"]:
        raise ValueError("Plan project mismatch")
    batch_id = str(uuid.uuid4())
    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as journal:

        def append(row):
            journal.write(json.dumps(row, ensure_ascii=False) + "\n")
            journal.flush()
            os.fsync(journal.fileno())

        append(
            {
                "kind": "header",
                "schema_version": 1,
                "batch_id": batch_id,
                "project": db.project,
                "collection": plan["collection"],
                "plan_sha256": plan["plan_sha256"],
            }
        )
        for change in plan["changes"]:
            if change["action"] in {"unchanged", "withhold"}:
                continue
            ref = db.collection(plan["collection"]).document(change["doc_id"])
            snapshot = ref.get()
            before = encode_firestore(snapshot.to_dict()) if snapshot.exists else None
            if (digest(before) if before is not None else None) != change["before_sha256"]:
                raise ValueError("Cloud document changed since the reviewed plan; regenerate it")
            payload = decode_firestore(change["payload"])
            if payload.get("metadata", {}).get("corpus_active"):
                if before and before.get("content") == payload["content"] and "embedding" in before:
                    payload["embedding"] = decode_firestore(before["embedding"])
                else:
                    payload["embedding"] = Vector(await embed(payload["content"]))
            elif before and "embedding" in before:
                payload["embedding"] = decode_firestore(before["embedding"])
            payload["_legal_import"] = {"batch_id": batch_id, "plan_sha256": plan["plan_sha256"]}
            after = encode_firestore(payload)
            append(
                {
                    "kind": "document",
                    "doc_id": change["doc_id"],
                    "before": before,
                    "after_sha256": digest(after),
                }
            )

            @firestore.transactional
            def write(transaction, ref=ref, before=before, payload=payload):
                current = ref.get(transaction=transaction)
                current_data = encode_firestore(current.to_dict()) if current.exists else None
                if current_data != before:
                    raise ValueError("Concurrent change prevented import")
                transaction.set(ref, payload)

            write(db.transaction())
        append({"kind": "complete", "batch_id": batch_id})
    return batch_id


def rollback(journal, db, *, apply=False):
    from google.cloud import firestore

    header = journal[0]
    if header.get("kind") != "header" or header.get("project") != db.project:
        raise ValueError("Rollback journal/project mismatch")
    counts = {"restorable": 0, "already_original": 0, "conflicts": 0}
    for entry in reversed(journal[1:]):
        if entry.get("kind") != "document":
            continue
        ref = db.collection(header["collection"]).document(entry["doc_id"])

        @firestore.transactional
        def restore(transaction, ref=ref, entry=entry):
            snapshot = ref.get(transaction=transaction)
            current = encode_firestore(snapshot.to_dict()) if snapshot.exists else None
            if current == entry["before"]:
                return "already_original"
            if digest(current) != entry["after_sha256"]:
                return "conflicts"
            if apply:
                if entry["before"] is None:
                    transaction.delete(ref)
                else:
                    transaction.set(ref, decode_firestore(entry["before"]))
            return "restorable"

        counts[restore(db.transaction())] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--output", required=True, type=Path)
    build = sub.add_parser("build")
    build.add_argument("--source", required=True, type=Path)
    build.add_argument("--output-dir", required=True, type=Path)
    build.add_argument("--checked-at", default=date.today().isoformat())
    build.add_argument("--overwrite", action="store_true")
    build.add_argument("--current-supplements", type=Path)
    audit = sub.add_parser("audit")
    audit.add_argument("--legacy-dir", type=Path, default=Path("data/documents"))
    audit.add_argument("--corpus", required=True, type=Path)
    audit.add_argument("--output", required=True, type=Path)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--project", required=True)
    snapshot.add_argument("--collection", default="rag_documents")
    snapshot.add_argument("--output", required=True, type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("--corpus", required=True, type=Path)
    plan.add_argument("--snapshot", type=Path)
    plan.add_argument("--project", required=True)
    plan.add_argument("--collection", default="rag_documents")
    plan.add_argument("--output", required=True, type=Path)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", required=True, type=Path)
    apply.add_argument("--project", required=True)
    apply.add_argument("--expected-plan-sha256", required=True)
    apply.add_argument("--backup", required=True, type=Path)
    back = sub.add_parser("rollback")
    back.add_argument("--backup", required=True, type=Path)
    back.add_argument("--project", required=True)
    back.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "fetch":
        if args.output.exists():
            parser.error("Output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--proto",
                "=https",
                "--max-time",
                "120",
                "--output",
                str(args.output),
                OFFICIAL_URL,
            ],
            check=True,
        )
        _, sha = read_bulk(args.output)
        print(json.dumps({"source_url": OFFICIAL_URL, "sha256": sha}))
    elif args.command == "build":
        bulk, sha = read_bulk(args.source)
        docs, manifest = build_corpus(bulk, checked_at=args.checked_at, source_sha256=sha)
        if args.current_supplements:
            docs, manifest = apply_current_supplements(
                docs, manifest, json.loads(args.current_supplements.read_text())
            )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        corpus = args.output_dir / "articles.jsonl"
        manifest_path = args.output_dir / "manifest.json"
        if not args.overwrite and (corpus.exists() or manifest_path.exists()):
            parser.error("Output exists; review before using --overwrite")
        corpus.write_text("".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs))
        write_json(manifest_path, manifest, overwrite=args.overwrite)
        print(
            json.dumps(
                {
                    key: manifest[key]
                    for key in ("document_count", "active_count", "withheld_articles")
                },
                ensure_ascii=False,
            )
        )
    elif args.command == "audit":
        from backend.scripts.common.firestore_ingest import build_document_documents

        legacy = [
            {"doc_id": doc.doc_id, "content": doc.content, "metadata": doc.metadata}
            for path in sorted(args.legacy_dir.glob("*.md"))
            for doc in build_document_documents(path)
        ]
        report = audit_legacy(legacy, read_jsonl(args.corpus))
        write_json(args.output, report)
        print(json.dumps({"legacy_count": report["legacy_count"], "output": str(args.output)}))
    elif args.command == "snapshot":
        db = firestore_client(args.project)
        rows = [
            {"doc_id": doc.id, "data": encode_firestore(doc.to_dict())}
            for doc in db.collection(args.collection).stream()
        ]
        write_json(
            args.output, {"project": db.project, "collection": args.collection, "documents": rows}
        )
        os.chmod(args.output, 0o600)
        print(json.dumps({"document_count": len(rows)}))
    elif args.command == "plan":
        old = json.loads(args.snapshot.read_text()) if args.snapshot else {}
        if old and (old["project"] != args.project or old["collection"] != args.collection):
            parser.error("Snapshot project/collection mismatch")
        result = plan_import(
            read_jsonl(args.corpus), old.get("documents", []), args.collection, args.project
        )
        result["snapshot_status"] = (
            "cloud_snapshot" if args.snapshot else "no_cloud_snapshot_creates_only_preview"
        )
        result["plan_sha256"] = digest({k: v for k, v in result.items() if k != "plan_sha256"})
        write_json(args.output, result)
        print(json.dumps({"counts": result["counts"], "plan_sha256": result["plan_sha256"]}))
    elif args.command == "apply":
        result = json.loads(args.plan.read_text())
        verify_plan(result)
        if result["project"] != args.project or result["plan_sha256"] != args.expected_plan_sha256:
            parser.error("Explicit project or plan hash mismatch")
        if result.get("snapshot_status") != "cloud_snapshot":
            parser.error("Apply requires a reviewed plan made from an explicit cloud snapshot")
        from backend.app.rag.embeddings import EmbeddingClient

        client = EmbeddingClient()

        async def embed(text):
            return await client.embed(text, mode="passage")

        batch_id = asyncio.run(
            apply_plan(
                result, db=firestore_client(args.project), embed=embed, backup_path=args.backup
            )
        )
        print(json.dumps({"batch_id": batch_id, "backup": str(args.backup)}))
    else:
        records, ignored_tail = read_rollback_journal(args.backup)
        result = rollback(records, firestore_client(args.project), apply=args.apply)
        result["ignored_incomplete_journal_tail"] = ignored_tail
        print(json.dumps(result))


if __name__ == "__main__":
    main()
