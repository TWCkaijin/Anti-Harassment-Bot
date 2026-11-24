"""Reviewed legacy-source catalog plans; never infer an original from a filename."""

from pathlib import PurePath
from uuid import uuid4

from google.cloud.firestore_v1.base_query import FieldFilter

from rag_core.firestore_clients import collection_for_kind

from .parsing import CorpusError, canonical, digest
from .service import ASSETS, SOURCES, _read, now


def legacy_rows(service, kind, source_file):
    query = service.business.collection(collection_for_kind(kind)).where(
        filter=FieldFilter("metadata.source_file", "==", source_file)
    )
    return list(query.stream())


def prepare_legacy_catalog(service, kind, source_file, title=None):
    if not isinstance(source_file, str) or not source_file or len(source_file) > 2000:
        raise CorpusError("需要明確的 metadata.source_file 值")
    snapshots = legacy_rows(service, kind, source_file)
    if not snapshots:
        raise CorpusError("找不到此來源的舊記錄", "not_found", 404)
    groups, manifest = {}, []
    for snapshot in snapshots:
        data = snapshot.to_dict()
        meta = data.get("metadata") or {}
        if data.get("source_id") or meta.get("source_id"):
            raise CorpusError("來源已有 catalog，不能重新猜測歸屬", "already_cataloged", 409)
        count, index = meta.get("chunk_count"), meta.get("chunk_index")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < count
        ):
            raise CorpusError(
                "舊資料缺少有效 chunk_count／chunk_index，保留唯讀瀏覽", "unverified_source", 409
            )
        group = tuple(
            str(meta.get(key, ""))
            for key in ("source_row_id", "case_number", "section_title", "source")
        )
        groups.setdefault(group, []).append((index, count))
        manifest.append(
            {
                "id": snapshot.id,
                "content_hash": digest({"content": data.get("content", ""), "metadata": meta}),
            }
        )
    for chunks in groups.values():
        expected = chunks[0][1]
        if any(count != expected for _, count in chunks) or sorted(
            index for index, _ in chunks
        ) != list(range(expected)):
            raise CorpusError(
                "舊資料 chunk 清單不完整或有重複；禁止猜測完整來源", "unverified_source", 409
            )
    manifest.sort(key=lambda row: row["id"])
    result = {
        "schema_version": 1,
        "binding": service.binding(),
        "kind": kind,
        "source_file": source_file,
        "title": title or PurePath(source_file).name,
        "manifest": manifest,
        "chunk_count": len(manifest),
        "original_available": False,
        "scope": "explicit_metadata_source_file_snapshot",
    }
    result["plan_hash"] = digest(result)
    return result


def apply_legacy_catalog(service, plan, expected_plan_hash):
    if (
        digest({key: value for key, value in plan.items() if key != "plan_hash"})
        != plan.get("plan_hash")
        or plan.get("plan_hash") != expected_plan_hash
    ):
        raise CorpusError("Legacy catalog 計劃雜湊不符", "plan_changed", 409)
    if plan["binding"] != service.binding():
        raise CorpusError("Legacy catalog 環境不符", "binding_changed", 409)
    fresh = prepare_legacy_catalog(service, plan["kind"], plan["source_file"], plan["title"])
    if fresh["plan_hash"] != expected_plan_hash:
        raise CorpusError("來源在計劃後變更，請重新檢查", "source_conflict", 409)
    # A reviewed manifest is immutable, even when it exceeds a single Firestore
    # document. It identifies a snapshot, not recovered original file bytes.
    source_id = "legacy_" + plan["plan_hash"][:32]
    source_ref = service.business.collection(SOURCES).document(source_id)
    existing = _read(source_ref)
    if existing:
        return existing
    assets_ref = service.private.collection(ASSETS).document(source_id)
    assets = _read(assets_ref) or {}
    if assets.get("manifest") and service.store.read_bytes(assets["manifest"]) == canonical(
        plan["manifest"]
    ):
        manifest_ref = assets["manifest"]
    else:
        manifest_ref = service.store.put_bytes(
            f"corpus/legacy/{source_id}/{uuid4().hex}-manifest.json",
            canonical(plan["manifest"]),
            "application/json",
        )
        assets_ref.set({"manifest": manifest_ref})
    identity_hash = digest({"kind": plan["kind"], "title": plan["title"].casefold()})
    identity_ref = service.business.collection("corpus_source_keys").document(identity_hash)
    content_hash = digest({"legacy_manifest": plan["manifest"], "kind": plan["kind"]})
    content_ref = service.business.collection("corpus_content_keys").document(content_hash)

    def publish(transaction):
        source = _read(source_ref, transaction)
        identity = _read(identity_ref, transaction)
        if source:
            return source
        if identity and identity.get("status") != "deleted":
            raise CorpusError("來源名稱已有 catalog", "source_conflict", 409)
        source = {
            "source_id": source_id,
            "title": plan["title"],
            "filename": PurePath(plan["source_file"]).name,
            "kind": plan["kind"],
            "status": "active",
            "revision": 1,
            "chunk_count": plan["chunk_count"],
            "manifest_ref": True,
            "manifest_hash": digest(plan["manifest"]),
            "identity_hash": identity_hash,
            "content_hash": content_hash,
            "original_available": False,
            "legacy": True,
            "legacy_source_file": plan["source_file"],
            "created_at": now(),
        }
        transaction.create(source_ref, source)
        key = {"source_id": source_id, "status": "active", "content_hash": content_hash}
        transaction.set(identity_ref, key)
        transaction.set(content_ref, key)
        return source

    return service.transaction(service.business, publish)
