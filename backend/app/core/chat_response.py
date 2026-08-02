"""Structured response contract shared by the OpenRouter agent and chat API."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class AssistantActionButton(BaseModel):
    """A structured action chosen by the model and verified by the server."""

    action: Literal["tel"]
    phone_number: str = Field(pattern=r"^[0-9+()-]{3,24}$")


class AssistantChatResponse(BaseModel):
    """The only JSON shape accepted from the assistant model."""

    emotion: str = Field(min_length=1, max_length=40)
    emotion_color: Literal["red", "yellow", "green", "blue", "gray"]
    reply: str = Field(min_length=1, max_length=6000)
    suggested_replies: list[str] = Field(min_length=2, max_length=4)
    action_buttons: list[AssistantActionButton] = Field(default_factory=list, max_length=3)
    interaction_mode: Literal["answer", "clarify"] = "answer"
    clarifying_questions: list[str] = Field(default_factory=list, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def render_escaped_control_sequences(cls, value):
        if not isinstance(value, dict):
            return value

        def normalize(item):
            if isinstance(item, str):
                return item.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
            if isinstance(item, list):
                return [normalize(child) for child in item]
            if isinstance(item, dict):
                return {key: normalize(child) for key, child in item.items()}
            return item

        return normalize(value)

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

    @field_validator("clarifying_questions")
    @classmethod
    def normalize_clarifying_questions(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if isinstance(value, str) and value.strip()][:3]

    @model_validator(mode="after")
    def require_questions_for_clarification(self):
        if self.interaction_mode == "clarify" and not self.clarifying_questions:
            raise ValueError("clarify responses must include clarifying_questions")
        return self


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
                "action_buttons": {
                    "type": "array",
                    "description": "僅在目前情境腳本允許時提供的可執行動作。",
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["tel"]},
                            "phone_number": {
                                "type": "string",
                                "description": "情境腳本允許的電話號碼。",
                            },
                        },
                        "required": ["action", "phone_number"],
                        "additionalProperties": False,
                    },
                },
                "interaction_mode": {"type": "string", "enum": ["answer", "clarify"]},
                "clarifying_questions": {
                    "type": "array",
                    "description": "資訊不足時，提供最多三個具體釐清問題。",
                    "maxItems": 3,
                    "items": {"type": "string"},
                },
            },
            "required": [
                "emotion",
                "emotion_color",
                "reply",
                "suggested_replies",
                "action_buttons",
                "interaction_mode",
                "clarifying_questions",
            ],
            "additionalProperties": False,
        },
    },
}
