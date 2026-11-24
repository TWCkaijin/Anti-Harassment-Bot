"""Environment identities fail closed before any database or model call."""

import os

import pytest
from pydantic import ValidationError

from rag_core.core.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    fields = set(Settings.model_fields)
    for name in tuple(os.environ):
        if name.lower() in fields or name.startswith(("RAG_OPENROUTER_", "RAG_CLARIFICATION_")):
            monkeypatch.delenv(name)
    for name in ("FIRESTORE_EMULATOR_HOST", "RAG_FUNCTION_DISCOVERY", "K_SERVICE"):
        monkeypatch.delenv(name, raising=False)


def configured(environment, **overrides):
    values = {
        "environment": environment,
        "firestore_database_id": "(default)" if environment == "production" else "app-dev",
        "runtime_config_document_id": "app_main" if environment == "production" else "app_dev",
        "openrouter_api_key": "offline-model-key",
        "clarification_signing_key": "offline-signing-key",
    }
    return Settings(**(values | overrides), _env_file=None)


@pytest.mark.parametrize("environment", ["production", "preview", "development", "test", "ci"])
def test_each_environment_selects_its_business_database(environment):
    settings = configured(environment)
    expected = "(default)" if environment == "production" else "app-dev"
    assert settings.firestore_database_id == expected


@pytest.mark.parametrize("environment", ["production", "preview"])
@pytest.mark.parametrize("database", [None, "admin-private", "admin-private-dev", "unreviewed"])
def test_deployed_services_reject_missing_private_and_other_databases(environment, database):
    with pytest.raises(ValidationError, match="requires FIRESTORE_DATABASE_ID"):
        configured(environment, firestore_database_id=database)


@pytest.mark.parametrize(
    ("environment", "database", "document"),
    [("production", "app-dev", "app_main"), ("preview", "(default)", "app_dev")],
)
def test_deployed_services_cannot_cross_business_environments(environment, database, document):
    with pytest.raises(ValidationError, match="requires FIRESTORE_DATABASE_ID"):
        configured(environment, firestore_database_id=database, runtime_config_document_id=document)


@pytest.mark.parametrize("environment", ["production", "preview"])
def test_runtime_document_must_match_deployment(environment):
    with pytest.raises(ValidationError, match="requires runtime document"):
        configured(environment, runtime_config_document_id="another_environment")


@pytest.mark.parametrize("environment", ["development", "test", "ci"])
def test_local_default_is_preview_and_production_requires_emulator(environment, monkeypatch):
    assert configured(environment, firestore_database_id=None).firestore_database_id == "app-dev"
    with pytest.raises(ValidationError, match="Local applications"):
        configured(environment, firestore_database_id="(default)")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    assert (
        configured(environment, firestore_database_id="(default)").firestore_database_id
        == "(default)"
    )


@pytest.mark.parametrize("environment", ["production", "preview"])
@pytest.mark.parametrize("missing", ["openrouter_api_key", "clarification_signing_key"])
def test_deployed_services_require_both_independent_secrets(environment, missing):
    with pytest.raises(ValidationError, match="secrets are required"):
        configured(environment, **{missing: ""})


@pytest.mark.parametrize(("environment", "suffix"), [("production", "PROD"), ("preview", "DEV")])
def test_secret_manager_values_are_bound_to_environment(environment, suffix, monkeypatch):
    other = "DEV" if suffix == "PROD" else "PROD"
    monkeypatch.setenv(f"RAG_OPENROUTER_{other}", "wrong-environment-model")
    monkeypatch.setenv(f"RAG_CLARIFICATION_{other}", "wrong-environment-signing")
    with pytest.raises(ValidationError, match="secrets are required"):
        configured(environment, openrouter_api_key="", clarification_signing_key=None)
    monkeypatch.setenv(f"RAG_OPENROUTER_{suffix}", "selected-model")
    monkeypatch.setenv(f"RAG_CLARIFICATION_{suffix}", "selected-signing")
    monkeypatch.setenv(f"RAG_CLARIFICATION_LEGACY_{suffix}", '{"3":"derived-key-fixture"}')
    settings = configured(environment, openrouter_api_key="", clarification_signing_key=None)
    assert settings.openrouter_api_key == "selected-model"
    assert settings.clarification_signing_key == "selected-signing"
    assert settings.clarification_legacy_signing_key == '{"3":"derived-key-fixture"}'


def test_admin_password_cannot_supply_client_signing_secret(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "fixture-admin-password")
    with pytest.raises(ValidationError, match="secrets are required"):
        configured("production", clarification_signing_key=None)


def test_discovery_exception_is_disabled_in_deployed_service(monkeypatch):
    monkeypatch.setenv("RAG_FUNCTION_DISCOVERY", "1")
    assert configured("production", openrouter_api_key="", clarification_signing_key=None)
    monkeypatch.setenv("K_SERVICE", "client-production")
    with pytest.raises(ValidationError, match="secrets are required"):
        configured("production", openrouter_api_key="", clarification_signing_key=None)


@pytest.mark.parametrize(
    "field",
    [
        "rag_collection_name",
        "rag_judgment_collection_name",
        "rag_remedy_collection_name",
        "runtime_config_collection_name",
    ],
)
@pytest.mark.parametrize("value", ["other/doc/collection", "../admin", "", "bad space"])
def test_collection_settings_are_single_collection_names(field, value):
    with pytest.raises(ValidationError, match="single collection names"):
        configured("test", **{field: value})
