"""Gate and refusal model calls have separate tool-free contracts."""

import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from rag_core.agents import gate_agent, refusal_agent
from rag_core.agents.gate_agent import GateAgent
from rag_core.agents.refusal_agent import RefusalAgent
from rag_core.core.runtime_config import RuntimeConfig

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
    gate_timeout_seconds=10,
)


def fake_client(calls, answer):
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            assert kwargs["timeout"] == 10
            self.chat = SimpleNamespace(completions=self)
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(refusal=None, content=answer),
                    )
                ]
            )

    return Client


async def test_gate_model_is_independent_text_only_and_tool_free(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gate_agent,
        "AsyncOpenAI",
        fake_client(calls, '{"decision":"allow","reason_code":"allowed"}'),
    )
    decision = await GateAgent().run(
        stage="post",
        user_message="masked input",
        history=[],
        case_context={"summary": "masked summary"},
        candidate={"visible_payload": {"reply": "masked reply"}},
        runtime_config=CONFIG,
    )
    assert decision.decision == "allow"
    call = calls[0]
    assert call["model"] == "gate/model"
    assert "tools" not in call and "stream" not in call
    assert call["messages"][0]["content"] == "independent post policy"
    assert json.loads(call["messages"][1]["content"]) == {
        "stage": "post",
        "candidate": {"visible_payload": {"reply": "masked reply"}},
    }


async def test_pre_gate_uses_only_pre_policy_and_request_context(monkeypatch):
    calls = []
    monkeypatch.setattr(
        gate_agent,
        "AsyncOpenAI",
        fake_client(calls, '{"decision":"allow","reason_code":"allowed"}'),
    )
    await GateAgent().run(
        stage="pre",
        user_message="masked input",
        history=[{"role": "user", "content": "masked history"}],
        case_context={"summary": "masked summary"},
        runtime_config=CONFIG,
    )
    assert calls[0]["messages"][0]["content"] == "independent pre policy"
    assert json.loads(calls[0]["messages"][1]["content"]) == {
        "stage": "pre",
        "current_message": "masked input",
        "history": [{"role": "user", "content": "masked history"}],
        "case_context": {"summary": "masked summary"},
    }


async def test_main_model_refusal_is_bounded_and_receives_no_user_prose(monkeypatch):
    calls = []
    monkeypatch.setattr(
        refusal_agent,
        "AsyncOpenAI",
        fake_client(calls, '{"reply":"很抱歉，無法協助程式實作。"}'),
    )
    reply = await RefusalAgent().run(reason_code="technical_implementation", runtime_config=CONFIG)
    assert reply == "很抱歉，無法協助程式實作。"
    call = calls[0]
    assert call["model"] == "main/model"
    assert "tools" not in call and "stream" not in call
    assert "masked input" not in json.dumps(call, ensure_ascii=False)


@pytest.mark.parametrize(
    "invalid", ['{"decision":"deny","reason_code":"allowed"}', '{"reply":"```python"}']
)
async def test_invalid_gate_or_refusal_output_fails_closed(monkeypatch, invalid):
    calls = []
    if "decision" in invalid:
        monkeypatch.setattr(gate_agent, "AsyncOpenAI", fake_client(calls, invalid))
        with pytest.raises(ValueError):
            await GateAgent().run(
                stage="pre",
                user_message="masked",
                history=[],
                case_context={},
                runtime_config=CONFIG,
            )
    else:
        monkeypatch.setattr(refusal_agent, "AsyncOpenAI", fake_client(calls, invalid))
        with pytest.raises(ValueError):
            await RefusalAgent().run(reason_code="technical_implementation", runtime_config=CONFIG)


@pytest.mark.parametrize(
    ("stage", "choice", "score", "threshold", "expected"),
    [
        ("pre", "allowed", 0.8, 0.8, "allow"),
        ("post", "technical_implementation", 0.9, 0.8, "deny"),
        ("post", "allowed", 0.69, 0.7, None),
    ],
)
async def test_jev_decisions_routes_and_enforces_threshold(
    monkeypatch, stage, choice, score, threshold, expected
):
    client_type = httpx.AsyncClient

    def decisions(request):
        assert request.url == httpx.URL("https://openrouter.ai/api/alpha/decisions")
        assert request.headers["authorization"] == "Bearer synthetic-key"
        body = json.loads(request.content)
        assert body["model"] == "typesafe/jev-1.14-20261014"
        assert f"independent {stage} policy" in body["questions"]["review"]["instructions"]
        state = json.loads(body["state"])
        assert state["stage"] == stage
        assert set(state) == (
            {"stage", "candidate"}
            if stage == "post"
            else {"stage", "current_message", "history", "case_context"}
        )
        probabilities = {
            code: score if code == choice else (1 - score) / 2
            for code in body["questions"]["review"]["criteria"]
        }
        return httpx.Response(
            200,
            json={
                "answers": {
                    "review": {"type": "choice", "choice": choice, "probabilities": probabilities}
                }
            },
        )

    monkeypatch.setattr(
        gate_agent, "get_settings", lambda: SimpleNamespace(openrouter_api_key="synthetic-key")
    )
    monkeypatch.setattr(
        gate_agent.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(decisions), **kwargs),
    )
    config = replace(CONFIG, gate_model="typesafe/jev-1.14-20261014", gate_jev_threshold=threshold)
    call = GateAgent().run(
        stage=stage,
        user_message="masked synthetic question",
        history=[],
        case_context={},
        candidate={"reply": "masked synthetic reply"} if stage == "post" else None,
        runtime_config=config,
    )
    if expected is None:
        with pytest.raises(gate_agent.GateBelowThreshold) as error:
            await call
        assert error.value.probability == score
    else:
        result = await call
        assert result.decision == expected
        assert result.reason_code == choice
        assert result.probability == score
