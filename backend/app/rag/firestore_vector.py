import asyncio
from math import isfinite
from time import monotonic

from firebase_admin import firestore
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

from backend.app.core.logger import get_logger
from backend.app.rag.base import (
    BaseRAG,
    RAGDocument,
    RAGEmbeddingError,
    RAGVectorSearchError,
)
from backend.app.rag.embeddings import EmbeddingClient

logger = get_logger(__name__)


class FirestoreVectorRAG(BaseRAG):
    """
    使用 Firebase Firestore Vector Search 的 RAG 實作。
    """

    def __init__(self):
        self.db = firestore.client()
        self.embedding_client = EmbeddingClient()

    async def _get_embedding(self, text: str) -> list[float]:
        """取得查詢向量。"""
        try:
            embedding = await self.embedding_client.embed(text, mode="query")
        except Exception as exc:
            # Provider exception bodies can echo the query, which may contain
            # request-local facts. Keep only the error category in this layer.
            logger.error("Failed to get query embedding error_type=%s", type(exc).__name__)
            raise RAGEmbeddingError("Query embedding service is unavailable") from exc
        if not embedding:
            raise RAGEmbeddingError("Query embedding service returned an empty vector")
        return embedding

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        data_type: str = "law",
        collection_name: str | None = None,
        collection_names_by_data_type: dict[str, str] | None = None,
        distance_threshold: float | None = None,
        selected_data_types: list[str] | None = None,
        preserve_data_types: bool = False,
        timings: dict | None = None,
    ) -> list[RAGDocument]:
        """
        將查詢字串轉為向量，並在 Firestore 進行相似度檢索。
        """
        embedding_started = monotonic()
        query_vector = await self._get_embedding(query)
        if timings is not None:
            timings["embedding_ms"] = (
                timings.get("embedding_ms", 0.0) + (monotonic() - embedding_started) * 1000
            )
        if not query_vector:
            raise RAGEmbeddingError("Query embedding service returned an empty vector")

        configured_collections = collection_names_by_data_type or {}
        collections_by_data_type = {
            "law": [configured_collections.get("law", "rag_documents")],
            "judgment": [configured_collections.get("judgment", "rag_judgments")],
            "remedy": [configured_collections.get("remedy", "rag_remedies")],
            "all": [
                configured_collections.get("law", "rag_documents"),
                configured_collections.get("judgment", "rag_judgments"),
                configured_collections.get("remedy", "rag_remedies"),
            ],
        }
        collection_names = (
            [collection_name] if collection_name else collections_by_data_type.get(data_type)
        )
        if selected_data_types is not None:
            if not selected_data_types or any(
                kind not in {"law", "judgment", "remedy"} for kind in selected_data_types
            ):
                raise ValueError("Unsupported retrieval data types")
            collection_names = list(
                dict.fromkeys(collections_by_data_type[kind][0] for kind in selected_data_types)
            )
        if not collection_names:
            logger.warning("Unknown RAG data_type=%s; falling back to law collection.", data_type)
            collection_names = collections_by_data_type["law"]

        per_collection_limit = top_k if len(collection_names) == 1 else max(top_k, 1)
        # Firestore's synchronous client must not block SSE heartbeats. These
        # independent reads share the same embedding and can run concurrently;
        # gather preserves collection order for stable ranking tie-breaks.
        search_started = monotonic()
        results_by_collection = await asyncio.gather(
            *(
                asyncio.to_thread(
                    self._retrieve_from_collection,
                    collection_name=target_collection,
                    query_vector=query_vector,
                    limit=per_collection_limit,
                    distance_threshold=distance_threshold,
                )
                for target_collection in collection_names
            )
        )
        if timings is not None:
            timings["vector_search_ms"] = (
                timings.get("vector_search_ms", 0.0) + (monotonic() - search_started) * 1000
            )
        if preserve_data_types and len(collection_names) > 1:
            # Reserve one result from each requested type before filling the budget.
            # A mixed request cannot lose all statutory evidence to similar judgments.
            ordered = []
            for rank in range(max((len(items) for items in results_by_collection), default=0)):
                for items in results_by_collection:
                    if rank < len(items):
                        ordered.append(items[rank])
            return ordered[: max(top_k, len(collection_names))]
        if len(results_by_collection) == 1:
            return results_by_collection[0][:top_k]

        # Firestore cosine distance is lower-is-better. Merge cross-collection
        # results globally when distance is available; keep a stable round-robin
        # fallback for legacy documents that do not expose a distance field.
        ranked_results: list[tuple[float, int, int, RAGDocument]] = []
        fallback_by_collection: list[list[RAGDocument]] = []
        for collection_index, collection_results in enumerate(results_by_collection):
            fallback_items: list[RAGDocument] = []
            for result_index, document in enumerate(collection_results):
                distance = document.metadata.get("distance")
                if (
                    isinstance(distance, (int, float))
                    and not isinstance(distance, bool)
                    and isfinite(float(distance))
                ):
                    ranked_results.append(
                        (float(distance), collection_index, result_index, document)
                    )
                else:
                    fallback_items.append(document)
            fallback_by_collection.append(fallback_items)

        ranked_results.sort(key=lambda item: (item[0], item[1], item[2]))
        results = [item[3] for item in ranked_results]
        longest_fallback = max((len(items) for items in fallback_by_collection), default=0)
        for index in range(longest_fallback):
            for fallback_items in fallback_by_collection:
                if index < len(fallback_items):
                    results.append(fallback_items[index])
        return results[:top_k]

    def _retrieve_from_collection(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int,
        distance_threshold: float | None = None,
    ) -> list[RAGDocument]:
        # 使用 find_nearest 進行向量檢索
        # 需在 Firebase Console 中為 embedding 欄位建立 Vector Index
        try:
            query_options = {
                "vector_field": "embedding",
                "query_vector": query_vector,
                "distance_measure": DistanceMeasure.COSINE,
                "limit": limit,
                "distance_result_field": "vector_distance",
            }
            if distance_threshold is not None:
                query_options["distance_threshold"] = distance_threshold
            vector_query = self.db.collection(collection_name).find_nearest(**query_options)

            docs = vector_query.get()
            results = []
            for doc in docs:
                data = doc.to_dict()
                metadata = dict(data.get("metadata", {}))
                metadata.setdefault("collection", collection_name)
                # Never trust a document-authored value as the vector query score.
                metadata.pop("distance", None)
                distance = data.get("vector_distance")
                if (
                    isinstance(distance, (int, float))
                    and not isinstance(distance, bool)
                    and isfinite(float(distance))
                ):
                    metadata["distance"] = float(distance)
                    logger.info(
                        "RAG vector result collection=%s doc_id=%s distance=%.6f",
                        collection_name,
                        doc.id,
                        distance,
                    )
                results.append(
                    RAGDocument(
                        content=data.get("content", ""),
                        metadata=metadata,
                        doc_id=doc.id,
                    )
                )
            return results
        except Exception as exc:
            logger.error(
                "Firestore Vector Search failed for %s: error_type=%s "
                "(project=%s, vector_field=embedding, vector_dim=%s)",
                collection_name,
                type(exc).__name__,
                getattr(self.db, "project", None),
                len(query_vector),
            )
            raise RAGVectorSearchError(
                f"Vector search is unavailable for collection {collection_name}"
            ) from exc

    async def add_documents(self, documents: list[RAGDocument]) -> None:
        """新增文件 (此專案通常由 seed script 處理，不透過 API 動態新增)"""
        pass
