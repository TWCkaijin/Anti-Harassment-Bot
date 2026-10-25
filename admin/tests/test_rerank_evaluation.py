"""Offline recall, persistence and job replay tests; providers and corpus are fakes."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from admin_app import jobs
from admin_app.rerank_tests import service
from admin_app.rerank_tests.metrics import evaluate, summarize
from client.tests.test_agent import fake_runtime_config
from rag_core.rag import rerank

from .fakes import FakeDB

ROOT = "/api/v1/admin/rerank-tests"


@pytest.fixture
def corpus(monkeypatch):
    db = FakeDB()
    config = replace(
        fake_runtime_config(),
        rag_collections={"law": "laws", "judgment": "judgments", "remedy": "remedies"},
    )
    monkeypatch.setattr(service, "get_business_db", lambda: db)
    monkeypatch.setattr(service, "get_runtime_config", lambda: config)
    for index in range(1, 5):
        db.collection("laws").document(str(index)).set(
            {
                "content": f"合成法條 {index}",
                "metadata": {"law_name": "民法", "article_number": str(index), "version": "v1"},
            }
        )
    return db


def prepared(client, headers):
    response = client.post(ROOT + "/datasets", json={"name": "合成評估"}, headers=headers)
    assert response.status_code == 201, response.json
    dataset = response.json
    response = client.post(
        f"{ROOT}/datasets/{dataset['id']}/cases",
        headers={**headers, "If-Match": "1"},
        json={
            "query": "合成問題",
            "data_types": ["law"],
            "privacy_confirmed": True,
            "reviewed": True,
            "annotations": [
                {
                    "kind": "law",
                    "doc_id": str(i),
                    "relevance": "relevant",
                    "evidence_sha256": service.document("law", str(i))["evidence_sha256"],
                }
                for i in (1, 2, 3)
            ],
        },
    )
    assert response.status_code == 201, response.json
    dataset = response.json
    response = client.post(
        f"{ROOT}/datasets/{dataset['id']}/publish", headers={**headers, "If-Match": "2"}
    )
    assert response.status_code == 201, response.json
    return dataset


def drain(app, db, job_id):
    for _ in range(10):
        job = jobs.job_ref(job_id).get().to_dict()
        if job["status"] in jobs.TERMINAL:
            return job
        with app.app_context():
            jobs.execute_job(job_id, job["dispatch_generation"])
    raise AssertionError("Job did not finish")


def test_cut_before_unit_dedup_and_missing_gold():
    documents = [
        {"key": key, "unit_id": unit} for key, unit in [("a/1", "A"), ("a/2", "A"), ("b/1", "B")]
    ]
    case = {
        "annotations": [
            {"relevance": "relevant", "document": {"key": key, "unit_id": unit, "kind": "law"}}
            for key, unit in [("a/1", "A"), ("b/1", "B"), ("c/1", "C")]
        ],
        "data_types": ["law"],
    }
    result = evaluate(case, documents, ["b/1", "a/1", "a/2"], ["b/1", "a/1"], 2, {"status": "ok"})
    assert result["stages"]["candidate"]["recall"] == 2 / 3
    assert result["stages"]["before"]["recall"] == 1 / 3
    assert result["stages"]["final"]["recall"] == 2 / 3
    assert result["missing_candidates"] == ["C"]
    summary = summarize(
        [
            {
                "metrics": result,
                "data_types": ["law"],
                "status": "completed",
                "elapsed_ms": 12,
                "rerank": {"status": "ok"},
            }
        ],
        2,
    )
    assert summary["measurement_rate"] == 0.5
    assert summary["stages"]["final"]["micro"] == 2 / 3


def test_empty_gold_and_fallback_are_not_model_success():
    result = evaluate(
        {"annotations": [], "data_types": ["law"]}, [], None, [], 3, {"status": "timeout"}
    )
    assert all(stage["recall"] is None for stage in result["stages"].values())
    assert result["stages"]["model"]["hits"] is None


def test_versions_csrf_and_cross_session_history(signed_in, app, services, corpus):
    client, headers = signed_in
    assert client.post(ROOT + "/datasets", json={"name": "x"}).status_code == 403
    data = prepared(client, headers)
    conflict = client.patch(
        f"{ROOT}/datasets/{data['id']}", json={"name": "old"}, headers={**headers, "If-Match": "1"}
    )
    assert conflict.status_code == 409
    other = app.test_client()
    response = other.post(
        "/api/v1/admin/auth/login",
        json={"password": "a-long-test-password"},
        headers={"Origin": headers["Origin"]},
    )
    assert response.status_code == 200
    assert other.get(f"{ROOT}/datasets/{data['id']}").status_code == 200
    immutable = other.get(f"{ROOT}/datasets/{data['id']}/revisions/2").json
    assert len(immutable["cases"]) == 1
    services[0].collection(service.DATASETS).document(data["id"]).update({"environment": "other"})
    assert other.get(f"{ROOT}/datasets/{data['id']}").status_code == 404


def test_one_pool_replay_and_durable_results(signed_in, app, services, corpus, monkeypatch):
    client, headers = signed_in
    data = prepared(client, headers)
    docs = [service.document("law", str(i)) for i in (1, 4, 2)]
    retrieval = AsyncMock(
        return_value={"documents": docs, "retrieval_ms": 5, "observed_at": "synthetic"}
    )
    monkeypatch.setattr(service, "retrieve", retrieval)

    async def provider(query, candidates, config):
        return rerank.RankedDocuments(
            [candidates[2], candidates[0], candidates[1]],
            {rerank._key(d): i for i, d in enumerate(candidates)},
        ), "synthetic-model"

    provider_mock = AsyncMock(side_effect=provider)
    monkeypatch.setattr(rerank, "_request", provider_mock)
    payload = {
        "dataset_id": data["id"],
        "revision": 2,
        "settings": {"model": "synthetic", "candidate_k": 3, "top_k": 2},
        "external_confirmed": True,
    }
    created = client.post(
        ROOT + "/runs", json=payload, headers={**headers, "Idempotency-Key": "one"}
    )
    assert created.status_code == 202, created.json
    run = created.json
    completed = drain(app, services[0], run["job_id"])
    assert completed["status"] == "succeeded", completed.get("error")
    assert retrieval.await_count == provider_mock.await_count == 1
    result = client.get(f"{ROOT}/runs/{run['id']}").json
    assert result["summary"]["stages"]["before"]["macro"] == 1 / 3
    assert result["summary"]["stages"]["final"]["macro"] == 2 / 3
    assert len(result["results"][0]["excerpts"]) == 3
    assert result["results"][0]["metrics"]["missing_candidates"] == ["law:民法:3"]
    replay = client.post(
        ROOT + "/runs",
        json={**payload, "reuse_run_id": run["id"]},
        headers={**headers, "Idempotency-Key": "two"},
    )
    assert replay.status_code == 202, replay.json
    assert drain(app, services[0], replay.json["job_id"])["status"] == "succeeded"
    assert retrieval.await_count == 1
    assert provider_mock.await_count == 2
    # Historical results outlive the generic job record.
    jobs.job_ref(run["job_id"]).delete()
    assert client.get(f"{ROOT}/runs/{run['id']}").json["status"] == "succeeded"
    assert client.get(f"{ROOT}/runs/{run['id']}/export?format=csv").status_code == 200
    corpus.collection("laws").document("3").update({"content": "changed"})
    stale = client.post(
        ROOT + "/runs", json=payload, headers={**headers, "Idempotency-Key": "three"}
    )
    assert stale.status_code == 409


def test_limits_and_review_reset(signed_in, corpus):
    client, headers = signed_in
    data = prepared(client, headers)
    case = data["cases"][0]
    value = {
        "query": "修改後的問題",
        "data_types": ["law"],
        "privacy_confirmed": True,
        "reviewed": True,
        "no_relevant": True,
    }
    response = client.put(
        f"{ROOT}/datasets/{data['id']}/cases/{case['id']}",
        json=value,
        headers={**headers, "If-Match": "2"},
    )
    assert response.status_code == 200
    assert response.json["cases"][0]["reviewed"] is False
    response = client.post(
        f"{ROOT}/datasets/{data['id']}/cases",
        json={**value, "query": "甲" * 4001},
        headers={**headers, "If-Match": "3"},
    )
    assert response.status_code == 422


def test_recovery_after_provider_checkpoint_never_repeats_call(
    signed_in, app, services, corpus, monkeypatch
):
    client, headers = signed_in
    data = prepared(client, headers)
    docs = [service.document("law", str(i)) for i in (1, 4, 2)]
    retrieval = AsyncMock(return_value={"documents": docs})
    monkeypatch.setattr(service, "retrieve", retrieval)
    provider = AsyncMock(return_value=([], "synthetic"))

    # Valid ranking; failure is injected after the provider outcome is durable.
    async def ranking(query, candidates, config):
        return list(reversed(candidates)), "synthetic"

    provider.side_effect = ranking
    monkeypatch.setattr(rerank, "_request", provider)
    original = service._persist_results

    def crash(*args):
        raise RuntimeError("synthetic crash after completed checkpoint")

    monkeypatch.setattr(service, "_persist_results", crash)
    payload = {
        "dataset_id": data["id"],
        "revision": 2,
        "settings": {"model": "synthetic", "candidate_k": 3, "top_k": 2},
        "external_confirmed": True,
    }
    run = client.post(
        ROOT + "/runs", json=payload, headers={**headers, "Idempotency-Key": "recovery"}
    ).json
    assert drain(app, services[0], run["job_id"])["status"] == "failed"
    assert provider.await_count == 1
    monkeypatch.setattr(service, "_persist_results", original)
    response = client.post(
        f"/api/v1/admin/jobs/{run['job_id']}/retry",
        json={"acknowledge_duplicate_cost": True},
        headers=headers,
    )
    assert response.status_code == 202
    assert drain(app, services[0], run["job_id"])["status"] == "succeeded"
    assert provider.await_count == retrieval.await_count == 1
    saved = client.get(f"{ROOT}/runs/{run['id']}").json
    row = saved["results"][0]
    assert (
        evaluate(
            row["case"], row["candidates"], row["model_order"], row["final_order"], 2, row["rerank"]
        )
        == row["metrics"]
    )
    # Same idempotency receipt still works after source changes and generic TTL deletion.
    jobs.job_ref(run["job_id"]).delete()
    corpus.collection("laws").document("3").update({"content": "new"})
    repeated = client.post(
        ROOT + "/runs", json=payload, headers={**headers, "Idempotency-Key": "recovery"}
    )
    assert repeated.status_code == 202
    assert repeated.json["id"] == run["id"]
    assert jobs.job_ref(run["job_id"]).get().to_dict() is None
    assert (
        client.post(
            ROOT + "/runs",
            json={**payload, "settings": {"model": "other"}},
            headers={**headers, "Idempotency-Key": "recovery"},
        ).status_code
        == 409
    )


def test_failed_retrieval_and_cancel_are_distinct_from_zero_recall(
    signed_in, app, services, corpus, monkeypatch
):
    client, headers = signed_in
    data = prepared(client, headers)
    retrieval = AsyncMock(side_effect=RuntimeError("offline provider"))
    monkeypatch.setattr(service, "retrieve", retrieval)
    payload = {
        "dataset_id": data["id"],
        "revision": 2,
        "settings": {"model": "synthetic"},
        "external_confirmed": True,
    }
    run = client.post(
        ROOT + "/runs", json=payload, headers={**headers, "Idempotency-Key": "failure"}
    ).json
    assert drain(app, services[0], run["job_id"])["status"] == "succeeded"
    result = client.get(f"{ROOT}/runs/{run['id']}").json
    assert result["summary"]["measured"] == 0
    assert result["summary"]["stages"]["final"]["macro"] is None
    assert result["results"][0]["status"] == "retrieval_failed"
    run2 = client.post(
        ROOT + "/runs", json=payload, headers={**headers, "Idempotency-Key": "cancel"}
    ).json
    assert (
        client.post(f"/api/v1/admin/jobs/{run2['job_id']}/cancel", headers=headers).status_code
        == 200
    )
    drain(app, services[0], run2["job_id"])
    assert retrieval.await_count == 1
    assert client.get(f"{ROOT}/runs/{run2['id']}").json["status"] == "cancelled"


def test_current_evidence_confirmation_and_mixed_budget(signed_in, corpus):
    client, headers = signed_in
    data = prepared(client, headers)
    case = data["cases"][0]
    body = {
        "query": case["query"],
        "data_types": ["law"],
        "privacy_confirmed": True,
        "reviewed": True,
        "annotations": [
            {
                "kind": "law",
                "doc_id": a["document"]["doc_id"],
                "relevance": "relevant",
                "evidence_sha256": a["document"]["evidence_sha256"],
            }
            for a in case["annotations"]
        ],
    }
    corpus.collection("laws").document("1").update({"content": "changed"})
    assert (
        client.put(
            f"{ROOT}/datasets/{data['id']}/cases/{case['id']}",
            json=body,
            headers={**headers, "If-Match": "2"},
        ).status_code
        == 409
    )
    body.update(data_types=["law", "judgment"], annotations=[], no_relevant=True)
    response = client.put(
        f"{ROOT}/datasets/{data['id']}/cases/{case['id']}",
        json=body,
        headers={**headers, "If-Match": "2"},
    )
    assert response.status_code == 200
    # Scope change needs a separate review, then can be published.
    response = client.put(
        f"{ROOT}/datasets/{data['id']}/cases/{case['id']}",
        json=body,
        headers={**headers, "If-Match": "3"},
    )
    assert response.status_code == 200
    assert (
        client.post(
            f"{ROOT}/datasets/{data['id']}/publish", headers={**headers, "If-Match": "4"}
        ).status_code
        == 201
    )
    payload = {
        "dataset_id": data["id"],
        "revision": 4,
        "settings": {"model": "synthetic", "candidate_k": 3, "top_k": 1},
        "external_confirmed": True,
    }
    assert (
        client.post(
            ROOT + "/runs", json=payload, headers={**headers, "Idempotency-Key": "budget"}
        ).status_code
        == 422
    )
    corpus.collection("judgments").document("1").set(
        {"content": "judgment", "source_id": "source-x"}
    )
    assert service.document("judgment", "1")["unit_id"] == "judgment:source-x"
    assert service.document("law", "1")["key"] != service.document("judgment", "1")["key"]
    corpus.collection("judgments").document("1").update({"corpus_active": False})
    with pytest.raises(jobs.JobFailure):
        service.document("judgment", "1")


def test_annotation_preparation_is_durable_and_query_bound(
    signed_in, app, services, corpus, monkeypatch
):
    client, headers = signed_in
    data = prepared(client, headers)
    case = data["cases"][0]
    retrieval = AsyncMock(return_value={"documents": [service.document("law", "1")]})
    monkeypatch.setattr(service, "retrieve", retrieval)
    path = f"{ROOT}/datasets/{data['id']}"
    response = client.post(
        path + "/prepare",
        headers={**headers, "If-Match": "2", "Idempotency-Key": "prepare"},
        json={
            "case_id": case["id"],
            "settings": {"model": "synthetic"},
            "external_confirmed": True,
        },
    )
    assert response.status_code == 202, response.json
    job_id = response.json["job_id"]
    assert drain(app, services[0], job_id)["status"] == "succeeded"
    assert retrieval.await_count == 1
    jobs.job_ref(job_id).delete()
    saved = client.get(path + f"/cases/{case['id']}/preparation").json
    assert saved["status"] == "succeeded"
    assert len(saved["result"]["documents"]) == 1
    value = {"query": "新查詢", "data_types": ["law"], "privacy_confirmed": True}
    assert (
        client.put(
            path + f"/cases/{case['id']}", json=value, headers={**headers, "If-Match": "2"}
        ).status_code
        == 200
    )
    assert client.get(path + f"/cases/{case['id']}/preparation").json["status"] == "none"


def test_unknown_external_outcome_requires_explicit_retry(signed_in, app, services, corpus):
    from datetime import timedelta

    client, headers = signed_in
    data = prepared(client, headers)
    run = client.post(
        ROOT + "/runs",
        json={
            "dataset_id": data["id"],
            "revision": 2,
            "settings": {"model": "synthetic"},
            "external_confirmed": True,
        },
        headers={**headers, "Idempotency-Key": "unknown"},
    ).json
    jobs.job_ref(run["job_id"]).update(
        {
            "status": "running",
            "lease_until": jobs.now() - timedelta(seconds=1),
            "external_call": {"label": "rerank_test_provider"},
        }
    )
    assert client.get(f"{ROOT}/runs/{run['id']}").json["status"] == "needs_review"
    assert (
        client.post(
            f"/api/v1/admin/jobs/{run['job_id']}/retry", json={}, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/admin/jobs/{run['job_id']}/retry",
            json={"acknowledge_duplicate_cost": True},
            headers=headers,
        ).status_code
        == 202
    )


def test_browse_uses_runtime_mapping_and_cursor_scope(signed_in, corpus, monkeypatch):
    from .fakes import Collection, Query

    monkeypatch.setattr(
        Collection,
        "order_by",
        lambda self, name: Query(self.stream()).order_by(name),
        raising=False,
    )
    client, _ = signed_in
    for i in range(5, 56):
        corpus.collection("laws").document(str(i)).set({"content": f"synthetic {i}"})
    first = client.get(ROOT + "/documents?kind=law")
    assert first.status_code == 200
    assert len(first.json["items"]) == 50
    cursor = first.json["next_cursor"]
    assert cursor
    second = client.get(ROOT + "/documents", query_string={"kind": "law", "cursor": cursor})
    assert len(second.json["items"]) == 5
    assert not ({r["id"] for r in first.json["items"]} & {r["id"] for r in second.json["items"]})
    assert (
        client.get(
            ROOT + "/documents", query_string={"kind": "judgment", "cursor": cursor}
        ).status_code
        == 422
    )


def test_manual_units_limits_and_empty_candidates(signed_in, corpus):
    client, headers = signed_in
    data = prepared(client, headers)
    corpus.collection("remedies").document("legacy").set({"content": "missing source identity"})
    assert service.document("remedy", "legacy")["unit_id"] is None
    assert (
        service.document("remedy", "legacy", manual_unit_id="confirmed-source")["unit_id"]
        == "manual:remedy:confirmed-source"
    )
    with pytest.raises(jobs.JobFailure):
        service.update_dataset(data["id"], 2, cases=[data["cases"][0]] * 201)
    case = data["cases"][0]
    result = evaluate(case, [], None, [], 2, {"status": "skipped_small_pool"})
    assert result["stages"]["candidate"]["recall"] == 0
    assert result["stages"]["final"]["recall"] == 0
    assert result["stages"]["model"]["recall"] is None
    assert (
        client.post(
            ROOT + "/datasets", json={"name": "unauthorized"}, headers={"Origin": headers["Origin"]}
        ).status_code
        == 403
    )
