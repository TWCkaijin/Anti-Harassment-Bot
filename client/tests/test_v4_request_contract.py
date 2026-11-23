"""Chat contract v4 is the only accepted request shape."""

import json

import pytest
from pydantic import ValidationError

from client.tests.test_agent import fake_runtime_config
from client_app.main import app
from rag_core import chat_service as chat_api
from rag_core.core.case_context import CaseContext


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(chat_api, "get_runtime_config", fake_runtime_config)


def test_health_advertises_only_contract_v4():
    capabilities = app.test_client().get("/api/v1/health/").json["capabilities"]
    assert capabilities["chat_contract_versions"] == [4]


def test_requests_default_to_contract_v4():
    assert chat_api.ChatRequest(message="你好").contract_version == 4


@pytest.mark.parametrize("version", [1, 2, 3])
def test_retired_contract_versions_are_rejected_before_the_agent(monkeypatch, version):
    def unexpected_agent():
        raise AssertionError("retired contracts must not reach the agent")

    monkeypatch.setattr(chat_api, "get_agent", unexpected_agent)
    response = app.test_client().post(
        "/api/v1/chat/", json={"message": "你好", "contract_version": version}
    )
    assert response.status_code == 422
    assert response.json["code"] == "invalid_request"
    assert response.json["errors"][0]["field"] == "contract_version"


def test_legacy_fact_case_schema_is_rejected():
    with pytest.raises(ValidationError):
        chat_api.ChatRequest(message="你好", case_context={"schema_version": 2, "facts": {}})


@pytest.mark.parametrize(
    "facts",
    [
        {"name": {"status": "provided", "value": "person"}},
        {"other_role": {"status": "unknown", "value": "主管"}},
        {"other_role": {"status": "provided", "value": " "}},
        {"other_role": {"status": "provided", "value": "x" * 301}},
    ],
)
def test_case_contract_rejects_unknown_or_inconsistent_fields(facts):
    with pytest.raises(ValidationError):
        CaseContext(facts=facts)


def test_explicit_masking_master_toggle_controls_case_summary():
    request = chat_api.ChatRequest(
        message="想了解程序",
        case_context={"schema_version": 3, "summary": "please call 0912345678 now"},
    )
    kwargs, modified = chat_api._prepare_agent_input(
        request, fake_runtime_config(enable_anonymization=False)
    )
    assert not modified
    assert "0912345678" in json.dumps(kwargs["case_context"])


def test_provider_error_never_logs_or_returns_case_canary(monkeypatch, caplog):
    class FailingAgent:
        async def run(self, **kwargs):
            raise RuntimeError("PRIVATE_CASE_CANARY_8123")

    monkeypatch.setattr(chat_api, "get_agent", lambda: FailingAgent())
    monkeypatch.setattr(
        chat_api, "get_runtime_config", lambda: fake_runtime_config(development_mode=True)
    )
    response = app.test_client().post(
        "/api/v1/chat/",
        json={
            "message": "法律資訊",
            "case_context": {"schema_version": 3, "summary": "PRIVATE_CASE_CANARY_8123"},
        },
    )
    assert response.status_code == 500
    assert "PRIVATE_CASE_CANARY_8123" not in caplog.text
    assert "PRIVATE_CASE_CANARY_8123" not in response.get_data(as_text=True)
    assert "debug_message" not in response.json
