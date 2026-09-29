"""
性騷擾防治智能 AI — Chat API Blueprint
接收前端對話請求，執行匿名化後透過 OpenRouter 呼叫 AI 模型。
"""

import asyncio
import base64
import binascii
import json
import uuid
from time import monotonic
from typing import Literal

from flask import Blueprint, Response, jsonify, request, stream_with_context
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from backend.app.agents.openrouter_agent import AgentContractError, AgentResult, OpenRouterAgent
from backend.app.core.anonymizer import anonymize, anonymize_messages
from backend.app.core.chat_response import (
    ASSISTANT_REPLY_MAX_LENGTH,
    AssistantChatResponse,
    action_key,
)
from backend.app.core.logger import get_logger
from backend.app.core.runtime_config import get_runtime_config
from backend.app.rag.base import RAGUnavailableError

logger = get_logger(__name__)

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")

USER_MESSAGE_MAX_LENGTH = 2000
MAX_HISTORY_CHARACTERS = 30000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
STREAM_HEARTBEAT_SECONDS = 10
_STREAM_PROGRESS_PHASES = {
    "anonymizing",
    "preparing",
    "waiting_model",
    "retrieving",
    "generating",
    "guidance",
    "validating",
}
_MAX_BASE64_LENGTH = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
_SUPPORTED_IMAGE_MIME_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
_TRANSIENT_UPSTREAM_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    ConnectionError,
    TimeoutError,
)
_PERMANENT_UPSTREAM_ERRORS = (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
_RETRYABLE_AGENT_ERRORS = (AgentContractError, *_TRANSIENT_UPSTREAM_ERRORS)

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


def _matches_image_signature(mime_type: str, content: bytes) -> bool:
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if mime_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _validate_image_data_url(value: str | None) -> str | None:
    """Validate an image data URL without trusting its declared MIME type."""
    if value is None:
        return None
    header, separator, encoded = value.partition(",")
    if (
        not separator
        or not header.lower().startswith("data:")
        or not header.lower().endswith(";base64")
    ):
        raise ValueError("image_base64 must be a base64 data URL")
    mime_type = header[5:-7].lower()
    if mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
        raise ValueError("image_base64 MIME type is not supported")
    if not encoded or len(encoded) > _MAX_BASE64_LENGTH:
        raise ValueError(f"image_base64 decoded size must be at most {MAX_IMAGE_BYTES} bytes")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image_base64 contains invalid base64 data") from exc
    if len(decoded) > MAX_IMAGE_BYTES:
        raise ValueError(f"image_base64 decoded size must be at most {MAX_IMAGE_BYTES} bytes")
    if not _matches_image_signature(mime_type, decoded):
        raise ValueError("image_base64 content does not match its MIME type")
    return value


def _service_error(
    runtime_config,
    exc: Exception,
    *,
    code: str,
    detail: str,
    retryable: bool,
    status_code: int,
    error_id: str | None = None,
):
    """Keep operational diagnostics server-side unless development mode is enabled."""
    error_message = (
        json.dumps(
            exc.errors(include_input=False, include_context=False, include_url=False),
            ensure_ascii=False,
        )
        if isinstance(exc, ValidationError)
        else str(exc)
    )
    # Pydantic's default traceback renders input_value (the model's response).
    # Keep the original frames but replace its final exception text for logging.
    log_exception = (
        ValueError(f"{type(exc).__name__}: {error_message}")
        if isinstance(exc, ValidationError)
        else exc
    )
    logger.error(
        "Chat request failed [%s]: %s: %s",
        code,
        type(exc).__name__,
        error_message,
        exc_info=(type(log_exception), log_exception, exc.__traceback__),
        extra={
            "event": "chat_request_failed",
            "error_code": code,
            "error_type": type(exc).__name__,
            "error_message": error_message,
            "http_status": status_code,
            "retryable": retryable,
            **({"error_id": error_id} if error_id else {}),
        },
    )
    payload = {"code": code, "detail": detail, "retryable": retryable}
    if error_id:
        payload["error_id"] = error_id
    if runtime_config.development_mode:
        payload["debug_message"] = f"{type(exc).__name__}: {str(exc)[:2000]}"
    return jsonify(payload), status_code


def _retryable_error(runtime_config, exc: Exception):
    return _service_error(
        runtime_config,
        exc,
        code="upstream_invalid_response",
        detail="伺服器回傳錯誤，正在重試中",
        retryable=True,
        status_code=502,
    )


def _request_validation_error(exc: ValidationError):
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return (
        jsonify(
            {
                "code": "invalid_request",
                "detail": "Invalid request payload",
                "errors": errors,
                "retryable": False,
            }
        ),
        422,
    )


# ── 請求 / 回應模型 ─────────────────────────────────────────────────────────


class MessageItem(BaseModel):
    """單一訊息項目。"""

    role: Literal["user", "assistant"] = Field(..., description="發訊者角色")
    content: str = Field(
        ...,
        min_length=1,
        max_length=ASSISTANT_REPLY_MAX_LENGTH,
        description="訊息內容",
    )

    @model_validator(mode="after")
    def enforce_role_specific_length(self):
        if self.role == "user" and len(self.content) > USER_MESSAGE_MAX_LENGTH:
            raise ValueError(
                f"user history content must be at most {USER_MESSAGE_MAX_LENGTH} characters"
            )
        return self


class ChatRequest(BaseModel):
    """聊天請求：包含當前訊息及完整對話歷史。"""

    message: str = Field(
        default="",
        max_length=USER_MESSAGE_MAX_LENGTH,
        description="當前使用者訊息；附圖時可留空",
    )
    history: list[MessageItem] = Field(
        default_factory=list,
        max_length=50,
        description="對話歷史（最多 50 輪，由前端 localStorage 傳入）",
    )
    use_rag: bool = Field(default=True, description="是否啟用 RAG 檢索增強")
    stream: bool = Field(default=False, description="以 SSE 串流回覆文字，完成後傳回完整資料")
    image_base64: str | None = Field(default=None, description="使用者上傳的圖片 (base64 data URL)")

    @field_validator("image_base64")
    @classmethod
    def validate_image_base64(cls, value: str | None) -> str | None:
        return _validate_image_data_url(value)

    @model_validator(mode="after")
    def require_message_or_image(self):
        if not self.message.strip() and not self.image_base64:
            raise ValueError("message or image_base64 is required")
        history_characters = sum(len(item.content) for item in self.history)
        if history_characters > MAX_HISTORY_CHARACTERS:
            raise ValueError(
                f"history content must be at most {MAX_HISTORY_CHARACTERS} characters in total"
            )
        return self


def _agent_error(runtime_config, exc: Exception):
    """Use the same diagnostic codes for JSON and streaming requests."""
    if isinstance(exc, RAGUnavailableError):
        return _service_error(
            runtime_config,
            exc,
            code="rag_unavailable",
            detail="檢索服務暫時無法使用，請稍後再試",
            retryable=True,
            status_code=503,
        )
    if isinstance(exc, _RETRYABLE_AGENT_ERRORS):
        return _retryable_error(runtime_config, exc)
    if isinstance(exc, _PERMANENT_UPSTREAM_ERRORS):
        return _service_error(
            runtime_config,
            exc,
            code="upstream_request_rejected",
            detail="AI 服務目前無法處理此請求",
            retryable=False,
            status_code=502,
        )
    return _service_error(
        runtime_config,
        exc,
        code="internal_error",
        detail="伺服器無法完成請求",
        retryable=False,
        status_code=500,
        error_id=uuid.uuid4().hex,
    )


def _response_payload(result: AgentResult, session_id: str, was_anonymized: bool, runtime_config):
    """Only publish metadata after the complete response passes the existing contract."""
    data = parse_agent_json_response(result.reply)
    if not isinstance(data, dict):
        raise ValueError("OpenRouter response must be a JSON object")
    structured_response = AssistantChatResponse.model_validate(data)
    approved_actions = {}
    for action in result.available_actions:
        key = action_key(action)
        if key is not None:
            approved_actions.setdefault(key, action)
    action_buttons = []
    selected_keys = set()
    for action in structured_response.action_buttons:
        key = action_key(action)
        if key in approved_actions and key not in selected_keys:
            # Labels, URLs and option values still come only from trusted Skills.
            action_buttons.append(approved_actions[key])
            selected_keys.add(key)
    payload = {
        "reply": structured_response.reply,
        "session_id": session_id,
        "anonymized": was_anonymized,
        "rag_used": {"status": result.rag_used, "sources": result.sources or []},
        "emotion": structured_response.emotion,
        "emotion_color": structured_response.emotion_color,
        "suggested_replies": structured_response.suggested_replies,
        "action_buttons": action_buttons,
        "interaction_mode": structured_response.interaction_mode,
        "clarifying_questions": structured_response.clarifying_questions,
    }
    if runtime_config.development_mode:
        payload["debug_tool_calls"] = result.tool_calls
    return payload


def _sse_event(event: str, payload: dict) -> str:
    # JSON escaping keeps newlines in reply text inside one SSE data line.
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _prepare_agent_input(req_obj: ChatRequest, runtime_config) -> tuple[dict, bool]:
    history = [{"role": msg.role, "content": msg.content} for msg in req_obj.history]
    message = req_obj.message
    was_anonymized = False
    if runtime_config.enable_anonymization:
        anon_result = anonymize(message)
        message = anon_result.anonymized
        was_anonymized = anon_result.was_modified
        history = anonymize_messages(history)
    return {
        "user_message": message,
        "history": history,
        "image_base64": req_obj.image_base64 if runtime_config.enable_image_upload else None,
        "use_rag": req_obj.use_rag,
    }, was_anonymized


def _stream_response(
    req_obj: ChatRequest,
    session_id: str,
    runtime_config,
    *,
    request_started_at: float | None = None,
):
    started_at = request_started_at if request_started_at is not None else monotonic()

    @stream_with_context
    def generate():
        # Acknowledge each callback only after WSGI takes its bytes. Merely
        # queueing data doesn't yield: buffered SDK chunks or synchronous work
        # could otherwise run ahead and delay the first visible character.
        with asyncio.Runner() as runner:
            events: asyncio.Queue[tuple[str, dict, asyncio.Future | None]] = asyncio.Queue(
                maxsize=1
            )
            sent_output = False
            current_phase: str | None = None
            timings: dict[str, float] = {}
            outcome = "cancelled"

            def mark(name: str):
                timings.setdefault(name, round((monotonic() - started_at) * 1000, 2))

            async def emit(event: str, payload: dict):
                acknowledged = runner.get_loop().create_future()
                await events.put((event, payload, acknowledged))
                await acknowledged

            async def emit_progress(snapshot: dict):
                nonlocal current_phase
                phase = snapshot.get("phase")
                if phase not in _STREAM_PROGRESS_PHASES or phase == current_phase:
                    return
                current_phase = phase
                await emit(
                    "progress",
                    {
                        "phase": phase,
                        "elapsed_ms": round((monotonic() - started_at) * 1000, 2),
                    },
                )

            async def emit_delta(text: str):
                nonlocal sent_output
                if text:
                    sent_output = True
                    mark("first_reply_ready_ms")
                    await emit("delta", {"text": text})
                    # Deliver the visible token first; status must not delay it.
                    await emit_progress({"phase": "generating"})

            async def emit_guidance(snapshot: dict):
                nonlocal sent_output
                if snapshot:
                    sent_output = True
                    mark("first_guidance_ready_ms")
                    await emit("guidance", snapshot)
                    await emit_progress({"phase": "guidance"})

            async def produce():
                try:
                    if runtime_config.enable_anonymization:
                        await emit_progress({"phase": "anonymizing"})
                    run_kwargs, was_anonymized = _prepare_agent_input(req_obj, runtime_config)
                    result = await get_agent().run(
                        **run_kwargs,
                        on_reply_delta=emit_delta,
                        on_guidance=emit_guidance,
                        on_progress=emit_progress,
                    )
                except Exception as exc:
                    error_response, status = _agent_error(runtime_config, exc)
                else:
                    try:
                        await emit_progress({"phase": "validating"})
                        payload = _response_payload(
                            result, session_id, was_anonymized, runtime_config
                        )
                    except Exception as exc:
                        error_response, status = _retryable_error(runtime_config, exc)
                    else:
                        await events.put(("done", payload, None))
                        return
                payload = error_response.get_json()
                payload["status"] = status
                if sent_output:
                    # Restarting an answer after showing it would mix attempts.
                    payload["retryable"] = False
                    payload["detail"] = "回覆途中發生錯誤，內容尚未完成，請重新提問"
                await events.put(("error", payload, None))

            task = runner.get_loop().create_task(produce())
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event, payload, acknowledged = runner.run(
                            asyncio.wait_for(events.get(), timeout=STREAM_HEARTBEAT_SECONDS)
                        )
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event in {"delta", "guidance"}:
                        mark(
                            "first_reply_yield_ms"
                            if event == "delta"
                            else "first_guidance_yield_ms"
                        )
                    elif event in {"done", "error"}:
                        outcome = event
                    yield _sse_event(event, payload)
                    if acknowledged is not None and not acknowledged.done():
                        acknowledged.set_result(None)
                    if event in {"done", "error"}:
                        break
            finally:
                task.cancel()

                async def finish():
                    await asyncio.gather(task, return_exceptions=True)

                runner.run(finish())
                logger.info(
                    "Chat stream timing outcome=%s",
                    outcome,
                    extra={
                        "event": "chat_stream_timing",
                        "session_id": session_id,
                        "outcome": outcome,
                        "duration_ms": round((monotonic() - started_at) * 1000, 2),
                        **timings,
                    },
                )

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store, no-transform", "X-Accel-Buffering": "no"},
    )


# ── Endpoints ───────────────────────────────────────────────────────────────


@chat_bp.route("/", methods=["POST"])
def chat():
    """
    發送對話訊息
    接收使用者訊息與對話歷史，執行 PII 匿名化後透過 OpenRouter Agent 呼叫 AI，回傳回覆。
    """
    request_started_at = monotonic()
    try:
        req_data = request.get_json()
        if not req_data:
            return jsonify({"code": "invalid_json", "detail": "Invalid JSON"}), 400
        req_obj = ChatRequest(**req_data)
    except ValidationError as e:
        return _request_validation_error(e)
    except Exception as e:
        return jsonify({"detail": str(e)}), 400

    runtime_config = get_runtime_config()
    if runtime_config.maintenance_message:
        return (
            jsonify(
                {
                    "detail": runtime_config.maintenance_message,
                    "retryable": False,
                    "code": "maintenance",
                }
            ),
            503,
        )
    if req_obj.image_base64 and not runtime_config.enable_image_upload:
        return (
            jsonify(
                {
                    "code": "image_upload_disabled",
                    "detail": "Image upload is disabled",
                    "retryable": False,
                }
            ),
            422,
        )

    session_id = str(uuid.uuid4())
    if req_obj.stream:
        return _stream_response(
            req_obj,
            session_id,
            runtime_config,
            request_started_at=request_started_at,
        )

    try:
        run_kwargs, was_anonymized = _prepare_agent_input(req_obj, runtime_config)
        result = asyncio.run(get_agent().run(**run_kwargs))
    except Exception as exc:
        return _agent_error(runtime_config, exc)
    try:
        payload = _response_payload(result, session_id, was_anonymized, runtime_config)
    except Exception as exc:
        return _retryable_error(runtime_config, exc)
    return jsonify(payload)
