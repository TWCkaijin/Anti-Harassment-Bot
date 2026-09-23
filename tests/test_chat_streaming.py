"""Streaming must deliver text before completion and validate all final metadata."""

import asyncio
import json
from dataclasses import replace

import pytest

from backend.app.agents import AgentResult
from backend.app.api import chat as chat_module
from backend.app.core.runtime_config import RuntimeConfig
from backend.app.main import app
from backend.app.rag.base import RAGVectorSearchError


def result(**changes):
    payload = {
        "reply": "先陪您整理。\n再說明下一步。",
        "emotion": "冷靜",
        "emotion_color": "green",
        "suggested_replies": ["我想了解流程", "我想補充資訊"],
        "interaction_mode": "answer",
        "clarifying_questions": [],
        "action_buttons": [],
    }
    payload.update(changes)
    return AgentResult(reply=json.dumps(payload, ensure_ascii=False))


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    config = RuntimeConfig(
        openrouter_model="test/model",
        rag_retrieval_top_k=3,
        enable_anonymization=False,
        temperature=0.2,
        top_p=1,
        max_tokens=1200,
    )
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: config)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)
    return config


def post_stream():
    return app.test_client().post(
        "/api/v1/chat/",
        json={"message": "我想聊聊", "use_rag": False, "stream": True},
        buffered=False,
    )


def events(body):
    parsed = []
    for frame in body.decode().split("\n\n"):
        if frame.startswith("event: "):
            event, data = frame.split("\n", 1)
            parsed.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return parsed


def test_delivers_delta_while_model_is_still_generating_and_metadata_only_at_done(monkeypatch):
    state = {}

    class Agent:
        async def run(self, on_reply_delta, **kwargs):
            state["continue"] = asyncio.Event()
            await on_reply_delta("先陪您整理。\n")
            await state["continue"].wait()
            await on_reply_delta("再說明下一步。")
            state["finished"] = True
            return result()

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = post_stream()
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "no-transform" in response.headers["Cache-Control"]
    assert "Content-Length" not in response.headers
    iterator = iter(response.response)
    assert next(iterator) == b": connected\n\n"
    assert events(next(iterator)) == [("delta", {"text": "先陪您整理。\n"})]
    assert "finished" not in state
    state["continue"].set()
    remaining = events(b"".join(iterator))
    assert [event for event, _ in remaining] == ["delta", "done"]
    assert remaining[-1][1]["emotion"] == "冷靜"
    assert remaining[-1][1]["suggested_replies"] == ["我想了解流程", "我想補充資訊"]
    assert remaining[-1][1]["reply"] == "先陪您整理。\n再說明下一步。"
    response.close()


def test_disconnect_cancels_producer_and_closes_its_upstream_work(monkeypatch):
    state = {}

    class Agent:
        async def run(self, on_reply_delta, **kwargs):
            try:
                await on_reply_delta("部分回覆")
                await asyncio.Event().wait()
            finally:
                state["closed"] = True

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = post_stream()
    iterator = iter(response.response)
    next(iterator)
    assert events(next(iterator))[0][0] == "delta"
    response.close()
    assert state["closed"] is True


def test_sends_heartbeat_during_model_or_retrieval_wait(monkeypatch):
    class Agent:
        async def run(self, **kwargs):
            await asyncio.Event().wait()

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    monkeypatch.setattr(chat_module, "STREAM_HEARTBEAT_SECONDS", 0.001)
    response = post_stream()
    iterator = iter(response.response)
    next(iterator)
    assert next(iterator) == b": keep-alive\n\n"
    response.close()


@pytest.mark.parametrize(
    ("error", "code", "status", "retryable"),
    [
        (TimeoutError("upstream timed out"), "upstream_invalid_response", 502, True),
        (RAGVectorSearchError("index unavailable"), "rag_unavailable", 503, True),
        (RuntimeError("unexpected internal failure"), "internal_error", 500, False),
    ],
)
def test_stream_failures_send_terminal_event_and_log_error(
    monkeypatch, caplog, error, code, status, retryable
):
    class Agent:
        async def run(self, **kwargs):
            raise error

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    with caplog.at_level("ERROR"):
        response = post_stream()
        frames = events(response.get_data())
    assert len(frames) == 1
    event, payload = frames[0]
    assert event == "error"
    assert payload["code"] == code
    assert payload["status"] == status
    assert payload["retryable"] is retryable
    assert "debug_message" not in payload
    failure = next(r for r in caplog.records if getattr(r, "event", None) == "chat_request_failed")
    assert failure.levelname == "ERROR"
    assert failure.error_message == str(error)
    assert failure.http_status == status
    assert failure.exc_info is not None
    response.close()


def test_partial_failure_does_not_invite_automatic_retry_or_emit_metadata(monkeypatch):
    class Agent:
        async def run(self, on_reply_delta, **kwargs):
            await on_reply_delta("尚未完成的文字")
            raise TimeoutError("provider disconnected")

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = post_stream()
    frames = events(response.get_data())
    assert [event for event, _ in frames] == ["delta", "error"]
    assert frames[-1][1]["retryable"] is False
    assert "尚未完成" in frames[-1][1]["detail"]
    assert "suggested_replies" not in frames[-1][1]
    response.close()


def test_invalid_final_metadata_never_emits_done_or_sensitive_schema_inputs(monkeypatch, caplog):
    class Agent:
        async def run(self, on_reply_delta, **kwargs):
            await on_reply_delta("部分回覆")
            return AgentResult(reply='{"reply":"private model content"}')

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    with caplog.at_level("ERROR"):
        response = post_stream()
        frames = events(response.get_data())
    assert [event for event, _ in frames] == ["delta", "error"]
    assert frames[-1][1]["code"] == "upstream_invalid_response"
    assert "private model content" not in caplog.text
    response.close()


def test_done_resolves_only_trusted_skill_actions(monkeypatch):
    class Agent:
        async def run(self, **kwargs):
            answer = result(
                action_buttons=[
                    {"action": "options", "id": "trusted"},
                    {"action": "url", "url": "https://unapproved.example.com"},
                ]
            )
            return AgentResult(
                reply=answer.reply,
                available_actions=[
                    {
                        "action": "options",
                        "id": "trusted",
                        "label": "選擇方向",
                        "title": "下一步",
                        "options": [
                            {"label": "流程", "value": "我想了解流程"},
                            {"label": "資料", "value": "整理資料"},
                        ],
                    }
                ],
            )

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = post_stream()
    frames = events(response.get_data())
    assert frames[0][0] == "done"
    actions = frames[0][1]["action_buttons"]
    assert len(actions) == 1
    assert actions[0]["id"] == "trusted"
    assert actions[0]["options"][0]["value"] == "我想了解流程"
    response.close()


def test_maintenance_remains_pre_stream_json_error(monkeypatch, runtime):
    monkeypatch.setattr(
        chat_module, "get_runtime_config", lambda: replace(runtime, maintenance_message="系統維護")
    )
    monkeypatch.setattr(chat_module, "get_agent", lambda: pytest.fail("must not initialize model"))
    response = post_stream()
    assert response.status_code == 503
    assert response.is_json
    assert response.get_json()["code"] == "maintenance"


def test_request_validation_stays_json_before_stream_starts():
    response = app.test_client().post("/api/v1/chat/", json={"message": "", "stream": True})
    assert response.status_code == 422
    assert response.is_json


def test_stream_request_still_enforces_app_check(monkeypatch):
    monkeypatch.setattr(app.extensions["chat_request_security"], "app_check_enabled", True)
    monkeypatch.setattr(chat_module, "get_agent", lambda: pytest.fail("must not initialize model"))
    response = post_stream()
    assert response.status_code == 401
    assert response.is_json


def test_stream_request_still_enforces_rate_limit(monkeypatch):
    from backend.app.core.security import ProcessLocalSlidingWindowRateLimiter

    security = app.extensions["chat_request_security"]
    monkeypatch.setattr(security, "rate_limit_enabled", True)
    monkeypatch.setattr(security, "rate_limiter", ProcessLocalSlidingWindowRateLimiter(1, 60))

    class Agent:
        async def run(self, **kwargs):
            return result()

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    first = post_stream()
    assert events(first.get_data())[0][0] == "done"
    first.close()
    second = post_stream()
    assert second.status_code == 429
    assert second.is_json


def test_real_chat_stream_passes_through_function_wrapper_without_buffering(monkeypatch):
    from flask import Request

    import main as function_main

    state = {}

    class Agent:
        async def run(self, on_reply_delta, **kwargs):
            state["continue"] = asyncio.Event()
            await on_reply_delta("先陪您整理。\n")
            await state["continue"].wait()
            await on_reply_delta("再說明下一步。")
            return result()

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = function_main.handle_request(
        Request.from_values(
            "/v1/chat/",
            method="POST",
            content_type="application/json",
            data=json.dumps({"message": "聊聊", "stream": True}),
        )
    )
    iterator = iter(response.response)
    assert next(iterator) == b": connected\n\n"
    assert events(next(iterator)) == [("delta", {"text": "先陪您整理。\n"})]
    state["continue"].set()
    assert events(b"".join(iterator))[-1][0] == "done"
    response.close()


def test_first_character_is_yielded_before_producer_can_continue(monkeypatch):
    state = {}

    class Agent:
        async def run(self, on_reply_delta, **kwargs):
            await on_reply_delta("第")
            # Cached SDK chunks and synchronous work can execute without any
            # additional await. The HTTP consumer must get the first char first.
            state["producer_continued"] = True
            await on_reply_delta("一個字")
            return result(reply="第一個字")

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = post_stream()
    iterator = iter(response.response)
    assert next(iterator) == b": connected\n\n"
    try:
        assert events(next(iterator)) == [("delta", {"text": "第"})]
        assert "producer_continued" not in state
    finally:
        response.close()


def test_guidance_streams_before_done_and_preserves_partial_question_text(monkeypatch):
    state = {}

    class Agent:
        async def run(self, on_reply_delta, on_guidance, **kwargs):
            await on_reply_delta("我想先了解情況。")
            await on_guidance({"interaction_mode": "clarify", "clarifying_questions": ["在哪"]})
            state["continued"] = True
            await on_guidance(
                {
                    "interaction_mode": "clarify",
                    "clarifying_questions": ["在哪裡發生？"],
                    "suggested_replies": ["在工作", "在學校"],
                }
            )
            return result(
                reply="我想先了解情況。",
                interaction_mode="clarify",
                clarifying_questions=["在哪裡發生？"],
            )

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = post_stream()
    iterator = iter(response.response)
    next(iterator)
    assert events(next(iterator))[0][0] == "delta"
    assert events(next(iterator)) == [
        ("guidance", {"interaction_mode": "clarify", "clarifying_questions": ["在哪"]})
    ]
    assert "continued" not in state
    remaining = events(b"".join(iterator))
    assert [name for name, _ in remaining] == ["guidance", "done"]
    assert remaining[0][1]["suggested_replies"][0] == "在工作"
    response.close()


def test_guidance_only_failure_does_not_auto_replay(monkeypatch):
    class Agent:
        async def run(self, on_guidance, **kwargs):
            await on_guidance({"interaction_mode": "answer", "suggested_replies": ["我想"]})
            raise TimeoutError("generation interrupted")

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    response = post_stream()
    frames = events(response.get_data())
    assert [name for name, _ in frames] == ["guidance", "error"]
    assert frames[-1][1]["retryable"] is False
    response.close()


def test_stream_timing_records_ready_and_yield_without_message_content(monkeypatch, caplog):
    class Agent:
        async def run(self, on_reply_delta, on_guidance, **kwargs):
            await on_reply_delta("private-generated-text")
            await on_guidance(
                {"interaction_mode": "answer", "suggested_replies": ["private-option"]}
            )
            return result()

    monkeypatch.setattr(chat_module, "get_agent", Agent)
    with caplog.at_level("INFO"):
        response = post_stream()
        response.get_data()
        response.close()
    record = next(r for r in caplog.records if getattr(r, "event", None) == "chat_stream_timing")
    assert record.outcome == "done"
    assert 0 <= record.first_reply_ready_ms <= record.first_reply_yield_ms <= record.duration_ms
    assert (
        0 <= record.first_guidance_ready_ms <= record.first_guidance_yield_ms <= record.duration_ms
    )
    assert "private-generated-text" not in caplog.text
    assert "private-option" not in caplog.text


def test_openrouter_first_content_reaches_http_before_next_ready_sdk_chunk(monkeypatch):
    import backend.app.agents.openrouter_agent as agent_module
    from tests.test_agent import FakeCompletions, make_agent
    from tests.test_agent_streaming import FakeStream, chunk

    state = []

    async def before_chunk(index):
        state.append(index)

    raw = result(reply="你好，慢慢說。").reply
    boundary = raw.index("好")
    stream = FakeStream(
        [chunk(raw[:boundary]), chunk(raw[boundary:]), chunk(finish="stop")],
        before_chunk=before_chunk,
    )
    completions = FakeCompletions([stream])
    monkeypatch.setattr(chat_module, "get_agent", lambda: make_agent(completions))
    monkeypatch.setattr(agent_module, "get_runtime_config", chat_module.get_runtime_config)
    monkeypatch.setattr(agent_module, "get_matching_scenario_scripts", lambda *args, **kwargs: ())
    response = post_stream()
    iterator = iter(response.response)
    next(iterator)
    try:
        assert events(next(iterator)) == [("delta", {"text": "你"})]
        assert state == [0]
        assert completions.calls[0]["stream"] is True
        assert not stream.completed
    finally:
        response.close()
    assert stream.closed
