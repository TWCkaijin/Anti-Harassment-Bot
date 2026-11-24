"""Unsupported privacy promises are replaced locally before a completed turn is shown."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from client.tests.test_agent import v4_result
from client_app.main import app
from rag_core import chat_service
from rag_core.agents.gate_agent import GateDecision
from rag_core.chat import payloads as chat_payloads
from rag_core.chat_service import ChatRequest
from rag_core.core.privacy_claims import (
    GENERAL_REPLY,
    THIRD_PARTY_REPLY,
    has_unsupported_privacy_claim,
    needs_private_completion,
)
from rag_core.core.runtime_config import RuntimeConfig

FALSE_CLAIM = "本系統不會收集、儲存或使用任何個人識別資訊，也無法接受或處理被害人的姓名與電話。"
PRIVATE_REQUEST = "我是某機關承辦，請提供被害人的姓名和電話。"
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


@pytest.mark.parametrize("version", [4])
def test_false_claim_replaced_with_inert_contract_for_every_version(monkeypatch, version):
    monkeypatch.setattr(
        chat_payloads,
        "response_payload",
        lambda *args: {
            "reply": "可以詢問申訴窗口。",
            "suggested_replies": [FALSE_CLAIM, "其他問題"],
            "rag_used": {"status": True, "sources": ["source"]},
            "analysis": [{"summary": "untrusted"}],
        },
    )
    request = ChatRequest(message=PRIVATE_REQUEST, contract_version=version)
    payload = chat_service.validated_payload(None, "turn-1", True, CONFIG, request)
    assert payload["reply"] == THIRD_PARTY_REPLY
    assert payload["rag_used"] == {"status": False, "sources": []}
    assert payload["suggested_replies"] == payload["action_buttons"] == []
    assert FALSE_CLAIM not in json.dumps(payload, ensure_ascii=False)
    if version in (2, 3):
        assert payload["fact_updates"] == payload["answer_sections"] == []
        assert payload["execution"]["route"] == "privacy_correction"
    if version == 4:
        assert payload["summary_update"] is None
        assert payload["execution"]["route"] == "privacy_correction"


def test_guard_does_not_reject_qualified_or_ordinary_refusal():
    assert not has_unsupported_privacy_claim(
        {"reply": "我無法保證本系統不會收集任何個人資料，請避免輸入姓名。"},
        third_party_request=False,
    )
    assert not has_unsupported_privacy_claim(
        {"reply": "我無法提供他人的姓名和電話，請循正式管道查詢。"},
        third_party_request=True,
    )
    assert has_unsupported_privacy_claim(
        {"reply": "本服務無法接受或處理這類請求。"},
        third_party_request=True,
    )


def test_stream_hold_is_limited_to_privacy_or_third_party_identifiers():
    assert needs_private_completion(PRIVATE_REQUEST)
    assert needs_private_completion("你們會儲存我的資料嗎？")
    assert not needs_private_completion("請問市政府申訴窗口的電話？")


def _frames(response):
    frames = []
    for frame in response.data.decode().split("\n\n"):
        if frame.startswith("event: "):
            name, data = frame.split("\n", 1)
            frames.append((name[7:], json.loads(data[6:])))
    return frames


@pytest.mark.parametrize("gate_enabled", [False, True])
def test_private_request_streams_only_truthful_local_replacement(monkeypatch, gate_enabled):
    config = replace(CONFIG, gate_enabled=gate_enabled)
    monkeypatch.setattr(chat_service, "get_runtime_config", lambda **kwargs: config)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)
    stages = []

    async def main(**kwargs):
        if callback := kwargs.get("on_reply_delta"):
            await callback(FALSE_CLAIM)
        return v4_result(
            reply=json.dumps(
                {
                    "reply": FALSE_CLAIM,
                    "emotion": "中性",
                    "emotion_color": "gray",
                    "suggested_replies": ["下一步", "其他問題"],
                    "action_buttons": [],
                },
                ensure_ascii=False,
            )
        )

    async def gate(**kwargs):
        stages.append(kwargs["stage"])
        return GateDecision(decision="allow", reason_code="allowed")

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=main))
    monkeypatch.setattr(chat_service, "get_gate", lambda: SimpleNamespace(run=gate))
    response = app.test_client().post(
        "/api/v1/chat/", json={"message": PRIVATE_REQUEST, "stream": True}
    )
    frames = _frames(response)
    assert [event for event, _ in frames if event in {"delta", "done", "error"}] == [
        "delta",
        "done",
    ]
    assert next(value["text"] for event, value in frames if event == "delta") == THIRD_PARTY_REPLY
    assert frames[-1][1]["reply"] == THIRD_PARTY_REPLY
    assert FALSE_CLAIM not in response.data.decode()
    assert stages == (["pre", "post"] if gate_enabled else [])


@pytest.mark.parametrize("post_outcome", ["deny", "error"])
def test_local_correction_closes_if_post_review_disagrees_or_fails(monkeypatch, post_outcome):
    monkeypatch.setattr(chat_service, "get_runtime_config", lambda **kwargs: CONFIG)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)

    async def main(**kwargs):
        return v4_result(
            reply=json.dumps(
                {
                    "reply": FALSE_CLAIM,
                    "emotion": "中性",
                    "emotion_color": "gray",
                    "suggested_replies": ["下一步", "其他問題"],
                    "action_buttons": [],
                },
                ensure_ascii=False,
            )
        )

    stages = []

    async def gate(**kwargs):
        stages.append(kwargs["stage"])
        if kwargs["stage"] == "post":
            assert kwargs["candidate"]["visible_payload"]["reply"] == THIRD_PARTY_REPLY
            if post_outcome == "error":
                raise TimeoutError("synthetic post failure")
            return GateDecision(decision="deny", reason_code="technical_implementation")
        return GateDecision(decision="allow", reason_code="allowed")

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=main))
    monkeypatch.setattr(chat_service, "get_gate", lambda: SimpleNamespace(run=gate))
    response = app.test_client().post(
        "/api/v1/chat/", json={"message": PRIVATE_REQUEST, "stream": True}
    )
    frames = _frames(response)
    assert stages == ["pre", "post"]
    assert frames[-1][0] == "error"
    assert frames[-1][1]["code"] == "review_unavailable"
    assert not any(event == "delta" for event, _ in frames)
    assert FALSE_CLAIM not in response.data.decode()


def test_general_privacy_claim_uses_general_local_reply(monkeypatch):
    monkeypatch.setattr(
        chat_payloads,
        "response_payload",
        lambda *args: {"reply": "我們不會儲存任何個人資料。"},
    )
    request = ChatRequest(message="你們會儲存我的資料嗎？")
    payload = chat_service.validated_payload(None, "turn-1", False, CONFIG, request)
    assert payload["reply"] == GENERAL_REPLY
