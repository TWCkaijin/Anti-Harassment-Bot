"""Deterministic two-stage Gate races; no external model calls."""

import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag_core.agents.gate_agent import GateDecision
from rag_core.chat_service import ChatRequest, gate_refusal_payload
from rag_core.core.gate_policy import FIXED_SHORT_REFUSALS
from rag_core.core.gated_chat import GateUnavailableError, run_gated_chat
from rag_core.core.runtime_config import (
    RuntimeConfig,
    _default_runtime_document,
    _missing_runtime_defaults,
    client_settings,
    validate_runtime_config_update,
)
from shared.tests.reliability.gate_eval import summarize

CONFIG = RuntimeConfig(
    openrouter_model="main/model",
    rag_retrieval_top_k=3,
    enable_anonymization=True,
    temperature=0.2,
    top_p=1,
    max_tokens=1200,
    gate_enabled=True,
    gate_model="gate/model",
    gate_prompt="independent pre policy",
    gate_post_prompt="independent post policy",
    gate_timeout_seconds=0.5,
)
ALLOW = GateDecision(decision="allow", reason_code="allowed")
DENY = GateDecision(decision="deny", reason_code="technical_implementation")


def launch(
    main, gate, refusal, *, callbacks=None, on_retract=None, config=CONFIG, review_trace=None
):
    return run_gated_chat(
        SimpleNamespace(run=main),
        SimpleNamespace(run=gate),
        SimpleNamespace(run=refusal),
        run_kwargs={
            "user_message": "masked message",
            "history": [{"role": "user", "content": "masked history"}],
            "runtime_config": config,
            "use_rag": True,
        },
        runtime_config=config,
        build_payload=lambda result: {"reply": result},
        build_refusal_payload=lambda reply: {"reply": reply, "refusal": True},
        callbacks=callbacks,
        on_retract=on_retract,
        review_trace=review_trace,
    )


async def test_pre_gate_runs_concurrently_and_holds_visible_output():
    buffered, release = asyncio.Event(), asyncio.Event()
    visible = []

    async def main(**kwargs):
        await kwargs["on_reply_delta"]("answer")
        buffered.set()
        return "answer"

    async def gate(**kwargs):
        assert kwargs["user_message"] == "masked message"
        assert kwargs["history"][0]["content"] == "masked history"
        if kwargs["stage"] == "pre":
            await buffered.wait()
            await release.wait()
        else:
            assert kwargs["candidate"]["visible_payload"] == {"reply": "answer"}
        return ALLOW

    async def delta(text):
        visible.append(text)

    task = asyncio.create_task(launch(main, gate, AsyncMock(), callbacks={"on_reply_delta": delta}))
    await asyncio.wait_for(buffered.wait(), 1)
    assert visible == []
    release.set()
    assert await asyncio.wait_for(task, 1) == {"reply": "answer"}
    assert visible == ["answer"]


async def test_pre_deny_cancels_business_and_reruns_main_for_refusal():
    started, stopped = asyncio.Event(), asyncio.Event()
    visible = []

    async def main(**kwargs):
        try:
            await kwargs["on_reply_delta"]("hidden answer")
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def gate(**kwargs):
        if kwargs["stage"] == "pre":
            await started.wait()
            return DENY
        assert kwargs["candidate"]["visible_payload"] == {"reply": "很抱歉，無法協助此類程式實作。"}
        return ALLOW

    async def refusal(**kwargs):
        assert kwargs["reason_code"] == "technical_implementation"
        return "很抱歉，無法協助此類程式實作。"

    async def delta(text):
        visible.append(text)

    result = await asyncio.wait_for(
        launch(main, gate, refusal, callbacks={"on_reply_delta": delta}), 1
    )
    assert stopped.is_set()
    assert result == {"reply": "很抱歉，無法協助此類程式實作。", "refusal": True}
    assert visible == [result["reply"]]


async def test_failed_refusal_regeneration_uses_fixed_short_refusal():
    visible = []

    async def main(**kwargs):
        return "hidden answer"

    async def gate(**kwargs):
        return DENY

    async def refusal(**kwargs):
        raise TimeoutError("private upstream content")

    async def delta(text):
        visible.append(text)

    result = await launch(main, gate, refusal, callbacks={"on_reply_delta": delta})
    assert result == {
        "reply": FIXED_SHORT_REFUSALS["technical_implementation"],
        "refusal": True,
    }
    assert visible == [result["reply"]]


async def test_generated_refusal_with_false_privacy_promise_uses_fixed_fallback():
    stages = []

    async def gate(**kwargs):
        stages.append(kwargs["stage"])
        return DENY

    result = await launch(
        AsyncMock(return_value="hidden"),
        gate,
        AsyncMock(return_value="本系統不會收集、儲存或使用任何個人識別資訊。"),
    )
    assert result["reply"] == FIXED_SHORT_REFUSALS["technical_implementation"]
    assert stages == ["pre"]


async def test_post_deny_retracts_before_refusal_and_never_commits_candidate():
    visible = []

    async def main(**kwargs):
        await kwargs["on_reply_delta"]("provisional")
        await kwargs["on_analysis"]({"stage": "test", "text": "provisional analysis"})
        return "provisional"

    async def gate(**kwargs):
        if kwargs["stage"] == "post":
            if kwargs["candidate"]["visible_payload"] == {"reply": "short refusal"}:
                return ALLOW
            assert kwargs["candidate"]["visible_payload"] == {"reply": "provisional"}
            assert len(kwargs["candidate"]["published_events"]) == 2
            return DENY
        return ALLOW

    async def refusal(**kwargs):
        visible.append("rerun-main")
        return "short refusal"

    async def delta(text):
        visible.append(("delta", text))

    async def analysis(value):
        visible.append(("analysis", value))

    async def retract(code):
        visible.append(("retract", code))

    result = await launch(
        main,
        gate,
        refusal,
        callbacks={"on_reply_delta": delta, "on_analysis": analysis},
        on_retract=retract,
    )
    assert result == {"reply": "short refusal", "refusal": True}
    assert visible == [
        ("delta", "provisional"),
        ("analysis", {"stage": "test", "text": "provisional analysis"}),
        ("retract", "gate_denied"),
        "rerun-main",
        ("delta", "short refusal"),
    ]


async def test_generated_refusal_denied_by_post_gate_uses_fixed_fallback():
    visible = []
    reviews = []

    async def gate(**kwargs):
        reviews.append(kwargs["stage"])
        if kwargs["stage"] == "pre":
            return DENY
        assert kwargs["candidate"]["visible_payload"] == {"reply": "請執行這段程式碼"}
        return DENY

    async def delta(text):
        visible.append(text)

    result = await launch(
        AsyncMock(return_value="hidden"),
        gate,
        AsyncMock(return_value="請執行這段程式碼"),
        callbacks={"on_reply_delta": delta},
    )
    assert reviews == ["pre", "post"]
    assert result["reply"] == FIXED_SHORT_REFUSALS["technical_implementation"]
    assert visible == [result["reply"]]


async def test_refusal_generation_and_review_share_one_timeout():
    async def gate(**kwargs):
        if kwargs["stage"] == "pre":
            return DENY
        await asyncio.Event().wait()

    result = await asyncio.wait_for(
        launch(
            AsyncMock(return_value="hidden"),
            gate,
            AsyncMock(return_value="short refusal"),
            config=replace(CONFIG, gate_timeout_seconds=0.02),
        ),
        1,
    )
    assert result["reply"] == FIXED_SHORT_REFUSALS["technical_implementation"]


@pytest.mark.parametrize("stage", ["pre", "post"])
async def test_gate_failure_is_closed_and_retracts_post_output(stage):
    visible = []

    async def main(**kwargs):
        await kwargs["on_reply_delta"]("candidate")
        return "candidate"

    async def gate(**kwargs):
        if kwargs["stage"] == stage:
            raise TimeoutError("private material")
        return ALLOW

    async def delta(text):
        visible.append(("delta", text))

    async def retract(code):
        visible.append(("retract", code))

    with pytest.raises(GateUnavailableError):
        await launch(
            main,
            gate,
            AsyncMock(),
            callbacks={"on_reply_delta": delta},
            on_retract=retract,
        )
    assert visible == (
        [] if stage == "pre" else [("delta", "candidate"), ("retract", "review_unavailable")]
    )


async def test_disabled_gate_preserves_direct_main_path():
    gate = AsyncMock(side_effect=AssertionError("Gate must not run"))
    main = AsyncMock(return_value="normal")
    result = await launch(
        main,
        gate,
        AsyncMock(),
        config=replace(CONFIG, gate_enabled=False),
    )
    assert result == {"reply": "normal"}
    gate.assert_not_awaited()


@pytest.mark.parametrize("pre_decision", [ALLOW, DENY])
async def test_review_trace_hashes_the_actual_pre_and_post_prompts(pre_decision):
    reviews = []

    async def gate(**kwargs):
        return pre_decision if kwargs["stage"] == "pre" else ALLOW

    result = await launch(
        AsyncMock(return_value="allowed main reply"),
        gate,
        AsyncMock(return_value="safe short refusal"),
        review_trace=reviews,
    )
    assert result["reply"] == (
        "allowed main reply" if pre_decision == ALLOW else "safe short refusal"
    )
    assert [(review["stage"], review["policy_sha256"]) for review in reviews] == [
        ("pre", hashlib.sha256(CONFIG.gate_prompt.encode()).hexdigest()),
        ("post", hashlib.sha256(CONFIG.gate_post_prompt.encode()).hexdigest()),
    ]


@pytest.mark.parametrize("version", [4])
def test_refusal_payload_keeps_each_client_contract_inert(version):
    request = ChatRequest(message="hello", contract_version=version)
    payload = gate_refusal_payload("short refusal", "turn-1", True, request)
    assert payload["turn_id"] == payload["session_id"] == "turn-1"
    assert payload["reply"] == "short refusal"
    assert payload["rag_used"] == {"status": False, "sources": []}
    assert payload["suggested_replies"] == payload["action_buttons"] == []
    if version in (2, 3):
        assert payload["facts_revision"] == 0 and payload["fact_updates"] == []
    if version == 4:
        assert payload["context_revision"] == 0 and payload["summary_update"] is None


async def test_gate_always_masks_pre_and_post_transfers_even_when_main_masking_is_off():
    config = replace(CONFIG, enable_anonymization=False)
    transfers = []

    async def main(**kwargs):
        await kwargs["on_reply_delta"]("回信 alice@example.com")
        return "回信 alice@example.com"

    async def gate(**kwargs):
        transfers.append(kwargs)
        return ALLOW

    async def delta(text):
        pass

    await run_gated_chat(
        SimpleNamespace(run=main),
        SimpleNamespace(run=gate),
        SimpleNamespace(run=AsyncMock()),
        run_kwargs={
            "user_message": "請寄給 alice@example.com",
            "history": [{"role": "user", "content": "我的電話 0912345678"}],
            "case_context": {"summary": "聯絡 alice@example.com", "facts": {}},
            "clarification_answer": {
                "value": "0912345678",
                "validation_token": "secret-token-keep-local",
            },
            "runtime_config": config,
        },
        runtime_config=config,
        build_payload=lambda result: {
            "reply": result,
            "analysis": [{"text": "電話 0912345678"}],
            "action_buttons": [{"label": "alice@example.com", "url": "https://secret.example"}],
        },
        build_refusal_payload=lambda reply: {"reply": reply},
        callbacks={"on_reply_delta": delta},
    )
    assert len(transfers) == 2
    for transfer in transfers:
        sent = json.dumps(
            {key: value for key, value in transfer.items() if key != "runtime_config"},
            ensure_ascii=False,
        )
        for private in (
            "alice@example.com",
            "0912345678",
            "secret-token-keep-local",
            "https://secret.example",
        ):
            assert private not in sent
    assert "[電子郵件]" in json.dumps(transfers[1]["candidate"], ensure_ascii=False)


def test_semantic_eval_treats_gate_errors_as_unavailable_not_allowed():
    summary = summarize(
        [
            {
                "expected": "deny",
                "decision": "error",
                "error_type": "TimeoutError",
                "latency_ms": 10,
            },
            {
                "expected": "deny",
                "decision": "allow",
                "error_type": None,
                "latency_ms": 2,
            },
            {
                "expected": "allow",
                "decision": "deny",
                "error_type": None,
                "latency_ms": 3,
            },
        ]
    )
    assert summary["missed_denials"] == 1
    assert summary["false_denials"] == 1
    assert summary["unavailable"] == 1


def test_gate_settings_are_admin_only_and_backfill_current_main_model():
    assert "gate_model" not in client_settings(CONFIG)
    assert "gate_prompt" not in client_settings(CONFIG)
    assert "gate_post_prompt" not in client_settings(CONFIG)
    missing = _missing_runtime_defaults(
        {"openrouter_model": "custom/main"}, _default_runtime_document()
    )
    assert missing["gate_model"] == "custom/main"
    assert missing["gate_enabled"] is False
    assert missing["gate_timeout_seconds"] == 10.0
    assert missing["gate_post_prompt"] == _default_runtime_document()["gate_post_prompt"]
    assert validate_runtime_config_update(
        {"gate_enabled": True, "gate_model": "review/model", "gate_timeout_seconds": 7}
    ) == {"gate_enabled": True, "gate_model": "review/model", "gate_timeout_seconds": 7.0}
