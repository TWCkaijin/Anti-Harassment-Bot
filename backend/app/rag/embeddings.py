"""Embedding helpers shared by RAG retrieval and ingestion scripts."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from openai import AsyncOpenAI

from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

EmbeddingMode = Literal["query", "passage"]


def _e5_prefix(text: str, mode: EmbeddingMode) -> str:
    """E5 models expect query/passsage prefixes for best retrieval quality."""
    prefix = "query" if mode == "query" else "passage"
    stripped = " ".join(text.split())
    return f"{prefix}: {stripped}"


class EmbeddingClient:
    """Create embeddings using the configured provider."""

    def __init__(self) -> None:
        self.provider = settings.embedding_provider
        self.model = settings.embedding_model
        self._openrouter_client = AsyncOpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            timeout=settings.openrouter_request_timeout_seconds,
        )

    async def embed(self, text: str, mode: EmbeddingMode) -> list[float]:
        if self.provider == "local":
            return self._embed_local(text, mode)
        return await self._embed_openrouter(text, mode)

    async def _embed_openrouter(self, text: str, mode: EmbeddingMode) -> list[float]:
        response = await self._openrouter_client.embeddings.create(
            input=_e5_prefix(text, mode),
            model=self.model,
        )
        return response.data[0].embedding

    def _embed_local(self, text: str, mode: EmbeddingMode) -> list[float]:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "embedding_provider=local requires sentence-transformers. "
                "Install it before running local ingestion."
            ) from exc

        model = _load_sentence_transformer(self.model, SentenceTransformer)
        vector = model.encode(_e5_prefix(text, mode), normalize_embeddings=True)
        return [float(value) for value in vector.tolist()]


@lru_cache
def _load_sentence_transformer(model_name: str, sentence_transformer_cls: type) -> Any:
    return sentence_transformer_cls(model_name)
