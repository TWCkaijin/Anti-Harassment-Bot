"""Structured response contract shared by the OpenRouter agent and chat API."""

import ipaddress
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

ASSISTANT_REPLY_MAX_LENGTH = 6000


PHONE_PATTERN = r"^[0-9+()-]{3,24}$"
ACTION_ID_PATTERN = r"^[a-z][a-z0-9_-]{1,63}$"


def validate_action_url(value: str) -> str:
    """Accept an explicit HTTP(S) destination without ambiguous authority syntax."""
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Action URLs must not contain whitespace or control characters")
    if "\\" in value:
        raise ValueError("Action URLs must not contain backslashes")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("Action URLs require an HTTP(S) host without credentials")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            ascii_host = hostname.rstrip(".").encode("idna").decode("ascii")
            if len(ascii_host) > 253 or not all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in ascii_host.split(".")
            ):
                raise ValueError("Action URL host is invalid") from None
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Action URL must be a valid HTTP(S) URL without credentials") from exc
    return value


class _StrictAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TelephoneActionButton(_StrictAction):
    action: Literal["tel"]
    phone_number: str = Field(pattern=PHONE_PATTERN)


class URLActionButton(_StrictAction):
    action: Literal["url"]
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url", mode="before")
    @classmethod
    def require_http_url(cls, value):
        return validate_action_url(value) if isinstance(value, str) else value


class OptionsActionButton(_StrictAction):
    action: Literal["options"]
    id: str = Field(pattern=ACTION_ID_PATTERN)


AssistantActionButton = Annotated[
    TelephoneActionButton | URLActionButton | OptionsActionButton, Field(discriminator="action")
]


class ActionOption(_StrictAction):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)


class ConfiguredTelephoneAction(TelephoneActionButton):
    label: str = Field(min_length=1, max_length=80)


class ConfiguredURLAction(URLActionButton):
    label: str = Field(min_length=1, max_length=80)


class ConfiguredOptionsAction(OptionsActionButton):
    label: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    options: list[ActionOption] = Field(min_length=2, max_length=8)

    @field_validator("options")
    @classmethod
    def require_distinct_values(cls, values: list[ActionOption]) -> list[ActionOption]:
        if len({option.value for option in values}) != len(values):
            raise ValueError("Options must have distinct values")
        return values


ConfiguredAction = Annotated[
    ConfiguredTelephoneAction | ConfiguredURLAction | ConfiguredOptionsAction,
    Field(discriminator="action"),
]
configured_action_adapter = TypeAdapter(ConfiguredAction)


def action_selector(action: BaseModel | dict[str, Any]) -> dict[str, str]:
    """Project trusted display configuration into the selector the model may return."""
    data = action.model_dump() if isinstance(action, BaseModel) else action
    kind = data.get("action")
    target_field = {"tel": "phone_number", "url": "url", "options": "id"}.get(kind)
    if target_field is None or not isinstance(data.get(target_field), str):
        raise ValueError("Unknown or incomplete action selector")
    return {"action": kind, target_field: data[target_field]}


def action_key(action: BaseModel | dict[str, Any]) -> tuple[str, str] | None:
    try:
        selector = action_selector(action)
    except (ValueError, AttributeError, TypeError):
        return None
    return selector["action"], next(value for key, value in selector.items() if key != "action")


class AssistantChatResponse(BaseModel):
    """The only JSON shape accepted from the assistant model."""

    emotion: str = Field(min_length=1, max_length=40)
    emotion_color: Literal["red", "yellow", "green", "blue", "gray"]
    reply: str = Field(min_length=1, max_length=ASSISTANT_REPLY_MAX_LENGTH)
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
                "reply": {
                    "type": "string",
                    "description": "優先輸出的完整繁體中文回覆，會逐段顯示給使用者。",
                    "minLength": 1,
                    "maxLength": ASSISTANT_REPLY_MAX_LENGTH,
                },
                "emotion": {
                    "type": "string",
                    "description": "使用者當前情緒的繁體中文短標籤。",
                },
                "emotion_color": {
                    "type": "string",
                    "enum": ["red", "yellow", "green", "blue", "gray"],
                    "description": "對應情緒標籤的預定義顏色。",
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
                    "description": "僅選擇目前 Skills 允許的 tel、url 或 options 動作；原樣使用其 selector。",
                    "maxItems": 3,
                    "items": {
                        "anyOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string", "enum": ["tel"]},
                                    "phone_number": {"type": "string", "pattern": PHONE_PATTERN},
                                },
                                "required": ["action", "phone_number"],
                                "additionalProperties": False,
                            },
                            {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string", "enum": ["url"]},
                                    "url": {"type": "string", "minLength": 1, "maxLength": 2048},
                                },
                                "required": ["action", "url"],
                                "additionalProperties": False,
                            },
                            {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string", "enum": ["options"]},
                                    "id": {"type": "string", "pattern": ACTION_ID_PATTERN},
                                },
                                "required": ["action", "id"],
                                "additionalProperties": False,
                            },
                        ],
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
                "reply",
                "emotion",
                "emotion_color",
                "suggested_replies",
                "action_buttons",
                "interaction_mode",
                "clarifying_questions",
            ],
            "additionalProperties": False,
        },
    },
}
