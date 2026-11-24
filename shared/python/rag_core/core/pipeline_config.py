"""Validated request-pipeline controls shared by runtime settings and chat input."""

from typing import Any

MAX_INPUT_HISTORY_MESSAGES = 200
MAX_INPUT_HISTORY_CHARACTERS = 120000
PIPELINE_DEFAULTS: dict[str, Any] = {
    "mask_message": True,
    "mask_case_context": True,
    "mask_retrieval_query": True,
    "trim_history": True,
    "history_max_messages": 40,
    "history_max_chars": 12000,
    "extract_facts": True,
    "auto_clarify": True,
    "model_selection_mode": True,
    "enable_rag": True,
    "enable_skills": True,
    "enable_analysis": True,
    "content_policy": "repair",
}


def validate_pipeline_update(value: Any) -> dict[str, Any]:
    """Validate a partial update without resetting unspecified controls."""
    if not isinstance(value, dict):
        raise ValueError("pipeline must be an object")
    if set(value) - PIPELINE_DEFAULTS.keys():
        raise ValueError("pipeline contains unsupported settings")
    for key, item in value.items():
        if isinstance(PIPELINE_DEFAULTS[key], bool):
            if not isinstance(item, bool):
                raise ValueError(f"pipeline.{key} must be a boolean")
        elif key == "content_policy":
            if item not in ("repair", "annotate"):
                raise ValueError("pipeline.content_policy must be repair or annotate")
        else:
            maximum = (
                MAX_INPUT_HISTORY_MESSAGES
                if key == "history_max_messages"
                else MAX_INPUT_HISTORY_CHARACTERS
            )
            if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= maximum:
                raise ValueError(f"pipeline.{key} must be an integer between 0 and {maximum}")
    return dict(value)


def trim_request_history(history: list[dict], pipeline: dict) -> tuple[list[dict], dict]:
    """Keep recent complete turns within the configured budget, without cutting text."""
    original_chars = sum(len(item["content"]) for item in history)
    retained = history
    if pipeline["trim_history"]:
        turns: list[list[dict]] = []
        for item in history:
            if item["role"] == "user":
                turns.append([item])
            elif turns:
                turns[-1].append(item)
        selected: list[list[dict]] = []
        remaining_messages = pipeline["history_max_messages"]
        remaining_chars = pipeline["history_max_chars"]
        for turn in reversed(turns):
            size = sum(len(item["content"]) for item in turn)
            if len(turn) > remaining_messages or size > remaining_chars:
                break
            selected.append(turn)
            remaining_messages -= len(turn)
            remaining_chars -= size
        retained = [item for turn in reversed(selected) for item in turn]
    retained_chars = sum(len(item["content"]) for item in retained)
    return retained, {
        "trim_enabled": pipeline["trim_history"],
        "received_messages": len(history),
        "retained_messages": len(retained),
        "removed_messages": len(history) - len(retained),
        "received_chars": original_chars,
        "retained_chars": retained_chars,
        "removed_chars": original_chars - retained_chars,
    }
