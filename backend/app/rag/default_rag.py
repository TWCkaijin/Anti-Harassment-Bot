"""
性騷擾防治智能 AI — 預設 RAG 實作（記憶體版本）
此為初始佔位實作，使用簡單的關鍵字比對進行文件檢索。
後續可替換為 Vertex AI Vector Search、Pinecone、ChromaDB 等向量資料庫。

TODO: 接入真實的 RAG 資料庫（請提供 RAG 資料來源後更換此實作）
"""

import uuid
from difflib import SequenceMatcher

from backend.app.rag.base import BaseRAG, RAGDocument

# ── 預設內建知識庫（台灣性騷擾防治基礎資訊）────────────────────────────────

_DEFAULT_DOCUMENTS: list[dict] = [
    {
        "content": (
            "性騷擾防治法第2條定義：本法所稱性騷擾，係指性侵害犯罪以外，"
            "對他人實施違反其意願而與性或性別有關之行為，且有下列情形之一者："
            "（一）以該他人順服或拒絕該行為，作為其獲得、喪失或減損與工作、教育、"
            "訓練、服務、計畫、活動有關權益之條件。"
            "（二）以展示或播送文字、圖畫、聲音、影像或其他物品之方式，或以歧視、"
            "侮辱之言行，或以他法，而有損害他人人格尊嚴，或造成使人心生畏怖、"
            "感受敵意或冒犯之情境，或不當影響其工作、教育、訓練、服務、計畫、"
            "活動或正常生活之進行。"
        ),
        "metadata": {"source": "性騷擾防治法第2條", "type": "law"},
    },
    {
        "content": (
            "若遭受性騷擾，可依以下管道尋求協助："
            "1. 撥打 113 婦幼保護專線（24小時）"
            "2. 撥打 110 報警"
            "3. 向所在縣市政府社會局申訴"
            "4. 若在職場，可向公司性騷擾申訴委員會申訴"
            "5. 若在學校，可向學校性別平等教育委員會申訴"
            "6. 聯繫現代婦女基金會：02-2391-7133"
        ),
        "metadata": {"source": "通報管道指引", "type": "resource"},
    },
    {
        "content": (
            "性騷擾申訴時效：依性騷擾防治法第13條，"
            "性騷擾事件被害人除可依相關法律請求協助外，"
            "並得於事件發生後一年內，向加害人所屬機關（構）、部隊、學校、機構、"
            "僱用人或直轄市、縣（市）主管機關提出申訴。"
            "注意：時效為「一年」，請盡早提出申訴。"
        ),
        "metadata": {"source": "性騷擾防治法第13條（申訴時效）", "type": "law"},
    },
    {
        "content": (
            "性別工作平等法第13條規定，僱用受僱者30人以上之雇主，"
            "應訂定性騷擾防治措施、申訴及懲戒辦法，並在工作場所公開揭示。"
            "若雇主未依規定處理，受僱者可向地方主管機關（勞動局）申訴。"
        ),
        "metadata": {"source": "性別工作平等法第13條（職場義務）", "type": "law"},
    },
    {
        "content": (
            "遭受性騷擾後的自我保護步驟："
            "1. 保留證據：截圖、錄音、保留實物（若安全可行）"
            "2. 記錄細節：事件發生的時間、地點、人物、過程"
            "3. 告知信任的人：家人、朋友或信任的同事"
            "4. 尋求心理支持：創傷反應是正常的，不是你的錯"
            "5. 評估是否提出申訴：申訴是你的權利，非義務"
        ),
        "metadata": {"source": "性騷擾應對指引", "type": "guidance"},
    },
]


class DefaultRAG(BaseRAG):
    """
    預設 RAG 實作（記憶體關鍵字比對版本）。
    適合初始開發階段使用，後續請替換為向量資料庫實作。
    """

    def __init__(self) -> None:
        self._documents: list[RAGDocument] = []
        # 載入預設內建文件
        for _, doc_data in enumerate(_DEFAULT_DOCUMENTS):
            self._documents.append(
                RAGDocument(
                    content=doc_data["content"],
                    metadata=doc_data["metadata"],
                    doc_id=str(uuid.uuid4()),
                )
            )

    async def retrieve(self, query: str, top_k: int = 5) -> list[RAGDocument]:
        """使用 SequenceMatcher 進行簡易關鍵字相似度比對（支援中英文）。"""
        if not self._documents:
            return []

        scored: list[RAGDocument] = []
        query_lower = query.lower()

        for doc in self._documents:
            content_lower = doc.content.lower()

            # 支援中文：取 2~4 字元的 n-gram 進行子字串比對
            ngram_hits = 0
            ngram_total = 0
            for n in (2, 3, 4):
                for i in range(len(query_lower) - n + 1):
                    gram = query_lower[i : i + n]
                    ngram_total += 1
                    if gram in content_lower:
                        ngram_hits += 1
            keyword_score = ngram_hits / max(ngram_total, 1)

            similarity = SequenceMatcher(None, query_lower, content_lower[:200]).ratio()

            combined_score = keyword_score * 0.7 + similarity * 0.3
            scored.append(
                RAGDocument(
                    content=doc.content,
                    metadata=doc.metadata,
                    score=combined_score,
                    doc_id=doc.doc_id,
                )
            )

        scored.sort(key=lambda d: d.score, reverse=True)
        return [d for d in scored[:top_k] if d.score > 0.05]

    async def add_documents(self, documents: list[RAGDocument]) -> None:
        """新增文件到記憶體資料庫。"""
        for doc in documents:
            if not doc.doc_id:
                doc.doc_id = str(uuid.uuid4())
            self._documents.append(doc)

    def document_count(self) -> int:
        """回傳目前資料庫中的文件數量。"""
        return len(self._documents)
