"""Small server-side catalog cache; never part of the chat critical path."""

from threading import Lock
from time import monotonic

import httpx

from rag_core.core.config import get_settings

_cache = {}
_lock = Lock()


def list_rerank_models():
    settings = get_settings()
    base = settings.openrouter_base_url.rstrip("/")
    with _lock:
        cached = _cache.get(base)
        if cached and monotonic() - cached[0] < 600:
            return cached[1]
        response = httpx.get(
            f"{base}/models",
            params={"output_modalities": "rerank"},
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            timeout=5,
        )
        response.raise_for_status()
        models = [
            {"id": m["id"], "name": m.get("name", m["id"])}
            for m in response.json()["data"]
            if isinstance(m.get("id"), str)
            and "rerank" in m.get("architecture", {}).get("output_modalities", [])
        ]
        _cache[base] = (monotonic(), models)
        return models
