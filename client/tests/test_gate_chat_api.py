"""Public SSE Gate terminal-event and retraction contract."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from client.tests.test_agent import v4_result
from client_app.main import app
from rag_core import chat_service
from rag_core.agents.gate_agent import GateDecision
from rag_core.core.runtime_config import RuntimeConfig, _build_config

CONFIG = RuntimeConfig(
    openrouter_model="main/model",
    rag_retrieval_top_k=3,
    enable_anonymization=True,
    temperature=0.2,
    top_p=1,
    max_tokens=1200,
    gate_enabled=True,
    gate_model="gate/model",
    gate_prompt="independent policy",
)


def events(response):
    frames = []
    for frame in response.data.decode().split("\n\n"):
        if not frame.startswith("event: "):
            continue
        name, payload = frame.split("\n", 1)
        frames.append((name[7:], json.loads(payload[6:])))
    return frames


@pytest.mark.parametrize("post", ["allow", "deny", "error"])
def test_post_gate_stream_contract(monkeypatch, post):
    monkeypatch.setattr(chat_service, "get_runtime_config", lambda **kwargs: CONFIG)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)

    async def main(**kwargs):
        await kwargs["on_reply_delta"]("provisional answer")
        return v4_result(
            reply=json.dumps(
                {
                    "reply": "provisional answer",
                    "emotion": "中性",
                    "emotion_color": "gray",
                    "suggested_replies": ["下一步", "其他問題"],
                    "action_buttons": [],
                },
                ensure_ascii=False,
            )
        )

    async def gate(**kwargs):
        if kwargs["stage"] == "post":
            if kwargs["candidate"]["visible_payload"] == {
                "reply": "很抱歉，無法協助此類程式實作。"
            }:
                return GateDecision(decision="allow", reason_code="allowed")
            if post == "error":
                raise TimeoutError("private material")
            if post == "deny":
                return GateDecision(decision="deny", reason_code="technical_implementation")
        return GateDecision(decision="allow", reason_code="allowed")

    async def refusal(**kwargs):
        return "很抱歉，無法協助此類程式實作。"

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=main))
    monkeypatch.setattr(chat_service, "get_gate", lambda: SimpleNamespace(run=gate))
    monkeypatch.setattr(chat_service, "get_refusal_agent", lambda: SimpleNamespace(run=refusal))

    response = app.test_client().post("/api/v1/chat/", json={"message": "請寫程式", "stream": True})
    frames = events(response)
    names = [name for name, _ in frames]
    assert names.count("done") + names.count("error") == 1
    first_delta = next(index for index, item in enumerate(frames) if item[0] == "delta")
    assert frames[first_delta][1] == {"text": "provisional answer"}
    if post == "allow":
        assert "retract" not in names
        assert frames[-1][0] == "done"
        assert frames[-1][1]["reply"] == "provisional answer"
    else:
        retract_index = names.index("retract")
        assert retract_index > first_delta
        retract = frames[retract_index][1]
        assert retract == {
            "turn_id": frames[0][1]["turn_id"],
            "code": "gate_denied" if post == "deny" else "review_unavailable",
        }
        if post == "deny":
            assert frames[retract_index + 1] == (
                "delta",
                {"text": "很抱歉，無法協助此類程式實作。"},
            )
            assert frames[-1][0] == "done"
            assert frames[-1][1]["reply"] == "很抱歉，無法協助此類程式實作。"
        else:
            assert frames[-1][0] == "error"
            assert frames[-1][1]["code"] == "review_unavailable"
            assert frames[-1][1]["retryable"] is True
            assert "很抱歉，無法協助" not in response.data.decode()


def test_pre_gate_failure_never_streams_business_body(monkeypatch):
    monkeypatch.setattr(chat_service, "get_runtime_config", lambda **kwargs: CONFIG)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)

    async def main(**kwargs):
        await kwargs["on_reply_delta"]("hidden candidate")
        return v4_result(reply="{}")

    async def gate(**kwargs):
        raise TimeoutError("private material")

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=main))
    monkeypatch.setattr(chat_service, "get_gate", lambda: SimpleNamespace(run=gate))
    response = app.test_client().post("/api/v1/chat/", json={"message": "hello", "stream": True})
    frames = events(response)
    assert [name for name, _ in frames if name in {"delta", "retract", "done", "error"}] == [
        "error"
    ]
    assert frames[-1][1]["code"] == "review_unavailable"
    assert "hidden candidate" not in response.data.decode()


def test_runtime_config_read_failure_closes_public_chat_before_main(monkeypatch):
    monkeypatch.setattr(
        chat_service,
        "get_runtime_config",
        lambda **kwargs: replace(CONFIG, source="safe_defaults", gate_enabled=False),
    )
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)

    async def forbidden_main(**kwargs):
        raise AssertionError("Main must not run with an unknown Gate setting")

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=forbidden_main))
    response = app.test_client().post("/api/v1/chat/", json={"message": "hello"})
    assert response.status_code == 503
    assert response.get_json()["code"] == "review_unavailable"
    assert response.get_json()["retryable"] is True


@pytest.mark.parametrize(
    "persisted",
    [
        {"gate_enabled": "true"},
        {"gate_enabled": True, "gate_model": ""},
        {"gate_enabled": True, "gate_prompt": ""},
        {"gate_enabled": True, "gate_timeout_seconds": 0},
    ],
)
def test_invalid_persisted_gate_setting_closes_public_chat(monkeypatch, persisted):
    config = _build_config(persisted, "firestore")
    assert config.source == "invalid_gate_config"
    monkeypatch.setattr(chat_service, "get_runtime_config", lambda **kwargs: config)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)

    async def forbidden_main(**kwargs):
        raise AssertionError("Main must not run with an invalid Gate setting")

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=forbidden_main))
    response = app.test_client().post("/api/v1/chat/", json={"message": "hello"})
    assert response.status_code == 503
    assert response.get_json()["code"] == "review_unavailable"
