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


def test_validate_optional_rag_distance_threshold():
    assert validate_runtime_config_update({"rag_distance_threshold": 0.35}) == {
        "rag_distance_threshold": 0.35
    }
    assert validate_runtime_config_update({"rag_distance_threshold": None}) == {
        "rag_distance_threshold": None
    }

    for invalid_value in (-0.01, 2.01, "invalid", float("nan")):
        with pytest.raises(ValueError):
            validate_runtime_config_update({"rag_distance_threshold": invalid_value})


def test_firestore_read_uses_write_validation_and_falls_back_per_invalid_field():
    config = runtime_config_module._build_config(
        {
            "openrouter_model": [],
            "temperature": 3,
            "top_p": "invalid",
            "max_tokens": 99999,
            "reasoning_effort": "invalid",
            "rag_retrieval_top_k": 0,
            "rag_distance_threshold": 3,
            "enable_anonymization": "false",
            "rag_collections": {"law": ""},
            "agent_prompt_sections": {"language": 123},
            "maintenance_message": 123,
            "enable_image_upload": "false",
            "development_mode": "true",
        },
        source="firestore",
    )

    settings = runtime_config_module.settings
    assert config.openrouter_model == settings.openrouter_model
    assert config.temperature == settings.openrouter_temperature
    assert config.top_p == settings.openrouter_top_p
    assert config.max_tokens == settings.openrouter_max_tokens
    assert config.reasoning_effort == "none"
    assert config.rag_retrieval_top_k == settings.rag_retrieval_top_k
    assert config.rag_distance_threshold is None
    assert config.enable_anonymization is True
    assert config.rag_collections == runtime_config_module._default_rag_collections()
    assert config.agent_prompt_sections == {}
    assert config.maintenance_message is None
    assert config.enable_image_upload is False
    assert config.development_mode is False
    assert config.source == "firestore"


def test_firestore_read_matches_normalized_write_contract():
    payload = {
        "openrouter_model": " configured/model ",
        "temperature": "0.4",
        "top_p": 0.8,
        "max_tokens": 0,
        "reasoning_effort": "high",
        "rag_retrieval_top_k": "4",
        "rag_distance_threshold": "0.25",
        "enable_anonymization": False,
        "rag_collections": {"law": " custom_law "},
        "agent_prompt_sections": {"language": "第一行\\n第二行"},
        "maintenance_message": " 維護中 ",
        "enable_image_upload": False,
        "development_mode": True,
    }
    cleaned = validate_runtime_config_update(payload)
    config = runtime_config_module._build_config(payload, source="firestore")

    assert config.openrouter_model == cleaned["openrouter_model"]
    assert config.temperature == cleaned["temperature"]
    assert config.top_p == cleaned["top_p"]
    assert config.max_tokens == cleaned["max_tokens"]
    assert config.reasoning_effort == cleaned["reasoning_effort"]
    assert config.rag_retrieval_top_k == cleaned["rag_retrieval_top_k"]
    assert config.rag_distance_threshold == cleaned["rag_distance_threshold"]
    assert config.enable_anonymization is cleaned["enable_anonymization"]
    assert config.rag_collections["law"] == cleaned["rag_collections"]["law"]
    assert config.agent_prompt_sections == cleaned["agent_prompt_sections"]
    assert config.maintenance_message == cleaned["maintenance_message"]
    assert config.enable_image_upload is cleaned["enable_image_upload"]
    assert config.development_mode is cleaned["development_mode"]


def test_backfill_preserves_explicit_none_for_optional_threshold():
    missing = runtime_config_module._missing_runtime_defaults(
        {"rag_distance_threshold": None}, {"rag_distance_threshold": None}
    )

    assert missing == {}


def test_firestore_read_failure_returns_last_known_good_config(monkeypatch):
    last_known_good = runtime_config_module._build_config(
        {
            "openrouter_model": "known-good/model",
            "temperature": 0.4,
            "enable_anonymization": False,
            "development_mode": True,
            "enable_image_upload": True,
        },
        source="firestore",
    )
    monkeypatch.setattr(runtime_config_module, "_cached_config", None)
    monkeypatch.setattr(runtime_config_module, "_cached_at", 0.0)
    monkeypatch.setattr(runtime_config_module, "_last_known_good_config", last_known_good)
    monkeypatch.setattr(
        runtime_config_module.firestore,
        "client",
        lambda: (_ for _ in ()).throw(RuntimeError("Firestore unavailable")),
    )

    result = runtime_config_module.get_runtime_config(force_refresh=True)

    assert result.openrouter_model == "known-good/model"
    assert result.temperature == 0.4
    assert result.enable_anonymization is True
    assert result.development_mode is False
    assert result.enable_image_upload is False
    assert result.source == "last_known_good"
    assert runtime_config_module._cached_config is result


def test_firestore_read_failure_without_cache_uses_safe_defaults(monkeypatch):
    monkeypatch.setattr(runtime_config_module, "_cached_config", None)
    monkeypatch.setattr(runtime_config_module, "_cached_at", 0.0)
    monkeypatch.setattr(runtime_config_module, "_last_known_good_config", None)
    monkeypatch.setattr(
        runtime_config_module.firestore,
        "client",
        lambda: (_ for _ in ()).throw(RuntimeError("Firestore unavailable")),
    )

    result = runtime_config_module.get_runtime_config(force_refresh=True)

    assert result.enable_anonymization is True
    assert result.development_mode is False
    assert result.enable_image_upload is False
    assert result.source == "safe_defaults"


def test_production_forces_development_mode_off(monkeypatch):
    monkeypatch.setattr(runtime_config_module.settings, "environment", "production")

    config = runtime_config_module._build_config(
        {"development_mode": True},
        source="firestore",
    )

    assert config.development_mode is False


def test_preview_may_enable_development_mode(monkeypatch):
    monkeypatch.setattr(runtime_config_module.settings, "environment", "preview")

    config = runtime_config_module._build_config(
        {"development_mode": True},
        source="firestore",
    )

    assert config.development_mode is True
