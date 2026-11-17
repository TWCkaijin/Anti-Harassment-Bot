"""
性騷擾防治智能 AI — 預設 RAG 實作（記憶體版本）
此為初始佔位實作，使用簡單的關鍵字比對進行文件檢索。
後續可替換為 Vertex AI Vector Search、Pinecone、ChromaDB 等向量資料庫。

TODO: 接入真實的 RAG 資料庫（請提供 RAG 資料來源後更換此實作）
"""

import uuid
from difflib import SequenceMatcher

from rag_core.rag.base import BaseRAG, RAGDocument

# ── 預設內建知識庫（台灣性騷擾防治基礎資訊）────────────────────────────────

_DEFAULT_DOCUMENTS: list[dict] = [
    {
        "content": (
            "依性騷擾防治法第2條，性騷擾須是性侵害犯罪以外、違反他人意願且與性或性別有關的行為。"
            "條文涵蓋造成敵意或冒犯情境，以及將順服或拒絕作為學習、工作等權益條件的情形；"
            "是否符合仍需依個案事實判斷。"
        ),
        "metadata": {
            "source": "性騷擾防治法第2條",
            "type": "law",
            "law_name": "性騷擾防治法",
            "article_number": "2",
            "source_url": "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=D0050074&flno=2",
            "version": "D0050074@2023-08-16",
            "checked_at": "2026-11-10",
        },
    },
    {
        "content": (
            "衛生福利部 113 保護專線全年無休、24 小時提供性騷擾等事件求助諮詢。"
            "若有立即人身安全危險，該官方說明建議先撥 110 報警。"
        ),
        "metadata": {
            "source": "衛生福利部 113 保護專線介紹",
            "type": "resource",
            "source_url": "https://dep.mohw.gov.tw/DOPs/cp-1183-6499-105.html",
            "checked_at": "2026-11-10",
        },
    },
    {
        "content": (
            "一般事件可依行為人身分向所在地主管機關或警察機關詢問受理方式。"
            "職場事件可向雇主申訴；符合條件時也可向地方勞動主管機關申訴。"
            "校園性別事件可向學校或教育主管機關的性平窗口申請調查。"
        ),
        "metadata": {"source": "通報管道指引", "type": "guidance"},
    },
    {
        "content": (
            "依性騷擾防治法第14條，非權勢性騷擾的申訴原則上須在知悉事件後二年內，"
            "且事件發生後未逾五年；權勢性騷擾為知悉後三年內、發生後未逾七年。"
            "事件發生時被害人未成年另有成年後三年的規定；個案須核對事件日期與適用版本。"
        ),
        "metadata": {
            "source": "性騷擾防治法第14條（申訴期間）",
            "type": "law",
            "law_name": "性騷擾防治法",
            "article_number": "14",
            "source_url": "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=D0050074&flno=14",
            "version": "D0050074@2023-08-16",
            "checked_at": "2026-11-10",
        },
    },
    {
        "content": (
            "性別平等工作法第13條要求雇主採取防治性騷擾措施。僱用十人以上未達三十人者，"
            "須訂定並公開揭示申訴管道；三十人以上者，須訂定並公開揭示防治措施、申訴及懲戒規範。"
            "雇主知悉事件時，須依其知悉途徑採取立即有效的糾正與補救措施。"
            "地方主管機關申訴的條件與期限另見同法第32-1條，不能套用性騷擾防治法的期間。"
        ),
        "metadata": {
            "source": "性別平等工作法第13條（雇主防治義務）",
            "type": "law",
            "law_name": "性別平等工作法",
            "article_number": "13",
            "source_url": "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0030014&flno=13",
            "version": "N0030014@2023-08-16",
            "checked_at": "2026-11-10",
        },
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
