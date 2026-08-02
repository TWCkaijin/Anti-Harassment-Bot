"""Structured response contract shared by the OpenRouter agent and chat API."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AssistantChatResponse(BaseModel):
    """The only JSON shape accepted from the assistant model."""

    emotion: str = Field(min_length=1, max_length=40)
    emotion_color: Literal["red", "yellow", "green", "blue", "gray"]
    reply: str = Field(min_length=1, max_length=6000)
    suggested_replies: list[str] = Field(min_length=2, max_length=4)

    @field_validator("suggested_replies")
    @classmethod
    def normalize_suggested_replies(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if isinstance(value, str) and value.strip()]
        if len(normalized) < 2:
            raise ValueError("suggested_replies must contain at least two non-empty strings")
        if len(normalized) != len(set(normalized)):
            raise ValueError("suggested_replies must not contain duplicates")
        if any(len(value) > 120 for value in normalized):
            raise ValueError("each suggested reply must be at most 120 characters")
        return normalized


OPENROUTER_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "harassment_assistant_response",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "emotion": {
                    "type": "string",
                    "description": "使用者當前情緒的繁體中文短標籤。",
                },
                "emotion_color": {
                    "type": "string",
                    "enum": ["red", "yellow", "green", "blue", "gray"],
                    "description": "對應情緒標籤的預定義顏色。",
                },
                "reply": {
                    "type": "string",
                    "description": "給使用者的完整繁體中文回覆。",
                },
                "suggested_replies": {
                    "type": "array",
                    "description": "2 到 4 個使用者可直接點選回答的繁體中文短句。",
                    "minItems": 2,
                    "maxItems": 4,
                    "items": {"type": "string"},
                },
            },
            "required": ["emotion", "emotion_color", "reply", "suggested_replies"],
            "additionalProperties": False,
        },
    },
}
