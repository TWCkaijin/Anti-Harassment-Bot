"""Shared clients use explicit business identities and no private namespace."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag_core import firestore_clients


@pytest.fixture
def configured_client(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        environment="test",
        firebase_project_id="demo-boundary-test",
        firestore_database_id="app-dev",
        firebase_admin_credential_path=tmp_path / "credential.json",
        rag_collection_name="rag_documents",
        rag_judgment_collection_name="rag_judgments",
        rag_remedy_collection_name="rag_remedies",
    )
    monkeypatch.setattr(firestore_clients, "get_settings", lambda: settings)
    monkeypatch.setattr(firestore_clients.firebase_admin, "_apps", {})
    initialize = Mock()
    certificate = Mock(return_value=object())
    client = Mock(return_value=object())
    monkeypatch.setattr(firestore_clients.firebase_admin, "initialize_app", initialize)
    monkeypatch.setattr(firestore_clients.credentials, "Certificate", certificate)
    monkeypatch.setattr(firestore_clients.firestore, "client", client)
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    return SimpleNamespace(
        settings=settings, initialize=initialize, certificate=certificate, client=client
    )


@pytest.mark.parametrize("database", ["(default)", "app-dev"])
def test_business_database_is_always_explicit(configured_client, database):
    configured_client.settings.firestore_database_id = database
    result = firestore_clients.get_business_db()
    configured_client.client.assert_called_once_with(database_id=database)
    assert result is configured_client.client.return_value


def test_missing_business_identity_fails_before_initialization(configured_client):
    configured_client.settings.firestore_database_id = None
    with pytest.raises(RuntimeError, match="identity is missing"):
        firestore_clients.get_business_db()
    configured_client.initialize.assert_not_called()
    configured_client.client.assert_not_called()


@pytest.mark.parametrize("environment", ["production", "preview"])
def test_deployed_services_use_application_credentials_even_if_local_file_exists(
    configured_client, environment
):
    configured_client.settings.environment = environment
    configured_client.settings.firebase_admin_credential_path.write_text("synthetic local fixture")
    firestore_clients.initialize_firebase()
    configured_client.certificate.assert_not_called()
    configured_client.initialize.assert_called_once_with(
        options={"projectId": "demo-boundary-test"}
    )


def test_development_may_select_explicit_local_credentials(configured_client):
    configured_client.settings.environment = "development"
    configured_client.settings.firebase_admin_credential_path.write_text("synthetic local fixture")
    firestore_clients.initialize_firebase()
    configured_client.certificate.assert_called_once_with(
        str(configured_client.settings.firebase_admin_credential_path)
    )
    configured_client.initialize.assert_called_once_with(
        configured_client.certificate.return_value, options={"projectId": "demo-boundary-test"}
    )


def test_test_environment_uses_safe_project_when_none_configured(configured_client):
    configured_client.settings.firebase_project_id = None
    firestore_clients.initialize_firebase()
    configured_client.initialize.assert_called_once_with(
        options={"projectId": "demo-rag-harass-bot"}
    )
    configured_client.certificate.assert_not_called()


def test_emulator_does_not_read_development_credential_file(configured_client, monkeypatch):
    configured_client.settings.environment = "development"
    configured_client.settings.firebase_admin_credential_path.write_text("synthetic local fixture")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    firestore_clients.initialize_firebase()
    configured_client.certificate.assert_not_called()


def test_existing_application_is_not_reinitialized(configured_client, monkeypatch):
    monkeypatch.setattr(firestore_clients.firebase_admin, "_apps", {"[DEFAULT]": object()})
    firestore_clients.get_business_db()
    configured_client.initialize.assert_not_called()
    configured_client.client.assert_called_once_with(database_id="app-dev")


@pytest.mark.parametrize(
    ("kind", "collection"),
    [("law", "rag_documents"), ("judgment", "rag_judgments"), ("remedy", "rag_remedies")],
)
def test_public_kind_maps_to_fixed_collection(configured_client, kind, collection):
    assert firestore_clients.collection_for_kind(kind) == collection


@pytest.mark.parametrize("kind", ["admin_credentials", "runtime_config", "law/doc/sub", "", "LAW"])
def test_kind_cannot_supply_an_arbitrary_collection(configured_client, kind):
    with pytest.raises(ValueError, match="Unsupported"):
        firestore_clients.collection_for_kind(kind)
    configured_client.client.assert_not_called()
