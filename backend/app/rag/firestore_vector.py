import asyncio
from math import isfinite
from time import monotonic

from firebase_admin import firestore
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

from backend.app.core.logger import get_logger
from backend.app.rag.base import (
    BaseRAG,
    RAGDocument,
    RAGEmbeddingError,
    RAGVectorSearchError,
)
from backend.app.rag.embeddings import EmbeddingClient
from backend.app.rag.legal_lookup import legacy_conflict_reason, legal_references, lookup_key

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
        diagnostics: dict | None = None,
    ) -> list[RAGDocument]:
        """
        將查詢字串轉為向量，並在 Firestore 進行相似度檢索。
        """
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        if distance_threshold is not None and (
            isinstance(distance_threshold, bool)
            or not isinstance(distance_threshold, (int, float))
            or not isfinite(distance_threshold)
            or not 0 <= distance_threshold <= 2
        ):
            raise ValueError("Cosine distance threshold must be between 0 and 2")
        references = legal_references(query)
        law_collection = (collection_names_by_data_type or {}).get("law", "rag_documents")
        law_requested = (
            "law" in selected_data_types
            if selected_data_types is not None
            else data_type in {"law", "all"}
        )
        exact_results = []
        if law_requested and references:
            exact_started = monotonic()
            exact_results = await asyncio.to_thread(
                self._retrieve_exact_articles,
                collection_name or law_collection,
                [(law, number) for law, number in references if number],
                top_k,
            )
            if timings is not None:
                timings["exact_lookup_ms"] = (monotonic() - exact_started) * 1000
        if diagnostics is not None:
            diagnostics.update(
                exact_match_count=len(exact_results),
                requested_top_k=top_k,
                distance_threshold=distance_threshold,
            )

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
        # A named law narrows an additional vector query; an arbitrary first
        # article from that law is not a relevant match. Firestore requires the
        # documented law_name + embedding composite vector index for this path.
        if law_requested and references:
            named_results = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        self._retrieve_from_collection,
                        collection_name=collection_name or law_collection,
                        query_vector=query_vector,
                        limit=top_k,
                        distance_threshold=distance_threshold,
                        law_name=law,
                    )
                    for law in dict.fromkeys(law for law, _ in references)
                ),
                return_exceptions=True,
            )
            for items in named_results:
                if (
                    isinstance(items, RAGVectorSearchError)
                    and isinstance(items.__cause__, FailedPrecondition)
                    and "index" in str(items.__cause__).lower()
                ):
                    # During a staged corpus rollout the existing vector query
                    # and exact lookup remain usable before the new index exists.
                    if diagnostics is not None:
                        diagnostics["law_name_index_status"] = "missing_index"
                    continue
                if isinstance(items, BaseException):
                    raise items
                exact_results.extend(items)
        if timings is not None:
            timings["vector_search_ms"] = (
                timings.get("vector_search_ms", 0.0) + (monotonic() - search_started) * 1000
            )
        if preserve_data_types and len(collection_names) > 1:
            # Reserve one result from each requested type before filling the budget.
            # A mixed request cannot lose all statutory evidence to similar judgments.
            results_by_collection = [
                [*[doc for doc in exact_results if doc.metadata.get("collection") == name], *items]
                for name, items in zip(collection_names, results_by_collection, strict=True)
            ]
            ordered = []
            for rank in range(max((len(items) for items in results_by_collection), default=0)):
                for items in results_by_collection:
                    if rank < len(items):
                        ordered.append(items[rank])
            return self._merge_results([], ordered, max(top_k, len(collection_names)), diagnostics)
        if len(results_by_collection) == 1:
            return self._merge_results(exact_results, results_by_collection[0], top_k, diagnostics)

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
        return self._merge_results(exact_results, results, top_k, diagnostics)

    @staticmethod
    def _merge_results(exact, vector, top_k, diagnostics):
        results = []
        seen = set()
        observations = []
        for doc in [*exact, *vector]:
            key = (doc.metadata.get("collection"), doc.doc_id)
            reason = None
            if doc.metadata.get("corpus_active") is False:
                reason = "inactive_or_not_effective"
            elif conflict := legacy_conflict_reason(doc.content, doc.metadata):
                reason = conflict
            elif doc.doc_id and key in seen:
                reason = "duplicate"
            elif len(results) >= top_k:
                reason = "top_k_limit"
            if reason is None:
                results.append(doc)
                if doc.doc_id:
                    seen.add(key)
            observations.append(
                {
                    "doc_id": doc.doc_id,
                    "collection": key[0],
                    "distance": doc.metadata.get("distance"),
                    "method": doc.metadata.get("retrieval_method", "vector"),
                    "selected": reason is None,
                    "filter_reason": reason,
                }
            )
        if diagnostics is not None:
            diagnostics.update(results=observations, selected_count=len(results))
        return results

    def _retrieve_exact_articles(self, collection_name, references, limit):
        """Exact citations query indexed metadata in Firestore, never the public web."""
        results = []
        for law, number in references[:limit]:
            try:
                docs = (
                    self.db.collection(collection_name)
                    .where(
                        filter=FieldFilter(
                            "metadata.lookup_keys", "array_contains", lookup_key(law, number)
                        )
                    )
                    .limit(limit)
                    .get()
                )
                for doc in docs:
                    data = doc.to_dict()
                    metadata = dict(data.get("metadata") or {})
                    metadata.update(collection=collection_name, retrieval_method="exact")
                    metadata.pop("distance", None)
                    results.append(RAGDocument(data.get("content", ""), metadata, doc_id=doc.id))
            except Exception as exc:
                logger.error(
                    "Exact legal lookup failed collection=%s error_type=%s",
                    collection_name,
                    type(exc).__name__,
                )
                raise RAGVectorSearchError("Exact legal lookup is unavailable") from exc
        return results

    def _retrieve_from_collection(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int,
        distance_threshold: float | None = None,
        law_name: str | None = None,
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
            collection_query = self.db.collection(collection_name)
            if law_name:
                collection_query = collection_query.where(
                    filter=FieldFilter("metadata.law_name", "==", law_name)
                )
            vector_query = collection_query.find_nearest(**query_options)

            docs = vector_query.get()
            results = []
            for doc in docs:
                data = doc.to_dict()
                metadata = dict(data.get("metadata", {}))
                metadata.setdefault("collection", collection_name)
                metadata["retrieval_method"] = "law_name_vector" if law_name else "vector"
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
