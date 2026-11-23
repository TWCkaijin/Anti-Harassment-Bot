"""Request and response models for chat contract v4."""

import base64
import binascii
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from rag_core.core.case_context import (
    MAX_CONTEXT_REVISION,
    V4_CASE_SCHEMA_VERSION,
    CaseContext,
    ClarificationAnswer,
)
from rag_core.core.chat_response import (
    ASSISTANT_REPLY_MAX_LENGTH,
)
from rag_core.core.clarification_tokens import validate_clarification_answer
from rag_core.core.pipeline_config import (
    MAX_INPUT_HISTORY_CHARACTERS,
    MAX_INPUT_HISTORY_MESSAGES,
)

USER_MESSAGE_MAX_LENGTH = 2000


MAX_HISTORY_CHARACTERS = MAX_INPUT_HISTORY_CHARACTERS


MAX_IMAGE_BYTES = 5 * 1024 * 1024


_MAX_BASE64_LENGTH = 4 * ((MAX_IMAGE_BYTES + 2) // 3)


_SUPPORTED_IMAGE_MIME_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}


MAX_REASONING_CHARACTERS = 32000


MAX_REASONING_ENTRIES = 4096


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


def validate_image_data_url(value: str | None) -> str | None:
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
    contract_version: Literal[4] = 4
    regenerate_from_summary: bool = False
    case_context: CaseContext | None = None
    clarification_answer: ClarificationAnswer | None = None

    @field_validator("image_base64")
    @classmethod
    def validate_image_base64(cls, value: str | None) -> str | None:
        return validate_image_data_url(value)

    @model_validator(mode="after")
    def validate_chat_input(self):
        if self.regenerate_from_summary and self.clarification_answer is not None:
            raise ValueError("Summary regeneration cannot submit a new clarification answer")
        if self.case_context and self.case_context.schema_version != V4_CASE_SCHEMA_VERSION:
            raise ValueError("Case context schema does not match the chat contract")
        if self.clarification_answer:
            if "fact_key" in self.clarification_answer.model_fields_set:
                raise ValueError("V4 clarification answers must not contain a fact key")
            revision = self.case_context.revision if self.case_context else 0
            if self.clarification_answer.context_revision != revision:
                raise ValueError("Clarification answer does not match the context revision")
            validate_clarification_answer(
                self.clarification_answer.model_dump(exclude_none=True),
                require_token=True,
                contract_version=4,
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
    """Only provider-designated summaries reach this transient channel."""

    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_REASONING_CHARACTERS)
    kind: Literal["summary"]
    stage: Literal["understanding", "answer"]


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
