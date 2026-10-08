"""Synthetic rerank exchanges; no external calls or Firestore writes."""

import asyncio
import json
from dataclasses import replace
from time import monotonic
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.core.runtime_config import resolve_runtime_config, validate_runtime_config_update
from backend.app.rag import rerank
from backend.app.rag.base import RAGDocument
from tests.test_agent import fake_runtime_config


def config(**changes):
    return replace(fake_runtime_config(), rag_rerank_enabled=True, **changes)


def documents():
    return [RAGDocument(f"合成文件 {i}", {"collection": "laws"}, doc_id=str(i)) for i in range(5)]


class Rag:
    def __init__(self, docs):
        self.retrieve = AsyncMock(return_value=docs)


@pytest.mark.asyncio
async def test_one_retrieval_and_rerank_preserves_original_documents(monkeypatch):
    docs = documents()
    request = AsyncMock(return_value=(list(reversed(docs)), "actual-model"))
    monkeypatch.setattr(rerank, "_request", request)
    rag, metrics, diagnostics = Rag(docs), {}, {}
    result = await rerank.retrieve_with_rerank(
        rag, "masked query", config(), top_k=2, timings=metrics, diagnostics=diagnostics
    )
    assert result == [docs[4], docs[3]]
    assert result[0] is docs[4]
    assert rag.retrieve.await_count == request.await_count == 1
    assert rag.retrieve.call_args.kwargs["top_k"] == 20
    assert diagnostics["rerank"]["model"] == "actual-model"
    assert metrics["rerank_ms"] >= 0
    assert "masked query" not in json.dumps(diagnostics)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,count", [(False, 5), (True, 0), (True, 2)])
async def test_disabled_and_small_pools_skip_network(monkeypatch, enabled, count):
    request = AsyncMock()
    monkeypatch.setattr(rerank, "_request", request)
    cfg = replace(config(), rag_rerank_enabled=enabled)
    docs = documents()[:count]
    assert await rerank.retrieve_with_rerank(Rag(docs), "query", cfg, top_k=2) == docs
    request.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_cancels_request_and_never_retrieves_again(monkeypatch):
    cancelled = asyncio.Event()

    async def slow(*args):
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.set()

    monkeypatch.setattr(rerank, "_request", slow)
    docs, diagnostics = documents(), {}
    rag = Rag(docs)
    start = monotonic()
    result = await rerank.retrieve_with_rerank(
        rag, "query", config(rag_rerank_timeout_seconds=0.1), top_k=2, diagnostics=diagnostics
    )
    assert result == docs[:2]
    assert monotonic() - start < 0.5
    assert cancelled.is_set() and rag.retrieve.await_count == 1
    assert diagnostics["rerank"]["status"] == "timeout"


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_a_successful_fallback(monkeypatch):
    monkeypatch.setattr(rerank, "_request", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await rerank.retrieve_with_rerank(Rag(documents()), "query", config(), top_k=2)


def test_selection_preserves_exact_law_and_source_coverage():
    docs = documents()
    docs[0].metadata["retrieval_method"] = "exact"
    docs[1].metadata["collection"] = "judgments"
    docs[2].metadata["collection"] = "remedies"
    selected = rerank._select(docs, list(reversed(docs)), 3, True)
    assert docs[0] in selected
    assert {d.metadata["collection"] for d in selected} == {"laws", "judgments", "remedies"}
    assert len(selected) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["duplicate", "out_of_range", "missing", "nan", "429", "503"])
async def test_invalid_provider_results_fall_back_without_retry(monkeypatch, failure):
    calls = []
    original_client = httpx.AsyncClient

    def handler(request):
        calls.append(json.loads(request.content))
        if failure.isdigit():
            return httpx.Response(int(failure), json={"error": "synthetic"})
        results = [{"index": i, "relevance_score": 0.1} for i in range(5)]
        if failure == "duplicate":
            results[4]["index"] = 0
        if failure == "out_of_range":
            results[4]["index"] = 10
        if failure == "missing":
            results.pop()
        if failure == "nan":
            results[4]["relevance_score"] = "NaN"
        return httpx.Response(200, json={"results": results})

    monkeypatch.setattr(
        rerank.httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw),
    )
    docs, diagnostics = documents(), {}
    cfg = config(openrouter_provider_sort="latency", rag_rerank_max_chars_per_doc=100)
    assert (
        await rerank.retrieve_with_rerank(
            Rag(docs), "masked", cfg, top_k=2, diagnostics=diagnostics
        )
        == docs[:2]
    )
    assert len(calls) == 1 and calls[0]["provider"] == {"sort": "latency"}
    assert all(len(text) <= 100 for text in calls[0]["documents"])
    assert diagnostics["rerank"]["status"] == "failed"


def test_provider_sort_merges_reasoning_and_auto_omits_it():
    payload = {
        "extra_body": {"reasoning": {"effort": "low"}, "provider": {"require_parameters": True}}
    }
    rerank.apply_provider_sort(payload, config(openrouter_provider_sort="latency"))
    assert payload["extra_body"] == {
        "reasoning": {"effort": "low"},
        "provider": {"require_parameters": True, "sort": "latency"},
    }
    assert rerank.apply_provider_sort({}, config()) == {}


@pytest.mark.parametrize(
    "key,value",
    [
        ("openrouter_provider_sort", "fast"),
        ("rag_rerank_enabled", "true"),
        ("rag_rerank_candidate_k", 51),
        ("rag_rerank_candidate_k", True),
        ("rag_rerank_timeout_seconds", float("nan")),
        ("rag_rerank_timeout_seconds", 0),
        ("rag_rerank_model", " "),
    ],
)
def test_invalid_runtime_settings_rejected(key, value):
    with pytest.raises(ValueError):
        validate_runtime_config_update({key: value})


def test_cross_field_validation_and_request_snapshot():
    with pytest.raises(ValueError):
        resolve_runtime_config(config(), {"rag_rerank_candidate_k": 1, "rag_retrieval_top_k": 3})
    cfg = resolve_runtime_config(
        config(), {"openrouter_provider_sort": "price", "rag_rerank_timeout_seconds": 2}
    )
    assert cfg.openrouter_provider_sort == "price" and cfg.rag_rerank_timeout_seconds == 2


def test_catalog_is_admin_only_cached_and_filters_models(monkeypatch):
    from backend.app.api import admin
    from backend.app.core import rerank_models
    from backend.app.main import app

    monkeypatch.setattr(admin.settings, "admin_api_key", "synthetic-token")
    monkeypatch.setattr(rerank_models, "_cache", {})
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs)
        return httpx.Response(
            200,
            request=httpx.Request("GET", "https://synthetic.invalid/models"),
            json={
                "data": [
                    {
                        "id": "cohere/rerank-4-fast",
                        "architecture": {"output_modalities": ["rerank"]},
                    },
                    {"id": "chat/model", "architecture": {"output_modalities": ["text"]}},
                ]
            },
        )

    monkeypatch.setattr(rerank_models.httpx, "get", get)
    client = app.test_client()
    assert client.get("/api/v1/admin/rerank-models").status_code == 401
    assert not calls
    for _ in range(2):
        response = client.get(
            "/api/v1/admin/rerank-models", headers={"X-Admin-Token": "synthetic-token"}
        )
        assert response.status_code == 200
        assert response.json["data"] == [
            {"id": "cohere/rerank-4-fast", "name": "cohere/rerank-4-fast"}
        ]
    assert len(calls) == 1 and calls[0]["params"] == {"output_modalities": "rerank"}


@pytest.mark.asyncio
async def test_actual_request_shape_and_auto_routing(monkeypatch):
    calls = []
    original_client = httpx.AsyncClient

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "synthetic/rerank",
                "results": [{"index": i, "relevance_score": 1 - i / 10} for i in [4, 3, 2, 1, 0]],
            },
        )

    monkeypatch.setattr(
        rerank.httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw),
    )
    docs = documents()
    docs[0].content = "長文" * 5000
    ranked, actual = await rerank._request("masked", docs, config(rag_rerank_max_chars_per_doc=100))
    assert ranked == list(reversed(docs)) and actual == "synthetic/rerank"
    assert "provider" not in calls[0] and calls[0]["top_n"] == 5
    assert len(calls[0]["documents"][0]) == 100
    assert docs[0].content == "長文" * 5000
