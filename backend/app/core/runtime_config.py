"""Runtime config loaded from one Firestore document per deployment environment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from firebase_admin import firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()

RAG_DATA_TYPES = ("law", "judgment", "remedy")
AGENT_PROMPT_SECTIONS = (
    "core_mission",
    "communication_principles",
    "important_resources",
    "limitations",
    "language",
    "output_format",
    "analysis_rules",
)
PROMPT_MAX_LENGTH = 20000
WRITABLE_FIELDS = {
    "openrouter_model",
    "temperature",
    "top_p",
    "max_tokens",
    "agent_prompt_sections",
    "rag_retrieval_top_k",
    "enable_anonymization",
    "rag_collections",
    "maintenance_message",
    "enable_image_upload",
}


@dataclass(frozen=True)
class RuntimeConfig:
    """Merged runtime settings used by request handlers and the agent."""

    openrouter_model: str
    rag_retrieval_top_k: int
    enable_anonymization: bool
    temperature: float
    top_p: float
    max_tokens: int
    agent_prompt_sections: dict[str, str] = field(default_factory=dict)
    rag_collections: dict[str, str] = field(default_factory=dict)
    maintenance_message: str | None = None
    enable_image_upload: bool = True
    source: str = "defaults"
    updated_at: str | None = None
    updated_by: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


_cached_config: RuntimeConfig | None = None
_cached_at = 0.0


def _normalize_prompt(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\\n", "\n").strip()
    return normalized or None


def _validate_prompt_sections(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("agent_prompt_sections must be an object")
    allowed_keys = set(AGENT_PROMPT_SECTIONS)
    cleaned: dict[str, str] = {}
    for key, value in payload.items():
        if key not in allowed_keys:
            continue
        if value is None:
            cleaned[key] = ""
            continue
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        normalized = value.replace("\r\n", "\n").strip()
        if len(normalized) > PROMPT_MAX_LENGTH:
            raise ValueError(f"{key} must be at most {PROMPT_MAX_LENGTH} characters")
        cleaned[key] = normalized
    return cleaned


def _default_rag_collections() -> dict[str, str]:
    return {
        "law": settings.rag_collection_name,
        "judgment": settings.rag_judgment_collection_name,
        "remedy": settings.rag_remedy_collection_name,
    }


def _configured_prompt_sections(value: Any) -> dict[str, str]:
    """Read only valid prompt overrides from the current Firestore document."""
    if not isinstance(value, dict):
        return {}
    sections: dict[str, str] = {}
    for key in AGENT_PROMPT_SECTIONS:
        normalized = _normalize_prompt(value.get(key))
        if normalized:
            sections[key] = normalized[:PROMPT_MAX_LENGTH]
    return sections


def _timestamp_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _build_config(doc_data: dict[str, Any] | None, source: str) -> RuntimeConfig:
    data = doc_data or {}
    rag_collections = _default_rag_collections()
    configured_collections = data.get("rag_collections")
    if isinstance(configured_collections, dict):
        for key in RAG_DATA_TYPES:
            value = configured_collections.get(key)
            if isinstance(value, str) and value.strip():
                rag_collections[key] = value.strip()

    try:
        temperature = float(data.get("temperature", settings.openrouter_temperature))
    except (TypeError, ValueError):
        temperature = settings.openrouter_temperature
    try:
        top_p = float(data.get("top_p", settings.openrouter_top_p))
    except (TypeError, ValueError):
        top_p = settings.openrouter_top_p
    try:
        max_tokens = int(data.get("max_tokens", settings.openrouter_max_tokens))
    except (TypeError, ValueError):
        max_tokens = settings.openrouter_max_tokens

    return RuntimeConfig(
        openrouter_model=str(data.get("openrouter_model") or settings.openrouter_model),
        rag_retrieval_top_k=int(data.get("rag_retrieval_top_k") or settings.rag_retrieval_top_k),
        enable_anonymization=bool(data.get("enable_anonymization", settings.enable_anonymization)),
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        agent_prompt_sections=_configured_prompt_sections(data.get("agent_prompt_sections")),
        rag_collections=rag_collections,
        maintenance_message=_normalize_prompt(data.get("maintenance_message")),
        enable_image_upload=bool(data.get("enable_image_upload", True)),
        source=source,
        updated_at=_timestamp_to_iso(data.get("updated_at")),
        updated_by=data.get("updated_by") if isinstance(data.get("updated_by"), str) else None,
    )


def get_runtime_config(force_refresh: bool = False) -> RuntimeConfig:
    """Return runtime config with a short process-local TTL cache."""
    global _cached_at, _cached_config
    ttl = settings.runtime_config_cache_ttl_seconds
    now = monotonic()
    if not force_refresh and _cached_config is not None and ttl > 0 and now - _cached_at < ttl:
        return _cached_config

    doc_data: dict[str, Any] | None = None
    source = "defaults"
    try:
        db = firestore.client()
        snapshot = (
            db.collection(settings.runtime_config_collection_name)
            .document(settings.runtime_config_document_id)
            .get()
        )
        if snapshot.exists:
            doc_data = snapshot.to_dict() or {}
            source = "firestore"
    except Exception as exc:
        logger.warning("Failed to read Firestore runtime config: %s", exc)

    config = _build_config(doc_data, source)
    _cached_config = config
    _cached_at = now
    return config


def invalidate_runtime_config_cache() -> None:
    global _cached_at, _cached_config
    _cached_config = None
    _cached_at = 0.0


def _clean_rag_collections(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("rag_collections must be an object")
    cleaned: dict[str, str] = {}
    for key in RAG_DATA_TYPES:
        item = value.get(key)
        if item is None:
            continue
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"rag_collections.{key} must be a non-empty string")
        cleaned[key] = item.strip()
    return cleaned


def validate_runtime_config_update(payload: dict[str, Any]) -> dict[str, Any]:
    """Allow only known runtime fields and normalize values before Firestore write."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in WRITABLE_FIELDS:
            continue
        if key in {"openrouter_model", "maintenance_message"}:
            if value is None:
                if key == "openrouter_model":
                    raise ValueError("openrouter_model must be a non-empty string")
                cleaned[key] = ""
                continue
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            normalized = value.replace("\r\n", "\n").strip()
            if key == "openrouter_model" and not normalized:
                raise ValueError("openrouter_model must be a non-empty string")
            cleaned[key] = normalized
        elif key in {"temperature", "top_p"}:
            try:
                numeric_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number") from exc
            if key == "temperature" and not 0 <= numeric_value <= 2:
                raise ValueError("temperature must be between 0 and 2")
            if key == "top_p" and not 0 < numeric_value <= 1:
                raise ValueError("top_p must be greater than 0 and at most 1")
            cleaned[key] = numeric_value
        elif key == "max_tokens":
            try:
                max_tokens = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("max_tokens must be an integer") from exc
            if max_tokens < 128 or max_tokens > 8192:
                raise ValueError("max_tokens must be between 128 and 8192")
            cleaned[key] = max_tokens
        elif key == "rag_retrieval_top_k":
            top_k = int(value)
            if top_k < 1 or top_k > 20:
                raise ValueError("rag_retrieval_top_k must be between 1 and 20")
            cleaned[key] = top_k
        elif key in {"enable_anonymization", "enable_image_upload"}:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            cleaned[key] = value
        elif key == "rag_collections":
            cleaned[key] = _clean_rag_collections(value)
        elif key == "agent_prompt_sections":
            cleaned[key] = _validate_prompt_sections(value)
    return cleaned


def _default_runtime_document() -> dict[str, Any]:
    """Return the complete local fallback set for a Firestore runtime document."""
    # Avoid an import cycle while retaining one source of truth for prompt defaults.
    from backend.app.agents.openrouter_agent import get_default_prompt_sections

    return {
        "openrouter_model": settings.openrouter_model,
        "temperature": settings.openrouter_temperature,
        "top_p": settings.openrouter_top_p,
        "max_tokens": settings.openrouter_max_tokens,
        "agent_prompt_sections": get_default_prompt_sections(),
        "rag_retrieval_top_k": settings.rag_retrieval_top_k,
        "enable_anonymization": settings.enable_anonymization,
        "rag_collections": _default_rag_collections(),
        "maintenance_message": "",
        "enable_image_upload": True,
    }


def _missing_runtime_defaults(existing: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Fill missing settings while preserving existing scalar and map values."""
    missing: dict[str, Any] = {}
    for key, default_value in defaults.items():
        current_value = existing.get(key)
        if isinstance(default_value, dict) and isinstance(current_value, dict):
            completed_map = {**default_value, **current_value}
            if completed_map != current_value:
                missing[key] = completed_map
        elif (
            key not in existing
            or current_value is None
            or (key == "openrouter_model" and not str(current_value).strip())
        ):
            missing[key] = default_value
    return missing


def update_runtime_config(payload: dict[str, Any], updated_by: str) -> RuntimeConfig:
    seed_runtime_config_if_missing(updated_by=updated_by)
    cleaned = validate_runtime_config_update(payload)
    cleaned["updated_by"] = updated_by
    cleaned["updated_at"] = SERVER_TIMESTAMP

    db = firestore.client()
    (
        db.collection(settings.runtime_config_collection_name)
        .document(settings.runtime_config_document_id)
        .set(cleaned, merge=True)
    )
    invalidate_runtime_config_cache()
    return get_runtime_config(force_refresh=True)


def seed_runtime_config_if_missing(updated_by: str = "system") -> RuntimeConfig:
    """Create or backfill the complete Firestore runtime config document."""
    db = firestore.client()
    ref = db.collection(settings.runtime_config_collection_name).document(
        settings.runtime_config_document_id
    )
    snapshot = ref.get()
    existing = snapshot.to_dict() if snapshot.exists else {}
    missing = _missing_runtime_defaults(existing or {}, _default_runtime_document())
    if missing:
        missing["updated_by"] = updated_by
        missing["updated_at"] = datetime.now(tz=UTC)
        ref.set(missing, merge=True)
    invalidate_runtime_config_cache()
    return get_runtime_config(force_refresh=True)
