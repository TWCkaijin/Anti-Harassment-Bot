"""測試：Firestore runtime config 的可寫欄位與本地 fallback。"""

import pytest

import backend.app.core.runtime_config as runtime_config_module
from backend.app.core.runtime_config import validate_runtime_config_update


def test_validate_generation_settings():
    result = validate_runtime_config_update(
        {
            "temperature": 0.35,
            "top_p": 0.9,
            "max_tokens": 0,
        }
    )

    assert result == {
        "temperature": 0.35,
        "top_p": 0.9,
        "max_tokens": 0,
    }


def test_validate_reasoning_effort():
    assert validate_runtime_config_update({"reasoning_effort": "high"}) == {
        "reasoning_effort": "high"
    }

    with pytest.raises(ValueError, match="reasoning_effort is invalid"):
        validate_runtime_config_update({"reasoning_effort": "deep"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", 2.1),
        ("top_p", 0),
        ("max_tokens", -1),
        ("max_tokens", 8193),
    ],
)
def test_reject_invalid_generation_settings(field, value):
    with pytest.raises(ValueError):
        validate_runtime_config_update({field: value})


def test_firestore_prompt_sections_override_local_defaults():
    config = runtime_config_module._build_config(
        {"agent_prompt_sections": {"language": "Firestore 語言規則"}},
        source="firestore",
    )

    assert config.agent_prompt_sections == {"language": "Firestore 語言規則"}
    assert config.source == "firestore"


def test_validate_prompt_sections_for_firestore_write():
    result = validate_runtime_config_update(
        {"agent_prompt_sections": {"language": " Firestore 優先 ", "unknown": "ignored"}}
    )

    assert result == {"agent_prompt_sections": {"language": "Firestore 優先"}}


@pytest.mark.parametrize("value", [None, "", "   "])
def test_reject_empty_openrouter_model(value):
    with pytest.raises(ValueError, match="openrouter_model must be a non-empty string"):
        validate_runtime_config_update({"openrouter_model": value})


def test_backfill_preserves_existing_values_and_completes_nested_maps():
    defaults = {
        "openrouter_model": "default/model",
        "agent_prompt_sections": {
            "core_mission": "default mission",
            "language": "default language",
        },
        "rag_collections": {"law": "default_law", "judgment": "default_judgment"},
        "enable_image_upload": True,
    }
    existing = {
        "openrouter_model": "configured/model",
        "agent_prompt_sections": {"language": "configured language"},
        "rag_collections": {"law": "configured_law"},
    }

    missing = runtime_config_module._missing_runtime_defaults(existing, defaults)

    assert "openrouter_model" not in missing
    assert missing["agent_prompt_sections"] == {
        "core_mission": "default mission",
        "language": "configured language",
    }
    assert missing["rag_collections"] == {
        "law": "configured_law",
        "judgment": "default_judgment",
    }
    assert missing["enable_image_upload"] is True


def test_backfill_replaces_empty_openrouter_model_with_local_default():
    missing = runtime_config_module._missing_runtime_defaults(
        {"openrouter_model": ""}, {"openrouter_model": "default/model"}
    )

    assert missing == {"openrouter_model": "default/model"}
