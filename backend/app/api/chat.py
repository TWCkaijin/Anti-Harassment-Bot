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
from typing import Annotated, Literal

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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend.app.agents.openrouter_agent import AgentContractError, AgentResult, OpenRouterAgent
from backend.app.core.agent_errors import ModelOutputLimitError
from backend.app.core.anonymizer import anonymize
from backend.app.core.case_context import (
    MAX_CONTEXT_REVISION,
    SUMMARY_MAX_LENGTH,
    CaseContext,
    ClarificationAnswer,
    SummaryUpdate,
)
from backend.app.core.chat_response import (
    ASSISTANT_REPLY_MAX_LENGTH,
    AssistantChatResponse,
    action_key,
)
from backend.app.core.clarification_tokens import validate_clarification_answer
from backend.app.core.logger import get_logger
from backend.app.core.pipeline_config import (
    MAX_INPUT_HISTORY_CHARACTERS,
    MAX_INPUT_HISTORY_MESSAGES,
    trim_request_history,
)
from backend.app.core.runtime_config import get_runtime_config, resolve_runtime_config
from backend.app.rag.base import RAGUnavailableError

logger = get_logger(__name__)

chat_bp = Blueprint("chat", __name__, url_prefix="/chat")

USER_MESSAGE_MAX_LENGTH = 2000
MAX_HISTORY_CHARACTERS = MAX_INPUT_HISTORY_CHARACTERS
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_BASE64_LENGTH = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
_SUPPORTED_IMAGE_MIME_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}
STREAM_HEARTBEAT_SECONDS = 10
MAX_REASONING_CHARACTERS = 32000
MAX_REASONING_ENTRIES = 4096
_STREAM_PROGRESS_PHASES = {
    "anonymizing",
    "preparing",
    "waiting_model",
    "retrieving",
    "generating",
    "guidance",
    "validating",
}
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
    """Validate format and size; the image itself is not anonymized here."""
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


def _image_upload_disabled_error():
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
    # Guided requests and admin tests can contain private facts in provider errors.
    # Record a category only, including in development, never an upstream body.
    request_payload = request.get_json(silent=True) if request.is_json else None
    private_case_request = request.path.endswith("/admin/chat-test") or (
        isinstance(request_payload, dict) and request_payload.get("contract_version") in (2, 3, 4)
    )
    if private_case_request:
        error_message = type(exc).__name__
        log_exception = RuntimeError(error_message)
    logger.error(
        "Chat request failed [%s]: %s: %s",
        code,
        type(exc).__name__,
        error_message,
        exc_info=None
        if private_case_request
        else (type(log_exception), log_exception, exc.__traceback__),
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
    if runtime_config.development_mode and not private_case_request:
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

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        default="",
        max_length=USER_MESSAGE_MAX_LENGTH,
        description="當前使用者訊息；附圖時可留空",
    )
    history: list[MessageItem] = Field(
        default_factory=list,
        max_length=MAX_INPUT_HISTORY_MESSAGES,
        description="對話歷史，由後端依本次設定保留完整回合",
    )
    use_rag: bool = Field(default=True, description="是否啟用 RAG 檢索增強")
    stream: bool = Field(default=False, description="以 SSE 串流回覆文字，完成後傳回完整資料")
    image_base64: str | None = Field(default=None, description="使用者上傳的圖片 (base64 data URL)")
    contract_version: Literal[1, 2, 3, 4] = 1
    regenerate_from_summary: bool = False
    case_context: CaseContext | None = None
    clarification_answer: ClarificationAnswer | None = None

    @field_validator("image_base64")
    @classmethod
    def validate_image_base64(cls, value: str | None) -> str | None:
        return _validate_image_data_url(value)

    @model_validator(mode="after")
    def validate_chat_input(self):
        if self.regenerate_from_summary and self.contract_version != 4:
            raise ValueError("Regeneration from a text summary requires chat contract version 4")
        if self.regenerate_from_summary and self.clarification_answer is not None:
            raise ValueError("Summary regeneration cannot submit a new clarification answer")
        if self.contract_version == 1 and (
            self.case_context is not None or self.clarification_answer is not None
        ):
            raise ValueError("Case context requires contract version 2, 3 or 4")
        expected_schema = {2: 1, 3: 2, 4: 3}.get(self.contract_version, 1)
        if self.case_context and self.case_context.schema_version != expected_schema:
            raise ValueError("Case context schema does not match the chat contract")
        if self.clarification_answer:
            if self.contract_version == 4:
                if "fact_key" in self.clarification_answer.model_fields_set:
                    raise ValueError("V4 clarification answers must not contain a fact key")
                revision = self.case_context.revision if self.case_context else 0
                if self.clarification_answer.context_revision != revision:
                    raise ValueError("Clarification answer does not match the context revision")
            elif self.clarification_answer.fact_key is None:
                raise ValueError("Legacy clarification answers require a fact key")
            elif "context_revision" in self.clarification_answer.model_fields_set:
                raise ValueError("Clarification context revision requires chat contract version 4")
            if self.contract_version == 2 and isinstance(self.clarification_answer.value, list):
                raise ValueError("Multiple answers require chat contract version 3")
            validate_clarification_answer(
                self.clarification_answer.model_dump(exclude_none=True),
                require_token=self.contract_version >= 3,
                contract_version=4 if self.contract_version == 4 else 3,
            )
        if not self.message.strip() and not self.image_base64:
            raise ValueError("message or image_base64 is required")
        history_characters = sum(len(item.content) for item in self.history)
        if history_characters > MAX_HISTORY_CHARACTERS:
            raise ValueError(
                f"history content must be at most {MAX_HISTORY_CHARACTERS} characters in total"
            )
        return self


class AnalysisEntry(BaseModel):
    """Public, evidence-based summary; arbitrary provider metadata is never streamed."""

    model_config = ConfigDict(extra="forbid")
    stage: Literal["understanding", "sufficiency", "sources", "answer"]
    summary: str = Field(min_length=1, max_length=1000)
    facts: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=12)
    source_labels: list[Annotated[str, Field(max_length=300)]] = Field(
        default_factory=list, max_length=24
    )
    limitations: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=12
    )


class ReasoningEntry(BaseModel):
    """Only provider-designated public text/summary reaches this transient channel."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_REASONING_CHARACTERS)
    kind: Literal["text", "summary"]
    stage: Literal["understanding", "answer"]


def _public_reasoning_entry(value, remaining: int) -> dict | None:
    if not isinstance(value, dict) or remaining <= 0 or not isinstance(value.get("text"), str):
        return None
    try:
        # Never serialize a provider object wholesale: encrypted blocks,
        # signatures and tool metadata are not part of this public channel.
        return ReasoningEntry.model_validate(
            {
                "text": value["text"][:remaining],
                "kind": value.get("kind"),
                "stage": value.get("stage"),
            }
        ).model_dump()
    except ValidationError:
        return None


def _public_reasoning_entries(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    entries = []
    remaining = MAX_REASONING_CHARACTERS
    for value in values[:MAX_REASONING_ENTRIES]:
        entry = _public_reasoning_entry(value, remaining)
        if entry is not None:
            entries.append(entry)
            remaining -= len(entry["text"])
    return entries


class _ClarificationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=300)


class ClarificationV4(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")
    question: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=500)
    options: list[_ClarificationOption] = Field(default_factory=list, max_length=3)
    selection_mode: Literal["single", "multiple"]
    max_selections: int = Field(ge=1, le=4, strict=True)
    context_scope: Literal["personal", "scenario"]
    context_revision: int = Field(ge=0, le=MAX_CONTEXT_REVISION, strict=True)
    validation_token: str = Field(min_length=1, max_length=8192)

    @model_validator(mode="after")
    def validate_issued_shape(self):
        if (self.selection_mode == "single" and self.max_selections != 1) or (
            self.selection_mode == "multiple" and self.max_selections < 2
        ):
            raise ValueError("Invalid clarification selection bounds")
        values = [item.value for item in self.options]
        if len(set(values)) != len(values):
            raise ValueError("Clarification choices must be distinct")
        validate_clarification_answer(
            {
                "question_id": self.question_id,
                "status": "unknown",
                "selection_mode": self.selection_mode,
                "max_selections": self.max_selections,
                "context_scope": self.context_scope,
                "context_revision": self.context_revision,
                "allowed_values": values,
                "validation_token": self.validation_token,
            },
            contract_version=4,
        )
        return self


def _agent_error(runtime_config, exc: Exception):
    """Use the same diagnostic codes for JSON and streaming requests."""
    if isinstance(exc, ModelOutputLimitError):
        return _service_error(
            runtime_config,
            exc,
            code="model_output_limit",
            detail="模型輸出達到長度上限，請提高輸出預算後再測試"
            if request.path.endswith("/admin/chat-test")
            else "模型回覆因輸出長度限制而未完成，請縮小問題範圍後再試",
            retryable=False,
            status_code=502,
        )
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


def _response_payload(
    result: AgentResult,
    session_id: str,
    was_anonymized: bool,
    runtime_config,
    req_obj: ChatRequest | None = None,
):
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
    if req_obj is not None and req_obj.contract_version == 4:
        guidance = result.guidance or {}
        if guidance.get("contract_version") != 4:
            raise AgentContractError("Invalid v4 response contract")
        revision = req_obj.case_context.revision if req_obj.case_context else 0
        returned_revision = guidance.get("context_revision", revision)
        if (
            isinstance(returned_revision, bool)
            or not isinstance(returned_revision, int)
            or returned_revision != revision
        ):
            raise AgentContractError("Response context revision does not match the request")
        update = (
            SummaryUpdate.model_validate(guidance["summary_update"])
            if guidance.get("summary_update") is not None
            else None
        )
        if update is not None and update.base_revision != revision:
            raise AgentContractError("Summary update does not match the request revision")
        if update is not None and req_obj.regenerate_from_summary:
            raise AgentContractError("Regeneration must not replace the authoritative summary")
        current_summary = req_obj.case_context.summary if req_obj.case_context else ""
        if update is not None and update.summary == current_summary:
            update = None
        if update is not None and revision >= MAX_CONTEXT_REVISION:
            raise AgentContractError("Summary revision cannot be advanced")
        question = (
            ClarificationV4.model_validate(guidance["clarification"])
            if guidance.get("clarification") is not None
            else None
        )
        if question is not None and question.context_revision != revision + int(update is not None):
            raise AgentContractError("Clarification does not match the resulting context revision")
        # v4 has one authoritative text summary and natural reply. Do not expose
        # stale legacy fact/section fields or arbitrary agent-internal payloads.
        payload.update(
            contract_version=4,
            context_revision=revision,
            summary_update=update.model_dump() if update is not None else None,
            clarification=question.model_dump() if question is not None else None,
        )
        payload.update(
            {
                key: guidance[key]
                for key in ("execution", "analysis", "reasoning")
                if key in guidance
            }
        )
    elif result.guidance:
        payload.update(result.guidance)
    if payload.get("contract_version") in (3, 4):
        entries = payload.get("analysis", []) if runtime_config.pipeline["enable_analysis"] else []
        if not isinstance(entries, list) or len(entries) > 4:
            raise ValueError("Invalid analysis summary")
        payload["analysis"] = [AnalysisEntry.model_validate(item).model_dump() for item in entries]
        if len({entry["stage"] for entry in payload["analysis"]}) != len(entries):
            raise ValueError("Duplicate analysis summary stage")
    else:
        payload.pop("analysis", None)
    if payload.get("contract_version") == 4 and runtime_config.pipeline["enable_analysis"]:
        payload["reasoning"] = _public_reasoning_entries(payload.get("reasoning", []))
    else:
        payload.pop("reasoning", None)
    if runtime_config.development_mode:
        payload["debug_tool_calls"] = result.tool_calls
    return payload


def _sse_event(event: str, payload: dict) -> str:
    # JSON escaping keeps newlines in reply text inside one SSE data line.
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _prepare_agent_input(
    req_obj: ChatRequest,
    runtime_config,
    diagnostics: dict | None = None,
    *,
    include_input_stages: bool = False,
) -> tuple[dict, bool]:
    original_history = [{"role": msg.role, "content": msg.content} for msg in req_obj.history]
    history, history_counts = trim_request_history(original_history, runtime_config.pipeline)
    trimmed_history = history
    message = req_obj.message
    was_anonymized = False
    mask_message = runtime_config.enable_anonymization and runtime_config.pipeline["mask_message"]
    mask_case = runtime_config.enable_anonymization and runtime_config.pipeline["mask_case_context"]
    pii = {
        stage: {"enabled": enabled, "kinds": set(), "changed_values": 0}
        for stage, enabled in (("message", mask_message), ("case_context", mask_case))
    }

    def clean_text(value, stage, case_limit=300):
        nonlocal was_anonymized
        if isinstance(value, str) and pii[stage]["enabled"]:
            result = anonymize(value)
            was_anonymized = was_anonymized or result.was_modified
            pii[stage]["kinds"].update(result.detected_types)
            pii[stage]["changed_values"] += int(result.was_modified)
            return result.anonymized[:case_limit] if stage == "case_context" else result.anonymized
        if isinstance(value, dict):
            # Only case values carry user prose. Signed question metadata must
            # remain byte-for-byte intact for its downstream signature check.
            return {
                key: clean_text(item, stage, SUMMARY_MAX_LENGTH if key == "summary" else case_limit)
                if key not in {"validation_token", "question_id", "allowed_values"}
                else item
                for key, item in value.items()
            }
        if isinstance(value, list):
            cleaned = [clean_text(item, stage) for item in value]
            if stage == "case_context" and all(isinstance(item, str) for item in cleaned):
                # Distinct selections can contain different identifiers which
                # redact to the same placeholder. Preserve their count and order
                # without retaining any original identifying text.
                used = set()
                for index, item in enumerate(cleaned):
                    if item in used:
                        suffix_index = index + 1
                        while True:
                            suffix = f"（另一已遮罩值{suffix_index}）"
                            distinct = item[: 300 - len(suffix)] + suffix
                            if distinct not in used:
                                break
                            suffix_index += 1
                        cleaned[index] = distinct
                    used.add(cleaned[index])
            return cleaned
        return value

    message = clean_text(message, "message")
    history = [{**item, "content": clean_text(item["content"], "message")} for item in history]
    arguments = {
        "user_message": message,
        "history": history,
        "image_base64": req_obj.image_base64 if runtime_config.enable_image_upload else None,
        "use_rag": req_obj.use_rag and runtime_config.pipeline["enable_rag"],
        "runtime_config": runtime_config,
    }
    if req_obj.contract_version in (2, 3, 4):
        arguments.update(
            contract_version=req_obj.contract_version,
            case_context=clean_text(
                (
                    req_obj.case_context
                    or CaseContext(schema_version={2: 1, 3: 2, 4: 3}[req_obj.contract_version])
                ).model_dump(exclude_none=True),
                "case_context",
            ),
            clarification_answer=clean_text(
                req_obj.clarification_answer.model_dump(exclude_none=True), "case_context"
            )
            if req_obj.clarification_answer
            else None,
        )
        if req_obj.contract_version == 4:
            arguments["regenerate_from_summary"] = req_obj.regenerate_from_summary
        if req_obj.contract_version >= 3 and req_obj.clarification_answer:
            issued = validate_clarification_answer(
                req_obj.clarification_answer.model_dump(exclude_none=True),
                contract_version=req_obj.contract_version,
            )
            # A typed answer is verified before masking. The internal bounds
            # prevent rechecking masked values against the original token's
            # choices; the public request schema never accepts this argument.
            arguments["clarification_constraints"] = {
                key: issued[key]
                for key in (
                    "question_id",
                    "fact_key",
                    "selection_mode",
                    "max_selections",
                    "context_scope",
                    "context_revision",
                )
                if key in issued
            }
    if diagnostics is not None:
        diagnostics["history"] = history_counts
        diagnostics["pii"] = {
            stage: {**counts, "kinds": sorted(counts["kinds"])} for stage, counts in pii.items()
        }
        if include_input_stages:
            original_summary = req_obj.case_context.summary if req_obj.case_context else ""
            summary = arguments.get("case_context", {}).get("summary", "")
            # Only the authenticated, non-persistent admin test opts into raw
            # before/after views; regular chats never create these snapshots.
            diagnostics["input_stages"] = {
                "message": {
                    "before": req_obj.message,
                    "after": message,
                    "changed": req_obj.message != message,
                },
                "history": {
                    "before": original_history,
                    "after_trim": trimmed_history,
                    "after": history,
                    "changed": original_history != history,
                },
                "summary": {
                    "before": original_summary,
                    "after": summary,
                    "changed": original_summary != summary,
                },
            }
        arguments["diagnostics"] = diagnostics
    return arguments, was_anonymized


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
            analysis_entries: list[dict] = []
            reasoning_entries: list[dict] = []
            reasoning_characters = 0
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

            async def emit_analysis(snapshot: dict):
                nonlocal sent_output
                if (
                    req_obj.contract_version not in (3, 4)
                    or not runtime_config.pipeline["enable_analysis"]
                ):
                    return
                entry = AnalysisEntry.model_validate(snapshot).model_dump()
                if len(analysis_entries) >= 4 or any(
                    item["stage"] == entry["stage"] for item in analysis_entries
                ):
                    raise AgentContractError("Invalid analysis summary sequence")
                analysis_entries.append(entry)
                sent_output = True
                await emit("analysis", entry)

            async def emit_reasoning(snapshot: dict):
                nonlocal sent_output, reasoning_characters
                if (
                    req_obj.contract_version != 4
                    or not runtime_config.pipeline["enable_analysis"]
                    or len(reasoning_entries) >= MAX_REASONING_ENTRIES
                ):
                    return
                entry = _public_reasoning_entry(
                    snapshot, MAX_REASONING_CHARACTERS - reasoning_characters
                )
                if entry is not None:
                    reasoning_entries.append(entry)
                    reasoning_characters += len(entry["text"])
                    sent_output = True
                    mark("first_reasoning_ready_ms")
                    await emit("reasoning", entry)

            async def produce():
                try:
                    if runtime_config.enable_anonymization and (
                        runtime_config.pipeline["mask_message"]
                        or runtime_config.pipeline["mask_case_context"]
                    ):
                        await emit_progress({"phase": "anonymizing"})
                    run_kwargs, was_anonymized = _prepare_agent_input(req_obj, runtime_config)
                    result = await get_agent().run(
                        **run_kwargs,
                        on_reply_delta=emit_delta,
                        on_guidance=emit_guidance,
                        on_progress=emit_progress,
                        on_analysis=emit_analysis,
                        **(
                            {"on_reasoning": emit_reasoning}
                            if req_obj.contract_version == 4
                            else {}
                        ),
                    )
                except Exception as exc:
                    error_response, status = _agent_error(runtime_config, exc)
                else:
                    try:
                        await emit_progress({"phase": "validating"})
                        payload = _response_payload(
                            result, session_id, was_anonymized, runtime_config, req_obj
                        )
                        if req_obj.contract_version in (3, 4) and analysis_entries:
                            if payload.get("analysis") and payload["analysis"] != analysis_entries:
                                raise AgentContractError(
                                    "Analysis summary changed after publication"
                                )
                            payload["analysis"] = analysis_entries
                        if req_obj.contract_version == 4 and reasoning_entries:
                            payload["reasoning"] = reasoning_entries
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
                    elif event == "reasoning":
                        mark("first_reasoning_yield_ms")
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

    runtime_config = resolve_runtime_config(get_runtime_config())
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
        return _image_upload_disabled_error()
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
        payload = _response_payload(result, session_id, was_anonymized, runtime_config, req_obj)
    except Exception as exc:
        return _retryable_error(runtime_config, exc)
    return jsonify(payload)
