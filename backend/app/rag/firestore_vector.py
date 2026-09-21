from math import isfinite

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
            logger.exception("Failed to get query embedding")
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
    ) -> list[RAGDocument]:
        """
        將查詢字串轉為向量，並在 Firestore 進行相似度檢索。
        """
        query_vector = await self._get_embedding(query)
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
        if not collection_names:
            logger.warning("Unknown RAG data_type=%s; falling back to law collection.", data_type)
            collection_names = collections_by_data_type["law"]

        results_by_collection: list[list[RAGDocument]] = []
        per_collection_limit = top_k if len(collection_names) == 1 else max(top_k, 1)
        for target_collection in collection_names:
            results_by_collection.append(
                self._retrieve_from_collection(
                    collection_name=target_collection,
                    query_vector=query_vector,
                    limit=per_collection_limit,
                    distance_threshold=distance_threshold,
                )
            )
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
            logger.exception(
                "Firestore Vector Search failed for %s: %s "
                "(project=%s, vector_field=embedding, vector_dim=%s)",
                collection_name,
                exc,
                getattr(self.db, "project", None),
                len(query_vector),
            )
            raise RAGVectorSearchError(
                f"Vector search is unavailable for collection {collection_name}"
            ) from exc

    async def add_documents(self, documents: list[RAGDocument]) -> None:
        """新增文件 (此專案通常由 seed script 處理，不透過 API 動態新增)"""
        pass
