"""Corpus staging and bounded atomic publication, across explicitly separate databases.

The business operation receipt is authoritative. Private jobs may lag after a
crash and reconcile from that receipt; there is no cross-database transaction.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from google.api_core.exceptions import InvalidArgument, ResourceExhausted
from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector

from rag_core.core.config import get_settings
from rag_core.firestore_clients import collection_for_kind, get_business_db
from rag_core.rag.embeddings import EmbeddingClient

from .parsing import (
    MAX_CHUNKS,
    STRUCTURED,
    CorpusError,
    canonical,
    digest,
    inspect_structured_upload,
    parse_upload,
    validate_column_mapping,
    validate_upload,
    validate_vector,
)

SOURCES = "corpus_sources"
RECEIPTS = "corpus_operations"
IMPORTS = "corpus_imports"
ASSETS = "corpus_source_assets"
MAX_COMMIT_BYTES = 4 * 1024 * 1024
MAX_PARSE_REVISIONS = 20
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,160}$")
SOURCE_PUBLIC_FIELDS = (
    "source_id",
    "title",
    "filename",
    "kind",
    "status",
    "revision",
    "chunk_count",
    "original_available",
    "created_at",
    "deleted_at",
    "legacy",
    "operation_id",
)


def now():
    return datetime.now(UTC)


def identifier(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise CorpusError("識別碼無效", status=400)
    return value


def _transaction(db, callback):
    return firestore.transactional(callback)(db.transaction())


def _read(ref, transaction=None):
    snapshot = ref.get(transaction=transaction) if transaction else ref.get()
    return snapshot.to_dict() if snapshot.exists else None


def _cursor(scope, last_id):
    return (
        base64.urlsafe_b64encode(canonical({"scope": scope, "last": last_id})).decode().rstrip("=")
    )


def _page(collection, scope, limit, cursor, fields=None):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise CorpusError("limit 必須介於 1 與 100", status=400)
    query = collection.order_by("__name__")
    if fields is not None:
        query = query.select(fields)
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if value["scope"] != scope:
                raise ValueError("scope")
            last_id = identifier(value["last"])
        except Exception as exc:
            raise CorpusError("分頁游標無效", status=400) from exc
        query = query.start_after({"__name__": collection.document(last_id)})
    snapshots = list(query.limit(limit + 1).stream())
    return snapshots[:limit], _cursor(scope, snapshots[limit - 1].id) if len(
        snapshots
    ) > limit else None


def record(snapshot, kind):
    data = snapshot.to_dict()
    vector = data.get("embedding")
    dimensions = data.get("embedding_dimensions")
    if dimensions is None and vector is not None:
        try:
            dimensions = (
                len(vector.to_map_value()["value"])
                if hasattr(vector, "to_map_value")
                else len(vector)
            )
        except (TypeError, KeyError):
            dimensions = len(getattr(vector, "_value", [])) or None
    return {
        "id": snapshot.id,
        "kind": kind,
        "content": data.get("content", ""),
        "metadata": data.get("metadata", {}),
        "source_id": data.get("source_id") or data.get("metadata", {}).get("source_id"),
        "embedding": {"dimensions": dimensions, "model": data.get("embedding_model")},
    }


def source_public(data):
    return {key: data.get(key) for key in SOURCE_PUBLIC_FIELDS}


def import_summary(data):
    return {
        key: data.get(key)
        for key in (
            "import_id",
            "source_id",
            "title",
            "filename",
            "kind",
            "status",
            "parse_revision",
            "parse_job_id",
            "job_id",
            "expires_at",
            "created_at",
            "chunk_count",
            "plan_hash",
            "duplicate_source_id",
            "error",
        )
    }


class CorpusService:
    def __init__(
        self, business=None, private=None, store=None, transaction_runner=None, embed=None
    ):
        from admin_app.infrastructure import object_store, private_db

        self.business = business if business is not None else get_business_db()
        self.private = private if private is not None else private_db()
        self.store = store if store is not None else object_store()
        self.transaction = transaction_runner or _transaction
        self.embed = embed or self._embed

    @staticmethod
    def _embed(content):
        return asyncio.run(EmbeddingClient().embed(content, mode="passage"))

    def binding(self):
        settings = get_settings()
        return {
            "project": getattr(self.business, "project", settings.firebase_project_id),
            "database": getattr(self.business, "_database", settings.firestore_database_id),
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "dimensions": settings.embedding_dimensions,
            "prefix_version": "e5-prefix-v1",
            "parser_version": 1,
        }

    def list_records(self, kind, limit=50, cursor=None):
        collection = self.business.collection(collection_for_kind(kind))
        snapshots, next_cursor = _page(collection, kind, limit, cursor)
        return {
            "items": [record(snapshot, kind) for snapshot in snapshots],
            "next_cursor": next_cursor,
        }

    def list_sources(self, limit=50, cursor=None):
        snapshots, next_cursor = _page(
            self.business.collection(SOURCES), SOURCES, limit, cursor, fields=SOURCE_PUBLIC_FIELDS
        )
        return {
            "items": [source_public(snapshot.to_dict()) for snapshot in snapshots],
            "next_cursor": next_cursor,
        }

    def list_imports(self, limit=50, cursor=None):
        snapshots, next_cursor = _page(self.private.collection(IMPORTS), IMPORTS, limit, cursor)
        return {
            "items": [import_summary(snapshot.to_dict()) for snapshot in snapshots],
            "next_cursor": next_cursor,
        }

    def source(self, source_id):
        data = _read(self.business.collection(SOURCES).document(identifier(source_id)))
        if data is None:
            raise CorpusError("找不到來源", "not_found", 404)
        return data

    def source_records(self, source_id, limit=50, cursor=None):
        source = self.source(source_id)
        manifest = self._manifest(source)
        offset = 0
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
                if value["scope"] != source_id or value["revision"] != source["revision"]:
                    raise ValueError("source changed")
                offset = int(value["offset"])
            except Exception as exc:
                raise CorpusError("來源分頁已失效", "stale_cursor", 409) from exc
        if not 1 <= limit <= 100 or not 0 <= offset <= len(manifest):
            raise CorpusError("分頁參數無效", status=400)
        refs = [
            self.business.collection(collection_for_kind(source["kind"])).document(item["id"])
            for item in manifest[offset : offset + limit]
        ]
        found = {snapshot.id: snapshot for snapshot in self.business.get_all(refs)} if refs else {}
        chunks = [
            record(found[ref.id], source["kind"])
            for ref in refs
            if ref.id in found and found[ref.id].exists
        ]
        next_cursor = None
        if offset + limit < len(manifest):
            next_cursor = (
                base64.urlsafe_b64encode(
                    canonical(
                        {
                            "scope": source_id,
                            "revision": source["revision"],
                            "offset": offset + limit,
                        }
                    )
                )
                .decode()
                .rstrip("=")
            )
        return {**source_public(source), "chunks": chunks, "next_cursor": next_cursor}

    def download(self, source_id):
        source = self.source(source_id)
        if source["status"] != "active":
            raise CorpusError("來源已停止提供下載", "source_unavailable", 409)
        assets = _read(self.private.collection(ASSETS).document(source_id)) or {}
        if not assets.get("original"):
            raise CorpusError("舊資料沒有保存可驗證的原始檔", "original_unavailable", 404)
        return self.store.read_bytes(assets["original"]), source["filename"]

    def queue_preview(self, filename, raw, kind, title=None, metadata=None, column_mapping=None):
        """Persist the bounded upload, then queue all document-content parsing."""
        from admin_app.config import get_admin_settings

        upload = validate_upload(filename, raw, kind, title, metadata)
        if column_mapping is not None:
            column_mapping = validate_column_mapping(column_mapping)
            if upload["suffix"] not in STRUCTURED:
                raise CorpusError("此檔案不支援欄位對應", "mapping_not_supported")
        import_id = uuid.uuid4().hex
        original = self.store.put_bytes(
            f"corpus/imports/{import_id}/original", raw, "application/octet-stream"
        )
        data = {
            "import_id": import_id,
            "source_id": f"src_{import_id}",
            "environment": get_admin_settings().environment,
            "filename": upload["filename"],
            "title": upload["title"],
            "kind": kind,
            "base_metadata": upload["metadata"],
            "binding": self.binding(),
            "original": original,
            "preview": None,
            "draft_assets": [],
            "status": "parse_queued",
            "parse_revision": 1,
            "column_mapping": column_mapping,
            "plan_hash": None,
            "created_at": now(),
            "expires_at": now() + timedelta(hours=24),
            "available_fields": [],
            "suggested_mapping": {},
            "sample_rows": [],
        }
        try:
            self.private.collection(IMPORTS).document(import_id).create(data)
        except Exception:
            self.store.delete(original)
            raise
        return self._submit_parse(data)

    def _submit_parse(self, data):
        from admin_app.jobs import public_job, submit_job

        job = submit_job(
            "corpus_parse",
            {"import_id": data["import_id"], "parse_revision": data["parse_revision"]},
            snapshot=data["binding"],
            idempotency_key=f"corpus-parse:{data['import_id']}:{data['parse_revision']}",
        )
        ref = self.private.collection(IMPORTS).document(data["import_id"])

        def link(transaction):
            current = _read(ref, transaction)
            if (
                current
                and current.get("parse_revision") == data["parse_revision"]
                and current.get("status") not in {"expired", "expired_cleanup"}
            ):
                transaction.update(ref, {"parse_job_id": job["job_id"]})

        self.transaction(self.private, link)
        return {**import_summary(self._import(data["import_id"])), "job": public_job(job)}

    def resume_parse(self, import_id):
        from admin_app.jobs import get_job, public_job

        data = self._import(import_id)
        if data.get("status") not in {"parse_queued", "parsing", "parse_failed"} or not data.get(
            "parse_revision"
        ):
            raise CorpusError("目前匯入不需要重新排入解析", "parse_not_pending", 409)
        if data.get("parse_job_id"):
            previous = get_job(data["parse_job_id"])
            if previous["status"] in {"queued", "running"}:
                return {**import_summary(data), "job": public_job(previous)}
            if data["expires_at"] <= now() or data["parse_revision"] >= MAX_PARSE_REVISIONS:
                raise CorpusError("解析草稿已過期或達重試上限，請重新上傳", "preview_expired", 410)
            ref = self.private.collection(IMPORTS).document(import_id)

            def restart(transaction):
                current = _read(ref, transaction)
                if (
                    current.get("parse_revision") != data["parse_revision"]
                    or current.get("job_id")
                    or current.get("confirm_reserved")
                ):
                    raise CorpusError("匯入版本已變更", "parse_revision_conflict", 409)
                changes = {
                    "status": "parse_queued",
                    "parse_revision": data["parse_revision"] + 1,
                    "parse_job_id": None,
                    "error": None,
                }
                transaction.update(ref, changes)
                return {**current, **changes}

            data = self.transaction(self.private, restart)
        return self._submit_parse(data)

    def map_import(self, import_id, column_mapping, parse_revision):
        ref = self.private.collection(IMPORTS).document(identifier(import_id))
        if isinstance(parse_revision, bool) or not isinstance(parse_revision, int):
            raise CorpusError("請提供目前解析版本", "parse_revision_conflict", 409)

        def update(transaction):
            data = _read(ref, transaction)
            if not data:
                raise CorpusError("找不到匯入預覽", "not_found", 404)
            if (
                data.get("job_id")
                or data.get("confirm_reserved")
                or data.get("status")
                not in {
                    "mapping_required",
                    "preview",
                    "parse_failed",
                }
            ):
                raise CorpusError("目前狀態不能變更欄位對應", "mapping_conflict", 409)
            if data.get("parse_revision") != parse_revision:
                raise CorpusError("解析版本已變更，請重新整理", "parse_revision_conflict", 409)
            if data["expires_at"] <= now():
                raise CorpusError("預覽已過期，請重新上傳", "preview_expired", 410)
            if parse_revision >= MAX_PARSE_REVISIONS:
                raise CorpusError("欄位對應調整已達上限，請重新上傳", "mapping_limit", 409)
            mapping = validate_column_mapping(column_mapping, data.get("available_fields", []))
            changes = {
                "column_mapping": mapping,
                "parse_revision": parse_revision + 1,
                "status": "parse_queued",
                "parse_job_id": None,
                "plan_hash": None,
                "preview": None,
                "chunk_count": None,
                "error": None,
            }
            transaction.update(ref, changes)
            return {**data, **changes}

        return self._submit_parse(self.transaction(self.private, update))

    def _parse_result(self, data):
        return {
            key: data.get(key)
            for key in ("import_id", "source_id", "status", "parse_revision", "chunk_count")
        }

    def parse_job(self, job, context):
        data = self._import(job["payload"]["import_id"])
        revision = job["payload"].get("parse_revision")
        if data.get("parse_revision") != revision:
            raise CorpusError("解析版本已被取代", "parse_superseded", 409)
        if data.get("status") in {"mapping_required", "preview"}:
            return self._parse_result(data)
        ref = self.private.collection(IMPORTS).document(data["import_id"])

        def write(changes):
            context.ensure_active()

            def mutate(transaction):
                current = _read(ref, transaction)
                if hasattr(context, "lease_token") and hasattr(context, "job_id"):
                    from admin_app.jobs import JobCancelled, LeaseLost

                    lease = _read(
                        self.private.collection("admin_jobs").document(context.job_id), transaction
                    )
                    if (
                        not lease
                        or lease.get("lease_token") != context.lease_token
                        or lease.get("status") != "running"
                        or lease.get("lease_until") <= now()
                    ):
                        raise LeaseLost()
                    if lease.get("cancel_requested"):
                        raise JobCancelled()
                if (
                    not current
                    or current.get("parse_revision") != revision
                    or current.get("status") not in {"parse_queued", "parsing", "parse_failed"}
                    or current["expires_at"] <= now()
                ):
                    raise CorpusError("解析草稿已過期或被取代", "parse_superseded", 409)
                transaction.update(ref, changes)
                return {**current, **changes}

            return self.transaction(self.private, mutate)

        write({"status": "parsing", "error": None})
        context.progress({"phase": "parsing", "completed": 0, "total": 1})
        try:
            if data["binding"] != self.binding():
                raise CorpusError("環境或 embedding 設定已變更，請重新上傳", "binding_changed", 409)
            raw = self.store.read_bytes(data["original"])
            upload = validate_upload(
                data["filename"], raw, data["kind"], data["title"], data.get("base_metadata")
            )
            inspection = (
                inspect_structured_upload(data["filename"], raw)
                if upload["suffix"] in STRUCTURED
                else {}
            )
            if inspection and data.get("column_mapping") is None:
                current = write({**inspection, "status": "mapping_required"})
                context.progress({"phase": "mapping_required", "completed": 1, "total": 1})
                return self._parse_result(current)
            if inspection:
                write(inspection)
            parsed, duplicate = self._prepare_preview(
                data["filename"],
                raw,
                data["kind"],
                data["title"],
                data.get("base_metadata"),
                data.get("column_mapping"),
            )
            context.ensure_active()
            preview = self.store.put_bytes_idempotent(
                f"corpus/imports/{data['import_id']}/preview-{revision}.json",
                canonical(parsed),
                "application/json",
            )
            assets = [*data.get("draft_assets", []), preview]
            current = write(
                {
                    **inspection,
                    "status": "preview",
                    "preview": preview,
                    "draft_assets": assets,
                    "plan_hash": parsed["plan_hash"],
                    "chunk_count": parsed["chunk_count"],
                    "duplicate_source_id": duplicate.get("source_id")
                    if duplicate and duplicate.get("status") == "active"
                    else None,
                }
            )
            context.progress({"phase": "preview", "completed": 1, "total": 1})
            return self._parse_result(current)
        except Exception as exc:
            from admin_app.jobs import JobCancelled, LeaseLost

            if isinstance(exc, (JobCancelled, LeaseLost)):
                raise
            error = (
                {"code": exc.code, "detail": exc.detail}
                if isinstance(exc, CorpusError)
                else {"code": "parse_failed", "detail": "解析暫時失敗，請重試解析工作"}
            )
            with suppress(CorpusError, JobCancelled, LeaseLost):
                write({"status": "parse_failed", "error": error})
            raise

    def _prepare_preview(self, filename, raw, kind, title=None, metadata=None, column_mapping=None):
        parsed = parse_upload(filename, raw, kind, title, metadata, column_mapping)
        parsed["binding"] = self.binding()
        if (
            len(canonical(parsed)) + parsed["chunk_count"] * parsed["binding"]["dimensions"] * 24
            > MAX_COMMIT_BYTES
        ):
            raise CorpusError("預估發布大小超過原子寫入限制，請拆分文件", "commit_too_large")
        parsed["plan_hash"] = digest(parsed)
        source_key = _read(
            self.business.collection("corpus_source_keys").document(parsed["identity_hash"])
        )
        duplicate = _read(
            self.business.collection("corpus_content_keys").document(parsed["content_hash"])
        )
        if (
            source_key
            and source_key.get("status") != "deleted"
            and source_key["content_hash"] != parsed["content_hash"]
        ):
            raise CorpusError(
                "相同來源已有不同內容；此版本不支援替換，請先刪除完整舊來源", "source_conflict", 409
            )
        return parsed, duplicate

    def preview(self, filename, raw, kind, title=None, metadata=None):
        """Synchronous helper for offline ingestion fixtures; HTTP always queues parsing."""
        parsed, duplicate = self._prepare_preview(filename, raw, kind, title, metadata)
        import_id = uuid.uuid4().hex
        prefix = f"corpus/imports/{import_id}"
        original = self.store.put_bytes(f"{prefix}/original", raw, "application/octet-stream")
        try:
            preview_ref = self.store.put_bytes(
                f"{prefix}/preview.json", canonical(parsed), "application/json"
            )
        except Exception:
            self.store.delete(original)
            raise
        data = {
            "import_id": import_id,
            "source_id": f"src_{import_id}",
            "plan_hash": parsed["plan_hash"],
            "filename": parsed["filename"],
            "title": parsed["title"],
            "kind": kind,
            "chunk_count": parsed["chunk_count"],
            "original": original,
            "preview": preview_ref,
            "status": "preview",
            "created_at": now(),
            "expires_at": now() + timedelta(hours=24),
            "duplicate_source_id": duplicate.get("source_id")
            if duplicate and duplicate.get("status") == "active"
            else None,
        }
        self.private.collection(IMPORTS).document(import_id).create(data)
        return self.import_preview(import_id)

    def _import(self, import_id):
        data = _read(self.private.collection(IMPORTS).document(identifier(import_id)))
        if not data:
            raise CorpusError("找不到匯入預覽", "not_found", 404)
        if data.get("status") in {"expired", "expired_cleanup"}:
            raise CorpusError("未確認的預覽已過期，請重新上傳", "preview_expired", 410)
        return data

    def _plan(self, data):
        parsed = json.loads(self.store.read_bytes(data["preview"]))
        claimed = parsed.pop("plan_hash", None)
        if digest(parsed) != claimed or claimed != data["plan_hash"]:
            raise CorpusError("預覽內容驗證失敗，請重新上傳", "plan_changed", 409)
        parsed["plan_hash"] = claimed
        if parsed["binding"] != self.binding():
            raise CorpusError("環境或 embedding 設定已變更，請重新預覽", "binding_changed", 409)
        return parsed

    def import_preview(self, import_id):
        data = self._import(import_id)
        parsed = (
            self._plan(data)
            if data.get("preview") and data.get("status") != "duplicate_cleanup"
            else {"chunks": [], "warnings": [], "binding": self.binding()}
        )
        return {
            **import_summary(data),
            "available_fields": data.get("available_fields", []),
            "suggested_mapping": data.get("suggested_mapping", {}),
            "column_mapping": data.get("column_mapping"),
            "sample_rows": data.get("sample_rows", []),
            "row_count": data.get("row_count"),
            "chunks": parsed["chunks"],
            "warnings": parsed["warnings"] if data.get("preview") else data.get("warnings", []),
            "embedding": parsed["binding"],
            "limits": {"file_bytes": 5 * 1024 * 1024, "chunks": MAX_CHUNKS, "pdf_pages": 100},
        }

    def confirm(self, import_id, plan_hash):
        from admin_app.jobs import get_job, public_job, submit_job

        data = self._import(import_id)
        if (
            data.get("parse_revision")
            and data.get("status")
            not in {"preview", "succeeded", "queued", "running", "duplicate_cleanup"}
            and not data.get("job_id")
        ):
            raise CorpusError("解析與欄位對應尚未完成", "preview_not_ready", 409)
        if plan_hash != data["plan_hash"]:
            raise CorpusError("確認內容與預覽不符", "plan_changed", 409)
        if data.get("job_id"):
            return public_job(get_job(data["job_id"]))
        if data.get("result", {}).get("duplicate"):
            return self._complete_duplicate(data, data["result"]["source_id"])
        if data["expires_at"] < now() and not data.get("job_id"):
            raise CorpusError("預覽已過期，請重新上傳", "preview_expired", 409)
        plan = self._plan(data)
        duplicate = _read(
            self.business.collection("corpus_content_keys").document(plan["content_hash"])
        )
        if duplicate and duplicate.get("status") == "active":
            if duplicate["source_id"] == data["source_id"]:
                # A worker may commit before the request persists its job link.
                # Replaying that import must preserve its own published assets.
                published = self.source(data["source_id"])
                job = get_job(published["operation_id"])
                self.private.collection(IMPORTS).document(import_id).update(
                    {"job_id": job["job_id"], "status": job["status"]}
                )
                return public_job(job)
            return self._complete_duplicate(data, duplicate["source_id"])

        def reserve(transaction):
            ref = self.private.collection(IMPORTS).document(import_id)
            current = _read(ref, transaction)
            if not current or current.get("plan_hash") != plan_hash:
                raise CorpusError("預覽已變更，請重新整理後確認", "plan_changed", 409)
            if current.get("job_id"):
                return current["job_id"]
            if current.get("status") != "preview":
                raise CorpusError("預覽已變更，請重新整理後確認", "plan_changed", 409)
            transaction.update(ref, {"confirm_reserved": True})
            return None

        existing_job_id = self.transaction(self.private, reserve)
        if existing_job_id:
            return public_job(get_job(existing_job_id))
        job = submit_job(
            "corpus_import",
            {"import_id": import_id, "plan_hash": plan_hash},
            snapshot=plan["binding"],
            idempotency_key=f"corpus-import:{import_id}:{plan_hash}",
        )
        self.private.collection(IMPORTS).document(import_id).update(
            {"status": job["status"], "job_id": job["job_id"]}
        )
        return public_job(job)

    def _receipt(self, operation_id):
        return _read(self.business.collection(RECEIPTS).document(identifier(operation_id)))

    def _complete_duplicate(self, data, source_id):
        if data["source_id"] == source_id:
            raise CorpusError("不可清除目前匯入已發布的來源", "already_published", 409)
        result = {"status": "succeeded", "source_id": source_id, "duplicate": True}
        ref = self.private.collection(IMPORTS).document(data["import_id"])
        ref.update(
            {"status": "duplicate_cleanup", "result": result, "duplicate_source_id": source_id}
        )
        for reference in self._draft_references(data):
            self.store.delete(reference)
        for staged in ref.collection("embeddings").stream():
            staged.reference.delete()
        self.private.collection(ASSETS).document(data["source_id"]).delete()
        ref.update({"status": "succeeded", "original": None, "preview": None, "draft_assets": []})
        return result

    def _draft_references(self, data):
        refs = [data.get("original"), data.get("preview"), *data.get("draft_assets", [])]
        if data.get("import_id") and hasattr(self.store, "lookup_reference"):
            for revision in range(1, int(data.get("parse_revision") or 0) + 1):
                refs.append(
                    self.store.lookup_reference(
                        f"corpus/imports/{data['import_id']}/preview-{revision}.json"
                    )
                )
        unique = {}
        for reference in refs:
            if reference:
                unique[(reference["bucket"], reference["name"], reference["generation"])] = (
                    reference
                )
        return list(unique.values())

    def import_job(self, job, context):
        operation_id = identifier(job["job_id"])
        receipt = self._receipt(operation_id)
        if receipt and receipt.get("status") == "succeeded":
            data = _read(self.private.collection(IMPORTS).document(job["payload"]["import_id"]))
            if data and receipt["result"].get("duplicate"):
                return self._complete_duplicate(data, receipt["result"]["source_id"])
            if data:
                self.private.collection(IMPORTS).document(data["import_id"]).update(
                    {"status": "succeeded"}
                )
            return receipt["result"]
        data = self._import(job["payload"]["import_id"])
        plan = self._plan(data)
        if job["payload"].get("plan_hash") != plan["plan_hash"]:
            raise CorpusError("任務與預覽不符", "plan_changed", 409)
        started, completed = time.monotonic(), 0
        payloads = []
        for index, chunk in enumerate(plan["chunks"]):
            stage_ref = (
                self.private.collection(IMPORTS)
                .document(data["import_id"])
                .collection("embeddings")
                .document(str(index))
            )
            staged = _read(stage_ref)
            if not staged:
                if completed >= 4 or time.monotonic() - started > 45:
                    from admin_app.jobs import ContinueJob

                    context.checkpoint({"completed": index})
                    raise ContinueJob()
                context.external_call_started(f"embedding:{index}")
                returned = self.embed(chunk["content"])
                try:
                    vector = validate_vector(returned, plan["binding"]["dimensions"])
                except CorpusError:
                    # A completed, malformed response is known; it is not an
                    # uncertain paid call and must report the validation error.
                    context.external_call_finished()
                    raise
                stage_ref.create(
                    {"vector": vector, "chunk_hash": digest(chunk), "plan_hash": plan["plan_hash"]}
                )
                context.external_call_finished()
                staged = {
                    "vector": vector,
                    "chunk_hash": digest(chunk),
                    "plan_hash": plan["plan_hash"],
                }
                completed += 1
            if staged["chunk_hash"] != digest(chunk) or staged["plan_hash"] != plan["plan_hash"]:
                raise CorpusError("暫存向量與已確認內容不符", "stage_changed", 409)
            vector = validate_vector(staged["vector"], plan["binding"]["dimensions"])
            payloads.append(
                {
                    "content": chunk["content"],
                    "metadata": {
                        **chunk["metadata"],
                        "source_id": data["source_id"],
                        "import_id": data["import_id"],
                    },
                    "source_id": data["source_id"],
                    "embedding": vector,
                    "embedding_model": plan["binding"]["model"],
                    "embedding_dimensions": len(vector),
                }
            )
            context.progress(
                {"completed": index + 1, "total": len(plan["chunks"]), "phase": "embedding"}
            )
        if len(canonical(payloads)) > MAX_COMMIT_BYTES:
            raise CorpusError("發布內容超過原子寫入限制", "commit_too_large", 409)
        # Assets live only in the private database, and are prepared before the
        # business commit. A failed publication can be retried without losing them.
        self.private.collection(ASSETS).document(data["source_id"]).set(
            {
                "original": data["original"],
                "preview": data["preview"],
                "import_id": data["import_id"],
                "draft_assets": data.get("draft_assets", []),
                "parse_revision": data.get("parse_revision"),
            }
        )
        context.ensure_active()
        result = self._publish(data, plan, payloads, operation_id)
        if result.get("duplicate"):
            return self._complete_duplicate(data, result["source_id"])
        self.private.collection(IMPORTS).document(data["import_id"]).update({"status": "succeeded"})
        return result

    def _publish(self, data, plan, payloads, operation_id):
        source_ref = self.business.collection(SOURCES).document(data["source_id"])
        receipt_ref = self.business.collection(RECEIPTS).document(operation_id)
        identity_ref = self.business.collection("corpus_source_keys").document(
            plan["identity_hash"]
        )
        content_ref = self.business.collection("corpus_content_keys").document(plan["content_hash"])
        collection = self.business.collection(collection_for_kind(plan["kind"]))

        def publish(transaction):
            existing = _read(receipt_ref, transaction)
            identity = _read(identity_ref, transaction)
            duplicate = _read(content_ref, transaction)
            if existing:
                return existing["result"]
            if (
                identity
                and identity.get("status") != "deleted"
                and (
                    identity["content_hash"] != plan["content_hash"]
                    or identity.get("status") != "active"
                )
            ):
                raise CorpusError("來源在確認後發生變更", "source_conflict", 409)
            if duplicate and duplicate.get("status") == "active":
                result = {"source_id": duplicate["source_id"], "duplicate": True}
                transaction.create(
                    receipt_ref,
                    {
                        "status": "succeeded",
                        "kind": "import",
                        "result": result,
                        "completed_at": now(),
                    },
                )
                return result
            if duplicate and duplicate.get("status") not in {"active", "deleted"}:
                raise CorpusError("相同內容正在刪除，請等待完成", "source_conflict", 409)
            manifest = []
            for index, payload in enumerate(payloads):
                chunk_id = f"{data['source_id']}_{index:04d}"
                doc = {**payload, "embedding": Vector(payload["embedding"])}
                transaction.create(collection.document(chunk_id), doc)
                manifest.append(
                    {
                        "id": chunk_id,
                        "content_hash": digest(
                            {"content": payload["content"], "metadata": payload["metadata"]}
                        ),
                    }
                )
            source = {
                "source_id": data["source_id"],
                "title": plan["title"],
                "filename": plan["filename"],
                "kind": plan["kind"],
                "status": "active",
                "revision": 1,
                "chunk_count": len(manifest),
                "manifest": manifest,
                "identity_hash": plan["identity_hash"],
                "content_hash": plan["content_hash"],
                "original_available": True,
                "created_at": now(),
                "operation_id": operation_id,
                "legacy": False,
            }
            transaction.create(source_ref, source)
            key = {
                "source_id": source["source_id"],
                "status": "active",
                "content_hash": plan["content_hash"],
            }
            transaction.set(identity_ref, key)
            transaction.set(content_ref, key)
            result = {
                "source_id": source["source_id"],
                "chunk_count": len(manifest),
                "duplicate": False,
            }
            transaction.create(
                receipt_ref,
                {"status": "succeeded", "kind": "import", "result": result, "completed_at": now()},
            )
            return result

        return self.transaction(self.business, publish)

    def delete_source(self, source_id, revision):
        from admin_app.jobs import public_job, submit_job

        source = self.source(source_id)
        if source["status"] == "deleted":
            return {"status": "succeeded", "source_id": source_id, "already_deleted": True}
        if source["status"] in {"deleting", "purge_pending"}:
            return {"status": source["status"], "job_id": source["operation_id"]}
        if isinstance(revision, bool) or revision != source["revision"]:
            raise CorpusError("來源版本已變更，請重新整理後確認", "revision_conflict", 409)
        if not source.get("manifest") and not source.get("manifest_ref"):
            raise CorpusError("來源尚未完成安全關聯，不能推測刪除範圍", "unverified_source", 409)
        return public_job(
            submit_job(
                "corpus_delete",
                {"source_id": source_id, "revision": revision},
                idempotency_key=f"corpus-delete:{source_id}:{revision}",
            )
        )

    def _manifest(self, source):
        if "manifest" in source:
            return source["manifest"]
        # Large, reviewed legacy manifests are stored privately; never download
        # a caller-supplied object reference.
        assets = _read(self.private.collection(ASSETS).document(source["source_id"])) or {}
        if not assets.get("manifest"):
            raise CorpusError("來源沒有可驗證的完整清單", "unverified_source", 409)
        value = json.loads(self.store.read_bytes(assets["manifest"]))
        if digest(value) != source.get("manifest_hash"):
            raise CorpusError("來源清單已變更", "manifest_changed", 409)
        return value

    def delete_job(self, job, context):
        operation_id = identifier(job["job_id"])
        source_id = identifier(job["payload"]["source_id"])
        source_ref = self.business.collection(SOURCES).document(source_id)
        receipt_ref = self.business.collection(RECEIPTS).document(operation_id)

        def reserve(transaction):
            source = _read(source_ref, transaction)
            receipt = _read(receipt_ref, transaction)
            if receipt:
                return source, receipt
            if (
                not source
                or source["status"] != "active"
                or source["revision"] != job["payload"]["revision"]
            ):
                raise CorpusError("來源版本已变更", "revision_conflict", 409)
            source = {
                **source,
                "status": "deleting",
                "operation_id": operation_id,
                "revision": source["revision"] + 1,
            }
            transaction.set(source_ref, source)
            for collection, key in (
                ("corpus_source_keys", source.get("identity_hash")),
                ("corpus_content_keys", source.get("content_hash")),
            ):
                if key:
                    transaction.update(
                        self.business.collection(collection).document(key), {"status": "deleting"}
                    )
            receipt = {
                "kind": "delete",
                "source_id": source_id,
                "status": "deleting",
                "cursor": 0,
                "created_at": now(),
            }
            transaction.create(receipt_ref, receipt)
            return source, receipt

        context.ensure_active()
        source, receipt = self.transaction(self.business, reserve)
        if receipt["status"] == "succeeded":
            return receipt["result"]
        cursor = receipt["cursor"]
        total = source["chunk_count"]
        manifest = self._manifest(source) if cursor < total else []
        if source.get("legacy_source_file"):
            from .legacy import legacy_rows

            remaining = {item["id"] for item in manifest[cursor:]}
            present = {
                item.id for item in legacy_rows(self, source["kind"], source["legacy_source_file"])
            }
            if present - remaining:
                raise CorpusError("舊來源出現清單外的新記錄，已停止刪除", "source_conflict", 409)
        if cursor < total:
            group = manifest[cursor : cursor + 100]

            def remove(transaction):
                current = _read(source_ref, transaction)
                progress = _read(receipt_ref, transaction)
                if current["operation_id"] != operation_id or current["status"] != "deleting":
                    raise CorpusError("刪除工作與來源不符", "source_conflict", 409)
                if progress["cursor"] != cursor:
                    return progress["cursor"]
                refs = [
                    (
                        self.business.collection(collection_for_kind(source["kind"])).document(
                            item["id"]
                        ),
                        item,
                    )
                    for item in group
                ]
                snapshots = [(ref, item, _read(ref, transaction)) for ref, item in refs]
                for _, item, doc in snapshots:
                    if doc and not source.get("legacy") and doc.get("source_id") != source_id:
                        raise CorpusError("文件來源歸屬已變更", "document_conflict", 409)
                    if (
                        doc
                        and digest(
                            {"content": doc.get("content", ""), "metadata": doc.get("metadata", {})}
                        )
                        != item["content_hash"]
                    ):
                        raise CorpusError(
                            "文件在刪除確認後變更，已停止避免誤刪", "document_conflict", 409
                        )
                for ref, _, _ in snapshots:
                    transaction.delete(ref)
                next_cursor = cursor + len(group)
                state = "purge_pending" if next_cursor == total else "deleting"
                transaction.update(receipt_ref, {"cursor": next_cursor, "status": state})
                transaction.update(source_ref, {"status": state})
                return next_cursor

            context.ensure_active()
            while True:
                try:
                    cursor = self.transaction(self.business, remove)
                    break
                except (InvalidArgument, ResourceExhausted) as exc:
                    size_error = any(
                        word in str(exc).lower() for word in ("size", "too big", "too large")
                    )
                    if not source.get("legacy") or len(group) <= 1 or not size_error:
                        raise
                    # Old documents may have much larger index fanout than new
                    # bounded uploads. A rejected atomic batch changed nothing.
                    group = group[: max(1, len(group) // 2)]
            context.progress({"phase": "deleting", "completed": cursor, "total": total})
            if cursor < total:
                from admin_app.jobs import ContinueJob

                context.checkpoint({"deleted": cursor})
                raise ContinueJob()
        assets_ref = self.private.collection(ASSETS).document(source_id)
        context.ensure_active()
        if source.get("legacy_source_file"):
            from .legacy import legacy_rows

            if legacy_rows(self, source["kind"], source["legacy_source_file"]):
                raise CorpusError("舊來源仍有未刪除記錄，原始檔保留", "source_conflict", 409)
        assets = _read(assets_ref) or {}
        for reference in self._draft_references(assets):
            self.store.delete(reference)
        if assets.get("manifest"):
            self.store.delete(assets["manifest"])
        if assets.get("import_id"):
            import_ref = self.private.collection(IMPORTS).document(assets["import_id"])
            for snapshot in import_ref.collection("embeddings").stream():
                snapshot.reference.delete()
            import_ref.delete()
        assets_ref.delete()

        def finish(transaction):
            current = _read(source_ref, transaction)
            progress = _read(receipt_ref, transaction)
            if progress["status"] == "succeeded":
                return progress["result"]
            if current["operation_id"] != operation_id or current["status"] != "purge_pending":
                raise CorpusError("刪除狀態已變更", "source_conflict", 409)
            result = {"source_id": source_id, "deleted_chunks": total, "original_deleted": True}
            transaction.update(
                source_ref,
                {
                    "status": "deleted",
                    "original_available": False,
                    "deleted_at": now(),
                    "manifest": [],
                },
            )
            for collection, key in (
                ("corpus_source_keys", source.get("identity_hash")),
                ("corpus_content_keys", source.get("content_hash")),
            ):
                if key:
                    transaction.update(
                        self.business.collection(collection).document(key), {"status": "deleted"}
                    )
            transaction.update(
                receipt_ref, {"status": "succeeded", "result": result, "completed_at": now()}
            )
            return result

        context.ensure_active()
        return self.transaction(self.business, finish)
