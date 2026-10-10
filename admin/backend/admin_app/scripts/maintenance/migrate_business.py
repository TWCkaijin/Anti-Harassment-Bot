"""Reviewed, resumable (default) -> app-dev migration without model calls.

The fixed allowlist excludes admin credentials, sessions, jobs and user data.
Plan files are also exact source/target backups, mode 0600. Apply never mutates
the source database and refuses to overwrite a changed target document.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import firestore, storage
from google.cloud.firestore_v1.vector import Vector

from admin_app.corpus.legacy import apply_legacy_catalog, prepare_legacy_catalog
from admin_app.corpus.parsing import CorpusError
from admin_app.corpus.service import CorpusService
from admin_app.infrastructure import ObjectStore
from admin_app.scripts.common.legal_corpus import digest
from admin_app.scripts.ingest.official_laws import decode_firestore, encode_firestore

PROJECT = "anti-harassment-bot"
SOURCE_DATABASE = "(default)"
TARGET_DATABASE = "app-dev"
CORPUS = {"law": "rag_documents", "judgment": "rag_judgments", "remedy": "rag_remedies"}
ALLOWED_COLLECTIONS = {"runtime_config", "scenario_scripts", *CORPUS.values()}


def _identity(client, database):
    if client.project != PROJECT or client._database != database:
        raise ValueError("Unexpected Firestore project/database")


def _snapshot(snapshot):
    return encode_firestore(snapshot.to_dict()) if snapshot.exists else None


def _sha(payload):
    return digest(payload) if payload is not None else None


def vector_summary(rows):
    dimensions, native, missing, non_native = Counter(), 0, 0, 0
    for row in rows:
        value = row.to_dict().get("embedding")
        if value is None:
            missing += 1
        elif isinstance(value, Vector):
            native += 1
            dimensions[len(list(value))] += 1
        else:
            non_native += 1
    return {
        "native_vectors": native,
        "missing_vectors": missing,
        "non_native_vectors": non_native,
        "dimensions": dict(dimensions),
    }


def clone_plan(source, target):
    _identity(source, SOURCE_DATABASE)
    _identity(target, TARGET_DATABASE)
    config = source.collection("runtime_config").document("app_main").get()
    if not config.exists:
        raise ValueError("Production runtime_config/app_main does not exist")
    collections = {
        "runtime_config": [config],
        "scenario_scripts": list(source.collection("scenario_scripts").stream()),
        **{
            collection: list(source.collection(collection).stream())
            for collection in CORPUS.values()
        },
    }
    entries, summaries = [], {}
    for collection, snapshots in collections.items():
        targets = (
            {"app_dev": target.collection(collection).document("app_dev").get()}
            if collection == "runtime_config"
            else {snapshot.id: snapshot for snapshot in target.collection(collection).stream()}
        )
        summary = {
            "source_count": len(snapshots),
            "target_before_count": sum(snapshot.exists for snapshot in targets.values()),
        }
        if collection in CORPUS.values():
            summary.update(vector_summary(snapshots))
        for snapshot in sorted(snapshots, key=lambda item: item.id):
            target_id = "app_dev" if collection == "runtime_config" else snapshot.id
            payload = _snapshot(snapshot)
            before = _snapshot(targets[target_id]) if target_id in targets else None
            action = (
                "unchanged" if before == payload else "create" if before is None else "conflict"
            )
            entries.append(
                {
                    "collection": collection,
                    "source_id": snapshot.id,
                    "target_id": target_id,
                    "source_payload": payload,
                    "source_sha256": _sha(payload),
                    "target_before": before,
                    "target_before_sha256": _sha(before),
                    "action": action,
                }
            )
        summary["target_extra_count"] = sum(
            snapshot.exists
            and target_id
            not in {"app_dev" if collection == "runtime_config" else item.id for item in snapshots}
            for target_id, snapshot in targets.items()
        )
        summaries[collection] = summary
    result = {
        "schema_version": 1,
        "project": PROJECT,
        "source_database": SOURCE_DATABASE,
        "target_database": TARGET_DATABASE,
        "created_at": datetime.now(UTC).isoformat(),
        "collections": summaries,
        "actions": dict(Counter(entry["action"] for entry in entries)),
        "entries": entries,
    }
    result["plan_sha256"] = digest(result)
    return result


def validate_clone_plan(plan):
    if digest({key: value for key, value in plan.items() if key != "plan_sha256"}) != plan.get(
        "plan_sha256"
    ):
        raise ValueError("Migration plan hash mismatch")
    if (plan.get("project"), plan.get("source_database"), plan.get("target_database")) != (
        PROJECT,
        SOURCE_DATABASE,
        TARGET_DATABASE,
    ):
        raise ValueError("Migration is restricted to the reviewed database pair")
    for entry in plan["entries"]:
        if entry["collection"] not in ALLOWED_COLLECTIONS:
            raise ValueError("Unexpected migration collection")
        if entry["collection"] == "runtime_config" and (entry["source_id"], entry["target_id"]) != (
            "app_main",
            "app_dev",
        ):
            raise ValueError("Unexpected runtime document mapping")
        if entry["action"] == "conflict":
            raise ValueError("Target conflicts require a separately reviewed resolution")


def apply_clone(source, target, plan, transaction_runner=None, progress=None):
    _identity(source, SOURCE_DATABASE)
    _identity(target, TARGET_DATABASE)
    validate_clone_plan(plan)
    run = transaction_runner or (
        lambda db, callback: firestore.transactional(callback)(db.transaction())
    )
    counts = Counter()
    for number, entry in enumerate(plan["entries"], 1):
        source_ref = source.collection(entry["collection"]).document(entry["source_id"])
        if _sha(_snapshot(source_ref.get())) != entry["source_sha256"]:
            raise ValueError("Source changed after the reviewed snapshot; replan")
        target_ref = target.collection(entry["collection"]).document(entry["target_id"])

        def write(transaction, entry=entry, target_ref=target_ref):
            current = _snapshot(target_ref.get(transaction=transaction))
            if current == entry["source_payload"]:
                return "unchanged"
            if _sha(current) != entry["target_before_sha256"]:
                raise ValueError("Concurrent target change prevented migration")
            # The V1 migration does not overwrite differing pre-existing target
            # data, even if a caller tampers with an action label in the plan.
            if current is not None:
                raise ValueError("Refusing to replace an existing target document")
            transaction.create(target_ref, decode_firestore(entry["source_payload"]))
            return "created"

        counts[run(target, write)] += 1
        if progress and (number % 100 == 0 or number == len(plan["entries"])):
            progress({"processed": number, "total": len(plan["entries"]), **counts})
    return dict(counts)


def verify_clone(source, target, plan):
    _identity(source, SOURCE_DATABASE)
    _identity(target, TARGET_DATABASE)
    validate_clone_plan(plan)
    mismatches, changed_source, checked, native = [], [], 0, Counter()
    by_collection = {}
    for collection in ALLOWED_COLLECTIONS:
        by_collection[collection] = {
            snapshot.id: snapshot for snapshot in target.collection(collection).stream()
        }
    for entry in plan["entries"]:
        snapshot = by_collection[entry["collection"]].get(entry["target_id"])
        if snapshot is None or _sha(_snapshot(snapshot)) != entry["source_sha256"]:
            mismatches.append({"collection": entry["collection"], "id": entry["target_id"]})
        elif isinstance(snapshot.to_dict().get("embedding"), Vector):
            native[len(list(snapshot.to_dict()["embedding"]))] += 1
        source_snapshot = source.collection(entry["collection"]).document(entry["source_id"]).get()
        if _sha(_snapshot(source_snapshot)) != entry["source_sha256"]:
            changed_source.append({"collection": entry["collection"], "id": entry["source_id"]})
        checked += 1
    return {
        "checked": checked,
        "target_mismatch_count": len(mismatches),
        "source_changed_count": len(changed_source),
        "native_vector_dimensions": dict(native),
        "target_mismatches": mismatches,
        "changed_source": changed_source,
        "verified": not mismatches and not changed_source,
    }


def private_write(path, value):
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def catalog_plan(service):
    plans, skipped, counts = [], [], {}
    for kind, collection in CORPUS.items():
        snapshots = list(service.business.collection(collection).stream())
        source_files = set()
        missing = 0
        for snapshot in snapshots:
            data = snapshot.to_dict()
            meta = data.get("metadata") or {}
            if data.get("source_id") or meta.get("source_id"):
                continue
            if meta.get("source_file"):
                source_files.add(meta["source_file"])
            else:
                missing += 1
        associated = 0
        for source_file in sorted(source_files):
            try:
                plan = prepare_legacy_catalog(service, kind, source_file)
                plans.append(plan)
                associated += plan["chunk_count"]
            except CorpusError as exc:
                skipped.append(
                    {"kind": kind, "source_file_sha256": digest(source_file), "reason": exc.code}
                )
        counts[kind] = {
            "records": len(snapshots),
            "verified_source_groups": sum(plan["kind"] == kind for plan in plans),
            "associated_records": associated,
            "without_source_file": missing,
        }
    result = {
        "schema_version": 1,
        "binding": service.binding(),
        "counts": counts,
        "skipped": skipped,
        "sources": plans,
    }
    result["plan_sha256"] = digest(result)
    return result


def apply_catalog(service, plan, progress=None):
    if (
        digest({key: value for key, value in plan.items() if key != "plan_sha256"})
        != plan.get("plan_sha256")
        or plan["binding"] != service.binding()
    ):
        raise ValueError("Catalog plan environment or hash mismatch")
    result = []
    for index, source in enumerate(plan["sources"], 1):
        catalog = apply_legacy_catalog(service, source, source["plan_hash"])
        result.append({"source_id": catalog["source_id"], "chunk_count": catalog["chunk_count"]})
        if progress and (index % 25 == 0 or index == len(plan["sources"])):
            progress({"cataloged": index, "total": len(plan["sources"])})
    return {
        "sources": len(result),
        "records": sum(row["chunk_count"] for row in result),
        "original_downloads_created": 0,
        "rag_documents_modified": 0,
    }


def _service(database):
    if database not in {SOURCE_DATABASE, TARGET_DATABASE}:
        raise ValueError("Unsupported database")
    suffix = "prod" if database == SOURCE_DATABASE else "dev"
    private = "admin-private" if suffix == "prod" else "admin-private-dev"
    return CorpusService(
        firestore.Client(project=PROJECT, database=database),
        firestore.Client(project=PROJECT, database=private),
        ObjectStore(storage.Client(project=PROJECT).bucket(f"{PROJECT}-admin-{suffix}")),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("plan")
    prepare.add_argument("--output", type=Path, required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--expected-plan-sha256", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--plan", type=Path, required=True)
    catalogs = commands.add_parser("catalog-plan")
    catalogs.add_argument("--database", choices=[SOURCE_DATABASE, TARGET_DATABASE], required=True)
    catalogs.add_argument("--output", type=Path, required=True)
    catalog_apply = commands.add_parser("catalog-apply")
    catalog_apply.add_argument(
        "--database", choices=[SOURCE_DATABASE, TARGET_DATABASE], required=True
    )
    catalog_apply.add_argument("--plan", type=Path, required=True)
    catalog_apply.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()

    def emit(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)

    if args.command == "catalog-plan":
        plan = catalog_plan(_service(args.database))
        private_write(args.output, plan)
        emit(
            {
                "database": args.database,
                "plan_sha256": plan["plan_sha256"],
                "counts": plan["counts"],
                "skipped_groups": len(plan["skipped"]),
                "backup": str(args.output),
            }
        )
        return
    if args.command == "catalog-apply":
        plan = json.loads(args.plan.read_text())
        if plan.get("plan_sha256") != args.expected_plan_sha256:
            parser.error("Explicit reviewed hash does not match")
        emit(apply_catalog(_service(args.database), plan, progress=emit))
        return
    source = firestore.Client(project=PROJECT, database=SOURCE_DATABASE)
    target = firestore.Client(project=PROJECT, database=TARGET_DATABASE)
    if args.command == "plan":
        plan = clone_plan(source, target)
        private_write(args.output, plan)
        emit(
            {
                "plan_sha256": plan["plan_sha256"],
                "collections": plan["collections"],
                "actions": plan["actions"],
                "backup": str(args.output),
            }
        )
    else:
        plan = json.loads(args.plan.read_text())
        if args.command == "apply":
            if plan.get("plan_sha256") != args.expected_plan_sha256:
                parser.error("Explicit reviewed hash does not match")
            emit(apply_clone(source, target, plan, progress=emit))
        else:
            result = verify_clone(source, target, plan)
            emit(result)
            if not result["verified"]:
                raise SystemExit(1)


if __name__ == "__main__":
    main()
