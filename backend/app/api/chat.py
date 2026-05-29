"""
性騷擾防治智能 AI — Chat API Router
接收前端對話請求，執行匿名化後透過 Google ADK 呼叫 AI 模型。
"""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.agents.harass_agent import HarassmentCounselingAgent
from backend.app.core.anonymizer import anonymize, anonymize_messages
from backend.app.core.config import Settings, get_settings
from backend.app.core.logger import get_logger
from backend.app.rag.default_rag import DefaultRAG

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["Chat"])

# ── 依賴注入（Singleton per process）────────────────────────────────────────

_agent_instance: HarassmentCounselingAgent | None = None
_rag_instance: DefaultRAG | None = None


def get_agent() -> HarassmentCounselingAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = HarassmentCounselingAgent()
    return _agent_instance


def get_rag() -> DefaultRAG:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = DefaultRAG()
    return _rag_instance


# ── 請求 / 回應模型 ─────────────────────────────────────────────────────────


class MessageItem(BaseModel):
    """單一訊息項目。"""

    role: str = Field(..., pattern="^(user|assistant)$", description="發訊者角色")
    content: str = Field(..., min_length=1, max_length=4000, description="訊息內容")


class ChatRequest(BaseModel):
    """聊天請求：包含當前訊息及完整對話歷史。"""

    message: str = Field(..., min_length=1, max_length=2000, description="當前使用者訊息")
    history: list[MessageItem] = Field(
        default_factory=list,
        max_length=50,
        description="對話歷史（最多 50 輪，由前端 localStorage 傳入）",
    )
    use_rag: bool = Field(default=True, description="是否啟用 RAG 檢索增強")
    image_base64: str | None = Field(default=None, description="使用者上傳的圖片 (base64 data URL)")


class RagInfo(BaseModel):
    status: bool = Field(..., description="是否檢閱資料庫")
    sources: list[str] = Field(default_factory=list, description="檢閱到的條文名稱列表")


class ChatResponse(BaseModel):
    """聊天回應。"""

    reply: str = Field(..., description="AI 回覆內容")
    session_id: str = Field(..., description="本次會話 ID")
    anonymized: bool = Field(..., description="是否有 PII 被匿名化")
    rag_used: RagInfo = Field(..., description="RAG 檢索狀態與來源")
    emotion: str | None = Field(default=None, description="模型判斷的使用者當前情緒")
    emotion_color: str | None = Field(default=None, description="模型判斷的對應情緒顏色")


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=ChatResponse,
    summary="發送對話訊息",
    description=(
        "接收使用者訊息與對話歷史，"
        "執行 PII 匿名化後透過 Google ADK 呼叫 AI，回傳回覆。"
        "後端不儲存任何對話紀錄。"
    ),
)
async def chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),  # noqa: B008
    agent: HarassmentCounselingAgent = Depends(get_agent),  # noqa: B008
    rag: DefaultRAG = Depends(get_rag),  # noqa: B008
) -> ChatResponse:
    # 1. 匿名化當前訊息
    anon_result = anonymize(request.message)
    anonymized_message = anon_result.anonymized
    was_anonymized = anon_result.was_modified

    # 2. 匿名化歷史訊息（批次處理）
    history_dicts = [{"role": msg.role, "content": msg.content} for msg in request.history]
    anonymized_history = anonymize_messages(history_dicts)

    # 3. RAG 檢索（若啟用）
    rag_context = ""
    rag_used_status = False
    rag_sources: list[str] = []
    if request.use_rag and settings.enable_anonymization:
        try:
            docs = await rag.retrieve(anonymized_message)
            if docs:
                context_parts = [doc.to_context_string() for doc in docs]
                rag_context = "\n\n---\n\n".join(context_parts)
                rag_sources = [doc.metadata.get("source", "未知來源") for doc in docs]
                rag_used_status = True
        except Exception:
            # RAG 失敗不影響主要對話流程
            pass

    # 4. 若有 RAG 上下文，附加到訊息前
    final_message = anonymized_message
    if rag_context:
        final_message = (
            f"以下是相關的參考資料，請參考但不需逐字引用：\n\n"
            f"{rag_context}\n\n"
            f"---\n\n使用者的問題：{anonymized_message}"
        )

    # 5. 呼叫 ADK Agent
    try:
        session_id = str(uuid.uuid4())
        reply = await agent.run(
            user_message=final_message,
            history=anonymized_history,
            session_id=session_id,
            image_base64=request.image_base64,
        )
    except Exception as exc:
        logger.exception("AI agent run failed for session %s", session_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI 服務暫時無法使用，請稍後再試。({type(exc).__name__})",
        ) from exc

    # 6. 解析 JSON 回應
    emotion = None
    emotion_color = None
    parsed_reply = reply
    try:
        # 移除可能殘留的 markdown code block
        cleaned_reply = reply.strip()
        if cleaned_reply.startswith("```json"):
            cleaned_reply = cleaned_reply[7:]
        elif cleaned_reply.startswith("```"):
            cleaned_reply = cleaned_reply[3:]
        if cleaned_reply.endswith("```"):
            cleaned_reply = cleaned_reply[:-3]

        data = json.loads(cleaned_reply.strip())
        parsed_reply = data.get("reply", reply)
        emotion = data.get("emotion")
        emotion_color = data.get("emotion_color")
    except Exception:
        logger.warning("Failed to parse JSON from AI response: %s", reply)
        pass

    return ChatResponse(
        reply=parsed_reply,
        session_id=session_id,
        anonymized=was_anonymized,
        rag_used=RagInfo(status=rag_used_status, sources=rag_sources),
        emotion=emotion,
        emotion_color=emotion_color,
    )
