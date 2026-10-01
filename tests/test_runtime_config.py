"""測試：Firestore runtime config 的可寫欄位與本地 fallback。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.api_core.exceptions import DeadlineExceeded

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
            "enable_client_privacy_review": "false",
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
    assert config.enable_client_privacy_review is True
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
        "enable_client_privacy_review": False,
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
    assert config.enable_client_privacy_review is cleaned["enable_client_privacy_review"]
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
            "enable_client_privacy_review": False,
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
    assert result.enable_client_privacy_review is True
    assert last_known_good.enable_client_privacy_review is False
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
    assert result.enable_client_privacy_review is True
    assert result.source == "safe_defaults"


@pytest.mark.parametrize("has_last_known_good", [False, True])
def test_firestore_read_deadline_uses_cached_safe_fallback(monkeypatch, has_last_known_good):
    last_known_good = (
        runtime_config_module._build_config(
            {"openrouter_model": "known-good/model"}, source="firestore"
        )
        if has_last_known_good
        else None
    )
    client = Mock()
    document = client.collection.return_value.document.return_value
    document.get.side_effect = DeadlineExceeded("Runtime config read timed out")
    monkeypatch.setattr(runtime_config_module.firestore, "client", lambda: client)
    monkeypatch.setattr(runtime_config_module, "_cached_config", None)
    monkeypatch.setattr(runtime_config_module, "_cached_at", 0.0)
    monkeypatch.setattr(runtime_config_module, "_last_known_good_config", last_known_good)
    monkeypatch.setattr(runtime_config_module, "monotonic", lambda: 100.0)
    monkeypatch.setattr(runtime_config_module.settings, "runtime_config_cache_ttl_seconds", 30)

    result = runtime_config_module.get_runtime_config(force_refresh=True)

    assert result.source == ("last_known_good" if has_last_known_good else "safe_defaults")
    assert result.enable_anonymization is True
    assert result.development_mode is False
    assert result.enable_image_upload is False
    assert result.enable_client_privacy_review is True
    if has_last_known_good:
        assert result.openrouter_model == "known-good/model"
    assert runtime_config_module.get_runtime_config() is result
    document.get.assert_called_once_with(timeout=5.0, retry=None)


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


def test_request_overrides_merge_pipeline_without_mutating_environment():
    base = runtime_config_module._build_config(
        {"pipeline": {"enable_skills": False}}, source="firestore"
    )
    effective = runtime_config_module.resolve_runtime_config(
        base, {"pipeline": {"enable_rag": False, "content_policy": "annotate"}}
    )
    assert effective.pipeline["enable_rag"] is False
    assert effective.pipeline["enable_skills"] is False
    assert effective.pipeline["content_policy"] == "annotate"
    assert base.pipeline["enable_rag"] is True
    assert base.pipeline["content_policy"] == "repair"
    assert effective.pipeline is not base.pipeline
    assert effective.source == "request_override"
    assert runtime_config_module.resolve_runtime_config(base).pipeline == base.pipeline


@pytest.mark.parametrize(
    "pipeline",
    [
        {"mask_message": "false"},
        {"history_max_messages": True},
        {"history_max_messages": 201},
        {"history_max_chars": -1},
        {"history_max_chars": 120001},
        {"content_policy": "disable_schema"},
        {"disable_auth": True},
        {"enable_client_privacy_review": False},
    ],
)
def test_pipeline_rejects_unrecognized_or_ambiguous_values(pipeline):
    with pytest.raises(ValueError):
        validate_runtime_config_update({"pipeline": pipeline})


def test_request_overrides_reject_nonwritable_fields():
    base = runtime_config_module._build_config({}, source="defaults")
    with pytest.raises(ValueError, match="supported runtime"):
        runtime_config_module.resolve_runtime_config(base, {"admin_api_key": "injected"})


@pytest.mark.parametrize("images", [False, True])
@pytest.mark.parametrize("review", [False, True])
def test_client_projection_omits_administrative_and_model_settings(images, review):
    base = runtime_config_module._build_config(
        {"enable_image_upload": images, "enable_client_privacy_review": review}, source="defaults"
    )
    public = runtime_config_module.client_settings(base)
    assert set(public) == {
        "contract_version",
        "enable_image_upload",
        "enable_client_privacy_review",
        "pipeline",
    }
    assert public["contract_version"] == 4
    assert public["enable_image_upload"] is images
    assert public["enable_client_privacy_review"] is review
    assert set(public["pipeline"]) == {
        "trim_history",
        "history_max_messages",
        "history_max_chars",
        "model_selection_mode",
        "enable_analysis",
    }


def test_missing_client_privacy_review_defaults_on_and_backfill_preserves_false():
    defaults = runtime_config_module._default_runtime_document()
    assert defaults["enable_client_privacy_review"] is True
    config = runtime_config_module._build_config({}, source="firestore")
    assert config.enable_client_privacy_review is True
    assert config.public_dict()["enable_client_privacy_review"] is True
    assert runtime_config_module._missing_runtime_defaults(
        {}, {"enable_client_privacy_review": True}
    ) == {"enable_client_privacy_review": True}
    assert (
        runtime_config_module._missing_runtime_defaults(
            {"enable_client_privacy_review": False}, {"enable_client_privacy_review": True}
        )
        == {}
    )


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_client_privacy_review_requires_a_strict_boolean_and_invalid_reads_fail_closed(value):
    with pytest.raises(ValueError, match="enable_client_privacy_review must be a boolean"):
        validate_runtime_config_update({"enable_client_privacy_review": value})
    config = runtime_config_module._build_config(
        {"enable_client_privacy_review": value}, source="firestore"
    )
    assert config.enable_client_privacy_review is True


@pytest.mark.parametrize("environment", ["development", "preview", "production"])
def test_client_review_override_is_independent_and_does_not_mutate_environment(
    environment, monkeypatch
):
    monkeypatch.setattr(runtime_config_module.settings, "environment", environment)
    base = runtime_config_module._build_config(
        {"enable_client_privacy_review": True, "enable_anonymization": True}, source="firestore"
    )
    effective = runtime_config_module.resolve_runtime_config(
        base, {"enable_client_privacy_review": False}
    )
    assert effective.enable_client_privacy_review is False
    assert effective.enable_anonymization is True
    assert base.enable_client_privacy_review is True
    assert effective.public_dict()["enable_client_privacy_review"] is False


def test_client_privacy_review_round_trips_through_config_save_without_cloud(monkeypatch):
    stored = runtime_config_module._default_runtime_document()
    document = Mock()
    document.get.side_effect = lambda **kwargs: SimpleNamespace(
        exists=True, to_dict=lambda: dict(stored)
    )
    document.set.side_effect = lambda value, **kwargs: stored.update(value)
    client = Mock()
    client.collection.return_value.document.return_value = document
    monkeypatch.setattr(runtime_config_module.firestore, "client", lambda: client)
    monkeypatch.setattr(runtime_config_module, "_cached_config", None)
    monkeypatch.setattr(runtime_config_module, "_cached_at", 0.0)
    monkeypatch.setattr(runtime_config_module, "_last_known_good_config", None)

    result = runtime_config_module.update_runtime_config(
        {"enable_client_privacy_review": False}, updated_by="offline-admin"
    )

    assert stored["enable_client_privacy_review"] is False
    assert result.enable_client_privacy_review is False
    assert result.public_dict()["enable_client_privacy_review"] is False
    assert runtime_config_module.client_settings(result)["enable_client_privacy_review"] is False
    assert result.updated_by == "offline-admin"
    assert document.set.call_count == 1
    assert document.set.call_args.kwargs == {"merge": True}
