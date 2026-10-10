"""Bounded, optional OpenRouter reranking shared by every agent retrieval path."""

import asyncio
from math import isfinite
from time import monotonic

import httpx

from rag_core.core.config import get_settings


def apply_provider_sort(options, config):
    """Require schema-capable routing and preserve reasoning/provider preferences."""
    if options.get("response_format", {}).get("type") == "json_schema":
        options.setdefault("extra_body", {}).setdefault("provider", {})["require_parameters"] = True
    if config.openrouter_provider_sort != "auto":
        extra = options.setdefault("extra_body", {})
        extra.setdefault("provider", {})["sort"] = config.openrouter_provider_sort
    return options


def _key(doc):
    return doc.metadata.get("collection"), doc.doc_id


def _select(candidates, ranked, limit, preserve):
    """Reserve exact articles and collection coverage within the final budget."""
    collections = list(dict.fromkeys(d.metadata.get("collection") for d in candidates))
    limit = max(limit, len(collections)) if preserve else limit
    chosen = []
    seen = set()

    def add(doc):
        if _key(doc) not in seen and len(chosen) < limit:
            chosen.append(doc)
            seen.add(_key(doc))

    # First reserve one per collection, preferring exact matches for that source type.
    if preserve:
        for collection in collections:
            group = [d for d in ranked if d.metadata.get("collection") == collection]
            add(next((d for d in group if d.metadata.get("retrieval_method") == "exact"), group[0]))
    for doc in candidates:
        if doc.metadata.get("retrieval_method") == "exact":
            add(doc)
    for doc in ranked:
        add(doc)
    # Return the selected documents in their ranking order; exact articles go first.
    return sorted(
        chosen, key=lambda d: (d.metadata.get("retrieval_method") != "exact", ranked.index(d))
    )


def _excerpt(doc, limit):
    metadata = doc.metadata
    heading = " | ".join(
        str(metadata[k])
        for k in ("law_name", "article_number", "title", "source")
        if metadata.get(k)
    )
    return f"{heading}\n{doc.content}"[:limit]


async def _request(query, candidates, config):
    settings = get_settings()
    payload = {
        "model": config.rag_rerank_model,
        "query": query,
        "documents": [_excerpt(d, config.rag_rerank_max_chars_per_doc) for d in candidates],
        # Score the whole pool so mandatory source coverage can be enforced locally.
        "top_n": len(candidates),
    }
    if config.openrouter_provider_sort != "auto":
        payload["provider"] = {"sort": config.openrouter_provider_sort}
    # Request-scoped client: Firebase WSGI creates separate event loops per request.
    async with httpx.AsyncClient(timeout=config.rag_rerank_timeout_seconds) as client:
        response = await client.post(
            f"{settings.openrouter_base_url.rstrip('/')}/rerank",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    results = body["results"]
    if not isinstance(results, list) or len(results) != len(candidates):
        raise ValueError("incomplete_ranking")
    indices = []
    for result in results:
        index, score = result["index"], result["relevance_score"]
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(candidates)
        ):
            raise ValueError("invalid_index")
        if isinstance(score, bool) or not isinstance(score, (float, int)) or not isfinite(score):
            raise ValueError("invalid_score")
        indices.append(index)
    if len(set(indices)) != len(indices):
        raise ValueError("duplicate_index")
    ranked = [candidates[i] for i in indices]
    return ranked, body.get("model", config.rag_rerank_model)


async def retrieve_with_rerank(rag, query, config, **kwargs):
    """One retrieval, at most one rerank, and no model or retrieval retry on failure."""
    limit = kwargs.pop("top_k", config.rag_retrieval_top_k)
    metrics = kwargs.get("timings")
    diagnostics = kwargs.get("diagnostics")
    enabled = config.rag_rerank_enabled
    started = monotonic()
    candidates = await rag.retrieve(
        query, top_k=max(limit, config.rag_rerank_candidate_k) if enabled else limit, **kwargs
    )
    if metrics is not None:
        metrics["candidate_retrieval_ms"] = (
            metrics.get("candidate_retrieval_ms", 0) + (monotonic() - started) * 1000
        )
    info = {
        "status": "disabled",
        "model": config.rag_rerank_model,
        "candidate_count": len(candidates),
        "retained_count": len(candidates),
        "elapsed_ms": 0.0,
    }
    if diagnostics is not None:
        diagnostics["rerank"] = info
    if not enabled:
        return candidates
    preserve = kwargs.get("preserve_data_types", False)
    final_limit = (
        max(limit, len({d.metadata.get("collection") for d in candidates})) if preserve else limit
    )
    if len(candidates) <= final_limit:
        info["status"] = "skipped_small_pool"
        return candidates
    started = monotonic()
    try:
        # Bounds the entire exchange, rather than giving each network phase one second.
        async with asyncio.timeout(config.rag_rerank_timeout_seconds):
            ranked, actual_model = await _request(query, candidates, config)
        selected = _select(candidates, ranked, limit, preserve)
        info.update(status="ok", model=actual_model)
    except TimeoutError:
        selected = candidates[:final_limit]
        info["status"] = "timeout"
    except Exception as exc:
        selected = candidates[:final_limit]
        info.update(status="failed", failure_type=type(exc).__name__)
    finally:
        elapsed = (monotonic() - started) * 1000
        info["elapsed_ms"] = elapsed
        if metrics is not None:
            metrics["rerank_ms"] = metrics.get("rerank_ms", 0) + elapsed
    info["retained_count"] = len(selected)
    return selected
