import asyncio
import os
import sys
from pathlib import Path

# 將專案根目錄加入 PYTHONPATH
sys.path.append(str(Path(__file__).parent.parent.parent))

from firebase_admin import credentials, firestore, initialize_app
from google.cloud.firestore_v1.vector import Vector
from openai import AsyncOpenAI

from backend.app.core.config import get_settings
from backend.app.rag.default_rag import _DEFAULT_DOCUMENTS

settings = get_settings()

# 初始化 Firebase
cred_path = str(settings.firebase_admin_credential_path)
if os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)
    initialize_app(cred)
else:
    initialize_app()

db = firestore.client()

# 初始化 OpenRouter API (OpenAI SDK)
client = AsyncOpenAI(
    base_url=settings.openrouter_base_url,
    api_key=settings.openrouter_api_key,
    timeout=settings.openrouter_request_timeout_seconds,
)


async def get_embedding(text: str) -> list[float]:
    """使用 OpenRouter/OpenAI API 取得向量。"""
    try:
        response = await client.embeddings.create(
            input=text,
            model=settings.openrouter_embedding_model,
        )
        return response.data[0].embedding
    except Exception as e:
        raise RuntimeError(f"Error getting embedding: {e}") from e


async def seed_documents():
    print("開始匯入資料至 Firestore...")
    collection_ref = db.collection(settings.rag_collection_name)

    for i, doc in enumerate(_DEFAULT_DOCUMENTS):
        content = doc["content"]
        metadata = doc["metadata"]

        print(f"處理第 {i + 1} 筆資料: {metadata['source']}")
        embedding = await get_embedding(content)

        doc_ref = collection_ref.document()
        doc_ref.set(
            {
                "content": content,
                "metadata": metadata,
                # 使用 Firestore 支援的 Vector 型態
                "embedding": Vector(embedding),
            }
        )
        print(f"寫入成功: {doc_ref.id}")

    print("匯入完成！請記得在 Firebase Console 建立 Vector Index。")


if __name__ == "__main__":
    asyncio.run(seed_documents())
