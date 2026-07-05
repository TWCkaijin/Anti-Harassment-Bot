from firebase_admin import firestore
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger
from backend.app.rag.base import BaseRAG, RAGDocument
from backend.app.rag.embeddings import EmbeddingClient

logger = get_logger(__name__)
settings = get_settings()

COLLECTIONS_BY_DATA_TYPE = {
    "law": [settings.rag_collection_name],
    "judgment": [settings.rag_judgment_collection_name],
    "remedy": [settings.rag_remedy_collection_name],
    "all": [
        settings.rag_collection_name,
        settings.rag_judgment_collection_name,
        settings.rag_remedy_collection_name,
    ],
}


class FirestoreVectorRAG(BaseRAG):
    """
    使用 Firebase Firestore Vector Search 的 RAG 實作。
    """

    def __init__(self):
        self.db = firestore.client()
        self.embedding_client = EmbeddingClient()

    async def _get_embedding(self, text: str) -> list[float] | None:
        """取得查詢向量。"""
        try:
            return await self.embedding_client.embed(text, mode="query")
        except Exception as e:
            logger.warning("Failed to get query embedding: %s", e)
            return None

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        data_type: str = "law",
        collection_name: str | None = None,
    ) -> list[RAGDocument]:
        """
        將查詢字串轉為向量，並在 Firestore 進行相似度檢索。
        """
        query_vector = await self._get_embedding(query)
        if not query_vector:
            return []

        collection_names = (
            [collection_name] if collection_name else COLLECTIONS_BY_DATA_TYPE.get(data_type)
        )
        if not collection_names:
            logger.warning("Unknown RAG data_type=%s; falling back to law collection.", data_type)
            collection_names = COLLECTIONS_BY_DATA_TYPE["law"]

        results: list[RAGDocument] = []
        per_collection_limit = top_k if len(collection_names) == 1 else max(top_k, 1)
        for target_collection in collection_names:
            results.extend(
                self._retrieve_from_collection(
                    collection_name=target_collection,
                    query_vector=query_vector,
                    limit=per_collection_limit,
                )
            )
        return results[:top_k]

    def _retrieve_from_collection(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int,
    ) -> list[RAGDocument]:
        # 使用 find_nearest 進行向量檢索
        # 需在 Firebase Console 中為 embedding 欄位建立 Vector Index
        try:
            vector_query = self.db.collection(collection_name).find_nearest(
                vector_field="embedding",
                query_vector=query_vector,
                distance_measure=DistanceMeasure.COSINE,
                limit=limit,
            )

            docs = vector_query.get()
            results = []
            for doc in docs:
                data = doc.to_dict()
                metadata = dict(data.get("metadata", {}))
                metadata.setdefault("collection", collection_name)
                results.append(
                    RAGDocument(
                        content=data.get("content", ""),
                        metadata=metadata,
                        doc_id=doc.id,
                    )
                )
            return results
        except Exception as e:
            logger.exception(
                "Firestore Vector Search failed for %s: %s "
                "(project=%s, vector_field=embedding, vector_dim=%s)",
                collection_name,
                e,
                getattr(self.db, "project", None),
                len(query_vector),
            )
            return []

    async def add_documents(self, documents: list[RAGDocument]) -> None:
        """新增文件 (此專案通常由 seed script 處理，不透過 API 動態新增)"""
        pass
