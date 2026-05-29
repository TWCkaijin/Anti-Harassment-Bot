"""
性騷擾防治智能 AI — 抽象 RAG 基底類別
所有 RAG（Retrieval-Augmented Generation）實作都應繼承此類別。

使用方式：
    class MyRAG(BaseRAG):
        async def retrieve(self, query: str, top_k: int = 5) -> list[RAGDocument]:
            # 實作向量資料庫查詢邏輯
            ...

        async def add_documents(self, documents: list[RAGDocument]) -> None:
            # 實作文件新增邏輯
            ...
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RAGDocument:
    """
    RAG 文件資料結構。

    Attributes:
        content: 文件的文字內容
        metadata: 附加元資料（來源、類型、法條編號等）
        score: 相關性分數（0.0 ~ 1.0，檢索後填入）
        doc_id: 文件唯一識別碼
    """

    content: str
    metadata: dict = field(default_factory=dict)
    score: float = 0.0
    doc_id: str = ""

    def to_context_string(self) -> str:
        """將文件格式化為注入 Prompt 的上下文字串。"""
        source = self.metadata.get("source", "未知來源")
        return f"[參考資料 - {source}]\n{self.content}"


class BaseRAG(ABC):
    """
    所有 RAG 實作的抽象基底類別。
    定義統一介面，讓 Agent 可以透過相同方式呼叫不同的 RAG 後端。
    """

    @abstractmethod
    async def retrieve(
        self, query: str, top_k: int = 5
    ) -> list[RAGDocument]:
        """
        根據查詢字串檢索最相關的文件。

        Args:
            query: 使用者的查詢（已完成匿名化）
            top_k: 最多回傳的文件數量

        Returns:
            依相關性排序的 RAGDocument 清單
        """
        ...

    @abstractmethod
    async def add_documents(self, documents: list[RAGDocument]) -> None:
        """
        新增文件到 RAG 資料庫。

        Args:
            documents: 要新增的文件清單
        """
        ...

    async def retrieve_as_context(
        self, query: str, top_k: int = 5
    ) -> str:
        """
        檢索文件並格式化為可直接注入 Prompt 的上下文字串。
        預設使用 `retrieve()` 的結果，子類別可覆寫以客製化格式。

        Args:
            query: 使用者的查詢
            top_k: 最多回傳的文件數量

        Returns:
            格式化的上下文字串，若無結果則回傳空字串
        """
        docs = await self.retrieve(query, top_k=top_k)
        if not docs:
            return ""
        context_parts = [doc.to_context_string() for doc in docs]
        return "\n\n---\n\n".join(context_parts)

    def get_info(self) -> dict:
        """回傳此 RAG 實作的基本資訊。"""
        return {
            "type": self.__class__.__name__,
            "description": self.__class__.__doc__ or "",
        }
