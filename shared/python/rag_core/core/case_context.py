"""Request-scoped case facts and trusted questions. Nothing here persists a case."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_serializer,
    model_validator,
)

SUMMARY_MAX_LENGTH = 4000
MAX_CONTEXT_REVISION = 9007199254740991

FactKey = Literal[
    "subject_role",
    "other_role",
    "relationship",
    "work_related",
    "internship_related",
    "education_related",
    "behavior",
    "ongoing",
    "event_time",
    "age_group",
    "city",
    "desired_help",
]
FactStatus = Literal["provided", "unknown", "declined"]
FACT_KEYS = tuple(FactKey.__args__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


FactText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class CaseFact(StrictModel):
    status: FactStatus
    value: FactText | list[FactText] | None = None

    @model_validator(mode="after")
    def valid_value(self):
        if self.status == "provided" and not self.value:
            raise ValueError("Provided facts require a value")
        if self.status != "provided" and self.value is not None:
            raise ValueError("Unknown or declined facts must not carry a value")
        if isinstance(self.value, list) and (
            not 1 <= len(self.value) <= 4 or len(set(self.value)) != len(self.value)
        ):
            raise ValueError("Multi-value facts require one to four distinct values")
        return self


# Chat contract v4 sends the text-summary case schema.
V4_CASE_SCHEMA_VERSION = 3


class CaseContext(StrictModel):
    schema_version: Literal[1, 2, 3] = 1
    revision: int = Field(default=0, ge=0, le=MAX_CONTEXT_REVISION, strict=True)
    facts: dict[FactKey, CaseFact] = Field(default_factory=dict, max_length=len(FACT_KEYS))
    summary: str = Field(default="", max_length=SUMMARY_MAX_LENGTH)
    summary_origin: Literal["model", "user", "migration"] = "model"

    @model_validator(mode="before")
    @classmethod
    def reject_summary_in_legacy_context(cls, value):
        if (
            isinstance(value, dict)
            and value.get("schema_version", 1) != 3
            and {"summary", "summary_origin"}.intersection(value)
        ):
            raise ValueError("Text summaries require case schema 3")
        return value

    @model_validator(mode="after")
    def legacy_values_are_strings(self):
        if self.schema_version == 3 and self.facts:
            raise ValueError("Case schema 3 uses a text summary and cannot contain legacy facts")
        if self.schema_version == 1 and any(
            isinstance(fact.value, list) for fact in self.facts.values()
        ):
            raise ValueError("Multi-value facts require case schema 2")
        return self

    @model_serializer(mode="wrap")
    def serialize_context(self, handler):
        result = handler(self)
        if self.schema_version != 3:
            # Keep v1/v2 serialized snapshots valid when read by the same model.
            result.pop("summary", None)
            result.pop("summary_origin", None)
        return result


class SummaryUpdate(StrictModel):
    """A proposed replacement for exactly one request's context revision."""

    base_revision: int = Field(ge=0, le=MAX_CONTEXT_REVISION, strict=True)
    summary: str = Field(max_length=SUMMARY_MAX_LENGTH)
    evidence: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list, max_length=12
    )


class ClarificationAnswer(CaseFact):
    question_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")
    fact_key: FactKey | None = None
    context_revision: int | None = Field(default=None, ge=0, le=MAX_CONTEXT_REVISION, strict=True)
    validation_token: str | None = Field(default=None, max_length=8192)
    context_scope: Literal["personal", "scenario"] | None = None
    selection_mode: Literal["single", "multiple"] | None = None
    max_selections: int | None = Field(default=None, ge=1, le=4)
    allowed_values: list[FactText] | None = Field(default=None, max_length=3)


def is_urgent(message: str) -> bool:
    return any(
        term in message
        for term in (
            "正在被",
            "現在被",
            "不安全",
            "立即危險",
            "自殺",
            "輕生",
            "限制行動",
            "正在跟蹤",
            "威脅",
        )
    )
