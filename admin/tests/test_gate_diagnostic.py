"""Admin chat tests use the public Gate coordinator without saving rejected output."""

import base64
import json
from dataclasses import replace
from types import SimpleNamespace

from admin.tests.service_helpers import run_diagnostic
from admin_app import api
from rag_core import chat_service
from rag_core.agents.gate_agent import GateDecision
from rag_core.agents.openrouter_agent import AgentResult
from rag_core.core.runtime_config import RuntimeConfig


def test_chat_diagnostic_retracts_rejected_business_diagnostics(monkeypatch):
    config = RuntimeConfig(
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
    monkeypatch.setattr(api, "get_runtime_config", lambda **kwargs: config)
    calls = []

    async def main(**kwargs):
        kwargs["diagnostics"]["final_answer"] = "REJECTED BUSINESS OUTPUT"
        return AgentResult(
            reply=json.dumps(
                {
                    "reply": "REJECTED BUSINESS OUTPUT",
                    "emotion": "中性",
                    "emotion_color": "gray",
                    "suggested_replies": ["甲", "乙"],
                    "action_buttons": [],
                },
                ensure_ascii=False,
            )
        )

    async def gate(**kwargs):
        calls.append(kwargs["stage"])
        if kwargs["stage"] == "post" and kwargs["candidate"]["visible_payload"] == {
            "reply": "很抱歉，無法協助此類程式實作。"
        }:
            return GateDecision(decision="allow", reason_code="allowed")
        return GateDecision(
            decision="allow" if kwargs["stage"] == "pre" else "deny",
            reason_code="allowed" if kwargs["stage"] == "pre" else "technical_implementation",
        )

    async def refusal(**kwargs):
        return "很抱歉，無法協助此類程式實作。"

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=main))
    monkeypatch.setattr(chat_service, "get_gate", lambda: SimpleNamespace(run=gate))
    monkeypatch.setattr(chat_service, "get_refusal_agent", lambda: SimpleNamespace(run=refusal))

    response = run_diagnostic({"request": {"message": "請寫程式", "stream": False}})
    assert response.status_code == 200
    data = response.get_json()
    assert calls == ["pre", "post", "post"]
    assert data["response"]["reply"] == "很抱歉，無法協助此類程式實作。"
    reviews = data["diagnostics"]["gate_reviews"]
    assert [(item["stage"], item["decision"]) for item in reviews] == [
        ("pre", "allow"),
        ("post", "deny"),
        ("post", "allow"),
    ]
    assert all(item["duration_ms"] >= 0 and len(item["policy_sha256"]) == 64 for item in reviews)
    assert "REJECTED BUSINESS OUTPUT" not in json.dumps(data, ensure_ascii=False)


def test_chat_diagnostic_rejects_unavailable_runtime_config(monkeypatch):
    base = RuntimeConfig(
        openrouter_model="main/model",
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0.2,
        top_p=1,
        max_tokens=1200,
    )
    monkeypatch.setattr(
        api, "get_runtime_config", lambda **kwargs: replace(base, source="last_known_good")
    )
    response = run_diagnostic({"request": {"message": "hello"}})
    assert response.status_code == 503
    assert response.get_json()["code"] == "review_unavailable"


def test_admin_diagnostic_cannot_bypass_image_rollout_guard(monkeypatch):
    config = RuntimeConfig(
        openrouter_model="main/model",
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0.2,
        top_p=1,
        max_tokens=1200,
        enable_image_upload=True,
    )
    monkeypatch.setattr(api, "get_runtime_config", lambda **kwargs: config)
    image = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode()
    response = run_diagnostic({"request": {"message": "hello", "image_base64": image}})
    assert response.status_code == 422
    assert response.get_json()["code"] == "image_upload_disabled"
