"""Environment-scoped drafts, immutable revisions and durable evaluation artifacts."""

import asyncio
import hashlib
import json
import re
import uuid
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import UTC, datetime
from time import monotonic

from google.cloud.firestore_v1.base_query import FieldFilter

from admin_app import infrastructure, jobs
from admin_app.rerank_tests.contracts import (
    CaseInput,
    Settings,
    digest,
    retrieval_binding,
    validate_scope,
)
from admin_app.rerank_tests.metrics import evaluate, summarize
from rag_core.core.config import get_settings
from rag_core.core.runtime_config import RuntimeConfig, get_runtime_config
from rag_core.firestore_clients import get_business_db
from rag_core.rag.base import RAGDocument
from rag_core.rag.firestore_vector import FirestoreVectorRAG
from rag_core.rag.legal_lookup import LAW_ALIASES, article_number
from rag_core.rag.rerank import rerank_candidates

DATASETS = "admin_rerank_datasets"
REVISIONS = "admin_rerank_revisions"
RUNS = "admin_rerank_runs"
PREPARATIONS = "admin_rerank_preparations"


def timestamp():
    return datetime.now(UTC).isoformat()


def environment():
    return infrastructure.get_admin_settings().environment


def fail(code, detail, status=422):
    raise jobs.JobFailure(code, detail, http_status=status)


def reference(collection, identifier):
    if not re.fullmatch(r"[a-f0-9]{32}(?:-[0-9]+)?", identifier):
        fail("not_found", "找不到資料。", 404)
    return infrastructure.private_db().collection(collection).document(identifier)


def read(collection, identifier):
    value = reference(collection, identifier).get().to_dict()
    if not value or value.get("environment") != environment():
        fail("not_found", "找不到資料。", 404)
    return value


def store_object(prefix, value):
    return infrastructure.object_store().put_bytes(
        f"rerank-tests/{environment()}/{prefix}/{uuid.uuid4().hex}.json",
        jobs.canonical(value),
        "application/json",
    )


def load_object(ref):
    return json.loads(infrastructure.object_store().read_bytes(ref))


def list_items(collection, cursor=None):
    query = (
        infrastructure.private_db()
        .collection(collection)
        .where(filter=FieldFilter("environment", "==", environment()))
        .order_by("__name__")
    )
    if cursor:
        query = query.start_after({"__name__": reference(collection, cursor)})
    values = list(query.limit(51).stream())
    return {
        "items": [s.to_dict() for s in values[:50]],
        "next_cursor": values[49].id if len(values) > 50 else None,
    }


def dataset(identifier):
    meta = read(DATASETS, identifier)
    return {**meta, "cases": load_object(meta["draft_ref"])["cases"]}


def create_dataset(name):
    identifier = uuid.uuid4().hex
    value = {
        "id": identifier,
        "environment": environment(),
        "name": name,
        "archived": False,
        "revision": 1,
        "published_revision": None,
        "created_at": timestamp(),
        "updated_at": timestamp(),
        "case_count": 0,
        "draft_ref": store_object(f"datasets/{identifier}", {"cases": []}),
    }
    reference(DATASETS, identifier).create(value)
    return dataset(identifier)


def update_dataset(identifier, expected, *, cases=None, name=None, archived=None):
    current = dataset(identifier)
    if cases is not None and len(cases) > 200:
        fail("case_limit", "資料集最多 200 題。")
    changes = {"revision": expected + 1, "updated_at": timestamp()}
    if cases is not None:
        changes.update(
            draft_ref=store_object(f"datasets/{identifier}", {"cases": cases}),
            case_count=len(cases),
        )
    if name is not None:
        changes["name"] = name
    if archived is not None:
        changes["archived"] = archived

    def commit(tx):
        ref = reference(DATASETS, identifier)
        saved = ref.get(transaction=tx).to_dict()
        if not saved or saved.get("environment") != environment():
            fail("not_found", "找不到資料。", 404)
        if saved["revision"] != expected or current["revision"] != expected:
            fail("revision_conflict", "資料已更新，請重新載入後再儲存。", 409)
        tx.update(ref, changes)

    infrastructure.atomic(commit)
    return dataset(identifier)


def collections_config():
    return dict(get_runtime_config().rag_collections)


def document(kind, doc_id, collections=None, manual_unit_id=None):
    collections = collections or collections_config()
    if kind not in {"law", "judgment", "remedy"} or not re.fullmatch(r"[^/]{1,160}", doc_id):
        fail("invalid_document", "文件識別碼無效。")
    collection = collections[kind]
    snap = get_business_db().collection(collection).document(doc_id).get()
    data = snap.to_dict()
    if not data:
        fail("stale_document", "文件不存在，請重新確認標註。", 409)
    metadata = json.loads(json.dumps(data.get("metadata", {}), default=str))
    source_id = data.get("source_id") or metadata.get("source_id")
    active = data.get("corpus_active", metadata.get("corpus_active", True)) is not False
    if not active:
        fail("stale_document", "文件已停用，請重新確認標註。", 409)
    unit_id = None
    if kind == "law" and metadata.get("law_name") and metadata.get("article_number"):
        name = metadata["law_name"].strip()
        name = next(
            (canonical for canonical, aliases in LAW_ALIASES.items() if name in aliases), name
        )
        with suppress(ValueError):
            unit_id = f"law:{name}:{article_number(str(metadata['article_number']))}"
    elif kind != "law" and source_id:
        unit_id = f"{kind}:{source_id}"
    if manual_unit_id and unit_id is not None:
        fail("mapping_not_needed", "已有明確來源識別，不能覆寫人工對應。")
    if manual_unit_id:
        unit_id = f"manual:{kind}:{manual_unit_id}"
    content = data.get("content", "")
    version = metadata.get("version") or metadata.get("corpus_version")
    identity = {
        "content": content,
        "version": version,
        "source_id": source_id,
        "law_name": metadata.get("law_name"),
        "article_number": metadata.get("article_number"),
    }
    return {
        "key": f"{collection}/{doc_id}",
        "kind": kind,
        "collection": collection,
        "doc_id": doc_id,
        "content": content,
        "metadata": metadata,
        "source_id": source_id,
        "version": version,
        "unit_id": unit_id,
        "content_sha256": digest(content),
        "evidence_sha256": digest(identity),
        "manual_unit_id": manual_unit_id,
    }


def save_case(identifier, expected, value: CaseInput, case_id=None):
    current = dataset(identifier)
    if current["archived"]:
        fail("archived", "請先取消封存。", 409)
    previous = next((c for c in current["cases"] if c["id"] == case_id), None)
    if case_id and previous is None:
        fail("not_found", "找不到題目。", 404)
    annotations, seen, relevance = [], set(), {}
    for a in value.annotations:
        if a.kind not in value.data_types:
            fail("invalid_scope", "標註文件必須屬於本題資料類型。")
        doc = document(a.kind, a.doc_id, manual_unit_id=a.manual_unit_id)
        if (a.evidence_sha256 and a.evidence_sha256 != doc["evidence_sha256"]) or (
            value.reviewed and not a.evidence_sha256
        ):
            fail("stale_document", "請重新讀取文件並確認目前版本後儲存標註。", 409)
        if doc["key"] in seen:
            fail("duplicate_annotation", "文件重複標註。")
        seen.add(doc["key"])
        if doc["unit_id"]:
            if doc["unit_id"] in relevance and relevance[doc["unit_id"]] != a.relevance:
                fail("conflicting_annotation", "同一法條／來源的相關性標註必須一致。")
            relevance[doc["unit_id"]] = a.relevance
        annotations.append({"document": doc, "relevance": a.relevance})
    case = {
        "id": case_id or uuid.uuid4().hex,
        **value.model_dump(exclude={"annotations"}),
        "annotations": annotations,
        "query_sha256": digest(value.query),
    }
    if previous and (
        previous["query"] != case["query"] or previous["data_types"] != case["data_types"]
    ):
        case["reviewed"] = False
    if case["reviewed"]:
        validate_review(case)
    updated = [case if c["id"] == case_id else c for c in current["cases"]]
    if not case_id:
        updated.append(case)
    return update_dataset(identifier, expected, cases=updated)


def validate_review(case):
    gold = [a for a in case["annotations"] if a["relevance"] == "relevant"]
    if not case["reviewed"] or any(
        a["relevance"] == "unjudged" or not a["document"]["unit_id"] for a in case["annotations"]
    ):
        fail("review_required", "請完成相關性標註及來源對應後確認題目。", 409)
    if bool(gold) == bool(case["no_relevant"]):
        fail("invalid_gold", "請標註相關來源，或明確確認沒有相關來源。")


def check_evidence(case, collections):
    for a in case["annotations"]:
        old = a["document"]
        new = document(old["kind"], old["doc_id"], collections, old.get("manual_unit_id"))
        if new["key"] != old["key"] or new["evidence_sha256"] != old["evidence_sha256"]:
            fail("stale_document", "標註來源已變更，請重新確認並發布新版本。", 409)


def publish(identifier, expected):
    current = dataset(identifier)
    if current["revision"] != expected:
        fail("revision_conflict", "資料已更新，請重新載入。", 409)
    if current["archived"] or not current["cases"]:
        fail("invalid_dataset", "空資料集或已封存資料集無法發布。")
    collections = collections_config()
    for case in current["cases"]:
        validate_review(case)
        check_evidence(case, collections)
    revision_id = f"{identifier}-{expected}"
    immutable = {
        "id": revision_id,
        "environment": environment(),
        "dataset_id": identifier,
        "revision": expected,
        "sha256": digest(current["cases"]),
        "cases_ref": current["draft_ref"],
        "collections": collections,
        "created_at": timestamp(),
    }

    def commit(tx):
        ref = reference(DATASETS, identifier)
        saved = ref.get(transaction=tx).to_dict()
        rev = reference(REVISIONS, revision_id)
        existing = rev.get(transaction=tx).to_dict()
        if saved["revision"] != expected:
            fail("revision_conflict", "資料已更新，請重新載入。", 409)
        if existing and existing["sha256"] != immutable["sha256"]:
            fail("revision_conflict", "不可覆寫已發布版本。", 409)
        if not existing:
            tx.set(rev, immutable)
        tx.update(ref, {"published_revision": expected})

    infrastructure.atomic(commit)
    return immutable


def run_config(settings):
    base = get_runtime_config()
    return replace(
        base,
        rag_rerank_enabled=True,
        rag_rerank_model=settings.model,
        rag_rerank_candidate_k=settings.candidate_k,
        rag_retrieval_top_k=settings.top_k,
        rag_rerank_timeout_seconds=settings.timeout_seconds,
        rag_rerank_max_chars_per_doc=settings.max_chars_per_doc,
        rag_distance_threshold=settings.distance_threshold,
        openrouter_provider_sort=settings.provider_sort,
    )


def embedding_binding():
    settings = get_settings()
    return {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "prefix_version": "e5-prefix-v1",
    }


def create_run(value, idempotency_key):
    request_data = value.model_dump()
    identifier = digest({"environment": environment(), "kind": "run", "key": idempotency_key})[:32]
    ref = reference(RUNS, identifier)
    existing = ref.get().to_dict()
    fingerprint = digest(request_data)
    if existing:
        if (
            existing.get("environment") != environment()
            or existing["request_sha256"] != fingerprint
        ):
            fail("idempotency_conflict", "此操作識別碼已用於其他請求。", 409)
        if existing["status"] not in jobs.TERMINAL:
            ensure_run_job(existing)
        return get_run(identifier)
    revision = read(REVISIONS, f"{value.dataset_id}-{value.revision}")
    current = read(DATASETS, value.dataset_id)
    if current["archived"]:
        fail("archived", "資料集已封存。", 409)
    cases = load_object(revision["cases_ref"])["cases"]
    if value.case_ids is not None:
        if len(set(value.case_ids)) != len(value.case_ids) or set(value.case_ids) - {
            c["id"] for c in cases
        }:
            fail("invalid_cases", "題目清單無效。")
        cases = [c for c in cases if c["id"] in value.case_ids]
    validate_scope(value.settings, cases)
    config = run_config(value.settings)
    config_data, embedding = asdict(config), embedding_binding()
    if revision["collections"] != config.rag_collections:
        fail("collection_changed", "檢索集合已變更，請重新發布資料集。", 409)
    for case in cases:
        check_evidence(case, config.rag_collections)
    reuse = None
    if value.reuse_run_id:
        reuse = get_run(value.reuse_run_id)
        available = {r["case_id"]: r for r in reuse["results"] if r.get("candidate_ref")}
        for case in cases:
            row = available.get(case["id"])
            binding = retrieval_binding(case, value.settings, config.rag_collections, embedding)
            if not row or row["retrieval_binding"] != binding:
                fail("incompatible_snapshot", "候選快照缺失或檢索條件不同，請重新檢索。", 409)
        reuse = {c["id"]: available[c["id"]]["candidate_ref"] for c in cases}
    snapshot = {
        "cases": cases,
        "config": config_data,
        "settings": value.settings.model_dump(),
        "embedding": embedding,
        "reuse": reuse,
    }
    record = {
        "id": identifier,
        "environment": environment(),
        "dataset_id": value.dataset_id,
        "dataset_revision": value.revision,
        "dataset_sha256": revision["sha256"],
        "settings": value.settings.model_dump(),
        "request_sha256": fingerprint,
        "snapshot_ref": store_object(f"runs/{identifier}/input", snapshot),
        "results_ref": None,
        "summary": summarize([], len(cases)),
        "total": len(cases),
        "status": "queued",
        "created_at": timestamp(),
        "updated_at": timestamp(),
        "job_id": hashlib.sha256(f"{environment()}:rerank_test:{identifier}".encode()).hexdigest()[
            :32
        ],
        "archived": False,
    }

    def commit(tx):
        previous = ref.get(transaction=tx).to_dict()
        if previous and previous["request_sha256"] != fingerprint:
            fail("idempotency_conflict", "此操作識別碼已用於其他請求。", 409)
        if not previous:
            tx.set(ref, record)
        return previous or record

    record = infrastructure.atomic(commit)
    ensure_run_job(record)
    return get_run(identifier)


def ensure_run_job(record):
    return jobs.submit_job(
        "rerank_test",
        {"run_id": record["id"]},
        snapshot={"snapshot_ref": record["snapshot_ref"]},
        idempotency_key=record["id"],
    )


def get_run(identifier):
    record = read(RUNS, identifier)
    job = jobs.job_ref(record["job_id"]).get().to_dict()
    if job and job.get("environment") == environment():
        if job["expires_at"] > jobs.now():
            job = jobs.get_job(record["job_id"], internal=True)
        record.update(
            status=job["status"], error=job.get("error"), progress=job.get("progress", {})
        )
    results = load_object(record["results_ref"]) if record.get("results_ref") else []
    return {**record, "results": results}


def project_status(job):
    """Persist job status independently of job retention and browser sessions."""
    if job.get("kind") != "rerank_test":
        return
    run_id = (job.get("input") or {}).get("payload", {}).get("run_id")
    if not run_id:
        return
    ref = reference(RUNS, run_id)

    def project(tx):
        # Re-read inside the transaction so an older observer cannot undo a retry.
        latest = jobs.job_ref(job["job_id"]).get(transaction=tx).to_dict()
        old = ref.get(transaction=tx).to_dict()
        if old and latest and old["environment"] == latest["environment"]:
            tx.update(
                ref,
                {
                    "status": latest["status"],
                    "error": latest.get("error"),
                    "progress": latest.get("progress", {}),
                    "updated_at": timestamp(),
                },
            )

    infrastructure.atomic(project)


async def retrieve(case, settings, config):
    started, timings, diagnostics = monotonic(), {}, {}
    docs = await FirestoreVectorRAG().retrieve(
        case["query"],
        top_k=settings.candidate_k,
        collection_names_by_data_type=config.rag_collections,
        selected_data_types=case["data_types"],
        preserve_data_types=settings.preserve_data_types,
        distance_threshold=settings.distance_threshold,
        timings=timings,
        diagnostics=diagnostics,
    )
    kinds = {collection: kind for kind, collection in config.rag_collections.items()}
    documents = []
    for d in docs:
        kind = kinds[d.metadata["collection"]]
        current = document(kind, d.doc_id, config.rag_collections)
        # Do not quietly rerank a newer document than the one retrieved.
        if current["content"] != d.content:
            fail("corpus_changed", "檢索期間文件已變更，請重新準備。", 409)
        current["metadata"].update(d.metadata)
        current["retrieval_method"] = d.metadata.get("retrieval_method")
        current["distance"] = d.metadata.get("distance")
        documents.append(current)
    return {
        "documents": documents,
        "retrieval_ms": (monotonic() - started) * 1000,
        "timings": timings,
        "diagnostics": diagnostics,
        "observed_at": timestamp(),
    }


def _persist_results(run_id, results, context):
    ref = store_object(f"runs/{run_id}/results", results)
    run_ref = reference(RUNS, run_id)

    def commit(tx):
        job = jobs.job_ref(context.job_id).get(transaction=tx).to_dict()
        record = run_ref.get(transaction=tx).to_dict()
        if (
            job.get("lease_token") != context.lease_token
            or job["status"] != "running"
            or job["lease_until"] <= jobs.now()
        ):
            raise jobs.LeaseLost()
        tx.update(
            run_ref,
            {
                "results_ref": ref,
                "summary": summarize(results, record["total"]),
                "updated_at": timestamp(),
            },
        )

    infrastructure.atomic(commit)


def execute_run(job, context):
    run_id = job["payload"]["run_id"]
    snapshot = load_object(job["snapshot"]["snapshot_ref"])
    config, settings = (
        RuntimeConfig(**snapshot["config"]),
        Settings.model_validate(snapshot["settings"]),
    )
    cases = snapshot["cases"]
    record = read(RUNS, run_id)
    results = load_object(record["results_ref"]) if record.get("results_ref") else []
    checkpoint = dict(job.get("checkpoint") or {})
    index = len(results)
    if index == len(cases):
        return {"run_id": run_id, "summary": summarize(results, len(cases))}
    case = cases[index]
    context.ensure_active()
    if checkpoint.get("index") == index and (
        checkpoint.get("completed_ref") or checkpoint.get("failed_ref")
    ):
        recovered = (
            load_object(checkpoint["completed_ref"])
            if checkpoint.get("completed_ref")
            else load_object(checkpoint["failed_ref"])
        )
        recovered.setdefault("elapsed_ms", 0)
        results.append(recovered)
        _persist_results(run_id, results, context)
        context.checkpoint({"index": index + 1})
        context.progress({"completed": len(results), "total": len(cases)})
        if len(results) < len(cases):
            raise jobs.ContinueJob()
        return {"run_id": run_id, "summary": summarize(results, len(cases))}
    started = monotonic()
    binding = retrieval_binding(case, settings, config.rag_collections, snapshot["embedding"])
    row = {
        "case_id": case["id"],
        "query": case["query"],
        "data_types": case["data_types"],
        "case": case,
        "retrieval_binding": binding,
        "status": "completed",
        "metrics": None,
    }
    candidate_ref = checkpoint.get("candidate_ref") if checkpoint.get("index") == index else None
    try:
        check_evidence(case, config.rag_collections)
        if not candidate_ref:
            if snapshot.get("reuse"):
                candidate_ref = snapshot["reuse"][case["id"]]
                context.checkpoint({"index": index, "candidate_ref": candidate_ref})
            else:
                if embedding_binding() != snapshot["embedding"]:
                    fail("embedding_changed", "Embedding 設定已變更，請建立新執行。", 409)
                context.external_call_started("rerank_test_embedding")
                try:
                    pool = asyncio.run(retrieve(case, settings, config))
                except Exception as exc:
                    row.update(
                        status="retrieval_failed",
                        error={
                            "code": type(exc).__name__,
                            "detail": "無法取得候選；此題未完成量測。",
                        },
                    )
                    failed_ref = store_object(f"runs/{run_id}/failed-results", row)
                    context.external_call_finished(
                        checkpoint={"index": index, "failed_ref": failed_ref}
                    )
                    pool = None
                if pool is not None:
                    candidate_ref = store_object(f"runs/{run_id}/candidates", pool)
                    context.external_call_finished(
                        checkpoint={"index": index, "candidate_ref": candidate_ref}
                    )
        if candidate_ref:
            pool = load_object(candidate_ref)
            row.update(candidate_ref=candidate_ref, candidates=pool["documents"], retrieval=pool)
            docs = [
                RAGDocument(
                    content=d["content"],
                    metadata={**d["metadata"], "collection": d["collection"]},
                    doc_id=d["doc_id"],
                )
                for d in pool["documents"]
            ]
            # Only a real provider exchange gets a durable charge-uncertainty marker.
            will_call = len(docs) > settings.top_k
            if will_call:
                context.external_call_started("rerank_test_provider")
            ranking = asyncio.run(
                rerank_candidates(
                    case["query"],
                    docs,
                    config,
                    top_k=settings.top_k,
                    preserve_data_types=settings.preserve_data_types,
                )
            )

            def key(d):
                return f"{d.metadata['collection']}/{d.doc_id}"

            model_order = None if ranking.ranked is None else [key(d) for d in ranking.ranked]
            final_order = [key(d) for d in ranking.selected]
            row.update(
                rerank=ranking.info,
                model_order=model_order,
                final_order=final_order,
                scores={f"{a}/{b}": score for (a, b), score in ranking.scores.items()},
                selection_reasons={
                    f"{a}/{b}": reasons for (a, b), reasons in ranking.selection_reasons.items()
                },
                excerpts=[
                    {"key": d["key"], "text": text, "sha256": digest(text)}
                    for d, text in zip(pool["documents"], ranking.excerpts, strict=True)
                ],
                metrics=evaluate(
                    case, pool["documents"], model_order, final_order, settings.top_k, ranking.info
                ),
            )
            row["elapsed_ms"] = (monotonic() - started) * 1000
            completed_ref = store_object(f"runs/{run_id}/case-results", row)
            completed_checkpoint = {
                "index": index,
                "completed_ref": completed_ref,
                "candidate_ref": candidate_ref,
            }
            if will_call:
                context.external_call_finished(checkpoint=completed_checkpoint)
            else:
                context.checkpoint(completed_checkpoint)
    except jobs.JobFailure as exc:
        row.update(status="invalid_evidence", error=exc.error)
    row.setdefault("elapsed_ms", (monotonic() - started) * 1000)
    results.append(row)
    _persist_results(run_id, results, context)
    context.checkpoint({"index": index + 1})
    context.progress({"completed": len(results), "total": len(cases)})
    if len(results) < len(cases):
        raise jobs.ContinueJob()
    return {"run_id": run_id, "summary": summarize(results, len(cases))}


def prepare(identifier, expected, value, idempotency_key):
    current = dataset(identifier)
    if current["revision"] != expected:
        fail("revision_conflict", "資料已更新，請重新載入。", 409)
    case = next((c for c in current["cases"] if c["id"] == value.case_id), None)
    if not case:
        fail("not_found", "找不到題目。", 404)
    validate_scope(value.settings, [case])
    config = run_config(value.settings)
    job = jobs.submit_job(
        "rerank_prepare",
        {"dataset_id": identifier, "case_id": case["id"]},
        snapshot={
            "case": case,
            "config": asdict(config),
            "settings": value.settings.model_dump(),
            "embedding": embedding_binding(),
        },
        idempotency_key=idempotency_key,
    )
    preparation_id = digest({"dataset_id": identifier, "case_id": case["id"]})[:32]
    reference(PREPARATIONS, preparation_id).set(
        {
            "id": preparation_id,
            "environment": environment(),
            "dataset_id": identifier,
            "case_id": case["id"],
            "query_sha256": case["query_sha256"],
            "data_types": case["data_types"],
            "job_id": job["job_id"],
        }
    )
    return {"job_id": job["job_id"], "status": job["status"]}


def execute_prepare(job, context):
    if job.get("checkpoint", {}).get("result_ref"):
        ref = job["checkpoint"]["result_ref"]
        reference(PREPARATIONS, job["job_id"]).set(
            {"environment": environment(), "result_ref": ref}
        )
        return load_object(ref)
    snapshot = job["snapshot"]
    if snapshot.get("embedding") != embedding_binding():
        fail("embedding_changed", "Embedding 設定已變更，請重新準備候選。", 409)
    context.external_call_started("rerank_annotation_embedding")
    pool = asyncio.run(
        retrieve(
            snapshot["case"],
            Settings.model_validate(snapshot["settings"]),
            RuntimeConfig(**snapshot["config"]),
        )
    )
    ref = store_object(f"preparations/{job['job_id']}", pool)
    context.external_call_finished(checkpoint={"result_ref": ref})
    reference(PREPARATIONS, job["job_id"]).set(
        {
            "environment": environment(),
            "result_ref": ref,
        }
    )
    return pool


def get_preparation(identifier, case_id):
    current = dataset(identifier)
    case = next((c for c in current["cases"] if c["id"] == case_id), None)
    if not case:
        fail("not_found", "找不到題目。", 404)
    preparation_id = digest({"dataset_id": identifier, "case_id": case_id})[:32]
    ref = reference(PREPARATIONS, preparation_id)
    record = ref.get().to_dict()
    if (
        not record
        or record["environment"] != environment()
        or record["query_sha256"] != case["query_sha256"]
        or record["data_types"] != case["data_types"]
    ):
        return {"status": "none", "result": None}
    raw = jobs.job_ref(record["job_id"]).get().to_dict()
    if raw:
        if raw["expires_at"] > jobs.now():
            raw = jobs.get_job(record["job_id"], internal=True)
        result_ref = raw.get("checkpoint", {}).get("result_ref")
        if result_ref and not record.get("result_ref"):
            ref.update({"result_ref": result_ref})
            record["result_ref"] = result_ref
        return {
            "job_id": record["job_id"],
            "status": raw["status"],
            "error": raw.get("error"),
            "result": load_object(record["result_ref"]) if record.get("result_ref") else None,
        }
    completed = reference(PREPARATIONS, record["job_id"]).get().to_dict()
    if completed and completed.get("environment") == environment():
        record["result_ref"] = completed["result_ref"]
    return {
        "job_id": record["job_id"],
        "status": "succeeded" if record.get("result_ref") else "expired",
        "result": load_object(record["result_ref"]) if record.get("result_ref") else None,
    }


def browse_documents(kind, cursor=None):
    from admin_app.corpus.parsing import CorpusError
    from admin_app.corpus.service import _page, record

    if kind not in {"law", "judgment", "remedy"}:
        fail("invalid_kind", "資料類型無效。")
    name = collections_config()[kind]
    try:
        snapshots, next_cursor = _page(
            get_business_db().collection(name), f"{environment()}:{kind}:{name}", 50, cursor
        )
    except CorpusError:
        fail("invalid_cursor", "分頁游標無效，請重新瀏覽資料庫。")
    return {"items": [record(snapshot, kind) for snapshot in snapshots], "next_cursor": next_cursor}
