"""Admin-only evaluation contracts. No generated answers or inferred relevance."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Kind = Literal["law", "judgment", "remedy"]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DatasetInput(Input):
    name: str = Field(min_length=1, max_length=120)
    archived: bool = False


class Annotation(Input):
    kind: Kind
    doc_id: str = Field(min_length=1, max_length=160, pattern=r"^[^/]+$")
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    relevance: Literal["relevant", "irrelevant", "unjudged"] = "unjudged"
    manual_unit_id: str | None = Field(
        default=None, min_length=1, max_length=160, pattern=r"^[^/]+$"
    )


class CaseInput(Input):
    query: str = Field(min_length=1, max_length=4000)
    data_types: list[Kind] = Field(min_length=1, max_length=3)
    privacy_confirmed: Literal[True]
    annotations: list[Annotation] = Field(default_factory=list, max_length=200)
    reviewed: bool = False
    no_relevant: bool = False

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Query cannot be blank")
        return value

    @field_validator("data_types")
    @classmethod
    def unique_types(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate data types")
        return sorted(value)


class Settings(Input):
    model: str = Field(min_length=1, max_length=200)
    candidate_k: int = Field(default=20, ge=1, le=50)
    top_k: int = Field(default=3, ge=1, le=20)
    timeout_seconds: float = Field(default=1, ge=0.1, le=10, allow_inf_nan=False)
    max_chars_per_doc: int = Field(default=2000, ge=100, le=20000)
    distance_threshold: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    provider_sort: Literal["auto", "latency", "throughput", "price"] = "auto"
    preserve_data_types: bool = True

    @model_validator(mode="after")
    def valid_budget(self):
        if self.candidate_k < self.top_k:
            raise ValueError("N must be at least K")
        if not self.model.strip():
            raise ValueError("Model is required")
        return self


class RunInput(Input):
    dataset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: int = Field(ge=1)
    settings: Settings
    case_ids: list[str] | None = Field(default=None, min_length=1, max_length=200)
    reuse_run_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    external_confirmed: Literal[True]


class PrepareInput(Input):
    case_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    settings: Settings
    external_confirmed: Literal[True]


def validate_scope(settings, cases):
    if settings.preserve_data_types and any(settings.top_k < len(c["data_types"]) for c in cases):
        raise ValueError("K must cover the selected data types")


def retrieval_binding(case, settings, collections, embedding):
    return digest(
        {
            "query": case["query"],
            "data_types": case["data_types"],
            "candidate_k": settings.candidate_k,
            "distance_threshold": settings.distance_threshold,
            "preserve_data_types": settings.preserve_data_types,
            "collections": collections,
            "embedding": embedding,
        }
    )
