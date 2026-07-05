"""
性騷擾防治智能 AI — Chat API Blueprint
接收前端對話請求，執行匿名化後透過 OpenRouter 呼叫 AI 模型。
"""

import asyncio
import json
import uuid

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field, ValidationError

from backend.app.agents.openrouter_agent import OpenRouterAgent
from backend.app.core.anonymizer import anonymize, anonymize_messages
from backend.app.core.logger import get_logger

logger = get_logger(__name__)

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")

# ── 依賴注入（Singleton per process）────────────────────────────────────────

_agent_instance: OpenRouterAgent | None = None


def get_agent() -> OpenRouterAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = OpenRouterAgent()
    return _agent_instance


def _strip_json_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _escape_newlines_inside_json_strings(text: str) -> str:
    """Escape bare line breaks only when they appear inside JSON strings."""
    result: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\":
            result.append(char)
            escaped = True
            continue
        if char == '"':
            result.append(char)
            in_string = not in_string
            continue
        if in_string and char == "\n":
            result.append("\\n")
            continue
        if in_string and char == "\r":
            result.append("\\r")
            continue
        result.append(char)
    return "".join(result)


def parse_agent_json_response(reply: str) -> dict | None:
    cleaned = _strip_json_code_fence(reply)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = _escape_newlines_inside_json_strings(cleaned)
        return json.loads(repaired)


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


# ── Endpoints ───────────────────────────────────────────────────────────────


@chat_bp.route("/", methods=["POST"])
def chat():
    """
    發送對話訊息
    接收使用者訊息與對話歷史，執行 PII 匿名化後透過 OpenRouter Agent 呼叫 AI，回傳回覆。
    """
    try:
        req_data = request.get_json()
        if not req_data:
            return jsonify({"detail": "Invalid JSON"}), 400
        req_obj = ChatRequest(**req_data)
    except ValidationError as e:
        return jsonify({"detail": e.errors()}), 422
    except Exception as e:
        return jsonify({"detail": str(e)}), 400

    agent = get_agent()

    # 1. 匿名化當前訊息
    anon_result = anonymize(req_obj.message)
    anonymized_message = anon_result.anonymized
    was_anonymized = anon_result.was_modified

    # 2. 匿名化歷史訊息（批次處理）
    history_dicts = [{"role": msg.role, "content": msg.content} for msg in req_obj.history]
    anonymized_history = anonymize_messages(history_dicts)

    async def _run_chat_logic():
        # 3. 呼叫 OpenRouter Agent (內部已實作 Agentic RAG)
        session_id = str(uuid.uuid4())
        try:
            # 傳遞參數給 Agent (若後續 OpenRouterAgent 有回傳 RAG 狀態可再解構)
            reply = await agent.run(
                user_message=anonymized_message,
                history=anonymized_history,
                image_base64=req_obj.image_base64,
                use_rag=req_obj.use_rag,
            )
            return reply.reply, session_id, reply.rag_used, reply.sources or []
        except Exception as exc:
            logger.exception("AI agent run failed for session %s", session_id)
            raise exc

    # 執行 Async 邏輯
    try:
        reply, session_id, rag_used_status, rag_sources = asyncio.run(_run_chat_logic())
    except Exception as exc:
        return jsonify({"detail": f"AI 服務暫時無法使用，請稍後再試。({type(exc).__name__})"}), 503

    # 4. 解析 JSON 回應
    emotion = None
    emotion_color = None
    parsed_reply = reply
    try:
        data = parse_agent_json_response(reply)
        parsed_reply = data.get("reply", reply)
        emotion = data.get("emotion")
        emotion_color = data.get("emotion_color")
    except Exception:
        logger.warning("Failed to parse JSON from AI response: %s", reply)
        pass

    return jsonify(
        {
            "reply": parsed_reply,
            "session_id": session_id,
            "anonymized": was_anonymized,
            "rag_used": {"status": rag_used_status, "sources": rag_sources},
            "emotion": emotion,
            "emotion_color": emotion_color,
        }
    )
