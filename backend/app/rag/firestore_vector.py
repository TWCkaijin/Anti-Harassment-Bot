from firebase_admin import firestore
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from openai import AsyncOpenAI

from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger
from backend.app.rag.base import BaseRAG, RAGDocument

logger = get_logger(__name__)
settings = get_settings()

client = AsyncOpenAI(
    base_url=settings.openrouter_base_url,
    api_key=settings.openrouter_api_key,
    timeout=settings.openrouter_request_timeout_seconds,
)


class FirestoreVectorRAG(BaseRAG):
    """
    使用 Firebase Firestore Vector Search 的 RAG 實作。
    """

    def __init__(self):
        self.db = firestore.client()
        self.collection = self.db.collection(settings.rag_collection_name)

    async def _get_embedding(self, text: str) -> list[float] | None:
        """使用 OpenRouter 取得字串的 Embedding。"""
        try:
            response = await client.embeddings.create(
                input=text,
                model=settings.openrouter_embedding_model,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning("Failed to get embedding from OpenRouter: %s", e)
            return None

    async def retrieve(self, query: str, top_k: int = 5) -> list[RAGDocument]:
        """
        將查詢字串轉為向量，並在 Firestore 進行相似度檢索。
        """
        query_vector = await self._get_embedding(query)
        if not query_vector:
            return []

        # 使用 find_nearest 進行向量檢索
        # 需在 Firebase Console 中為 embedding 欄位建立 Vector Index
        try:
            vector_query = self.collection.find_nearest(
                vector_field="embedding",
                query_vector=query_vector,
                distance_measure=DistanceMeasure.COSINE,
                limit=top_k,
            )

            docs = vector_query.get()
            results = []
            for doc in docs:
                data = doc.to_dict()
                results.append(
                    RAGDocument(
                        content=data.get("content", ""),
                        metadata=data.get("metadata", {}),
                        doc_id=doc.id,
                    )
                )
            return results
        except Exception as e:
            logger.exception("Firestore Vector Search failed: %s", e)
            return []

    async def add_documents(self, documents: list[RAGDocument]) -> None:
        """新增文件 (此專案通常由 seed script 處理，不透過 API 動態新增)"""
        pass
