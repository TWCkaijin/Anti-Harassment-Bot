"""Real upstream streaming, incremental reply decoding, and tool round boundaries."""

import asyncio
import json
from types import SimpleNamespace

import pytest

import backend.app.agents.openrouter_agent as agent_module
from backend.app.agents.openrouter_agent import AgentContractError
from backend.app.core.chat_response import ASSISTANT_REPLY_MAX_LENGTH, AssistantChatResponse
from backend.app.core.streaming_reply import (
    MAX_STREAM_RESPONSE_LENGTH,
    ReplyJSONDecoder,
    StreamReplyError,
)
from tests.test_agent import FakeCompletions, fake_runtime_config, make_agent


def chunk(content=None, *, finish=None, tools=None, refusal=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(content=content, tool_calls=tools, refusal=refusal),
                finish_reason=finish,
            )
        ]
    )


def tool_fragment(*, index=0, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeStream:
    def __init__(self, chunks, before_chunk=None):
        self.chunks = chunks
        self.before_chunk = before_chunk
        self.closed = False
        self.completed = False

    async def __aiter__(self):
        for index, item in enumerate(self.chunks):
            if self.before_chunk:
                await self.before_chunk(index)
            if isinstance(item, Exception):
                raise item
            yield item
        self.completed = True

    async def close(self):
        self.closed = True


def response(reply="我在這裡陪著你。"):
    return {
        "reply": reply,
        "emotion": "焦慮",
        "emotion_color": "yellow",
        "suggested_replies": ["我想繼續說", "我想先休息"],
        "action_buttons": [],
        "interaction_mode": "answer",
        "clarifying_questions": [],
    }


@pytest.fixture(autouse=True)
def agent_dependencies(monkeypatch):
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())
    monkeypatch.setattr(
        agent_module, "get_matching_scenario_scripts", lambda user_message, history=None: ()
    )


@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_decoder_handles_every_character_boundary_without_exposing_metadata(ensure_ascii):
    expected = '繁體中文："你好"\n第二行\t\\路徑 🙂🚀'
    payload = response(expected)
    # Metadata may precede reply for providers ignoring property ordering.
    payload = {"metadata": {"reply": "不可顯示"}, "emotion": "焦慮", **payload}
    text = json.dumps(payload, ensure_ascii=ensure_ascii)
    decoder = ReplyJSONDecoder()
    actual = "".join(decoder.feed(char) for char in text)
    decoder.finish()
    assert actual == expected


@pytest.mark.parametrize("reply", [r"第一行\n第二行", r"第一行\r\n第二行\t結束", "尾端\\", r"\\n"])
def test_decoder_control_sequence_normalization_matches_final_response(reply):
    payload = response(reply)
    decoder = ReplyJSONDecoder()
    actual = "".join(decoder.feed(char) for char in json.dumps(payload))
    decoder.finish()
    assert actual == AssistantChatResponse.model_validate(payload).reply


@pytest.mark.parametrize(
    "raw",
    [
        '{"reply":"unfinished',
        '{"reply":"unfinished\\',
        '{"reply":"\\uD83D"}',
        '{"reply":"\\uDE80"}',
        '{"reply":"\\uD83Dx"}',
        '{"reply":"\\uXXXX"}',
        '{"reply":"bad\\x"}',
        '{"reply":"bad\ncontrol"}',
        '{"reply":4}',
        '{"reply":"one","reply":"two"}',
        '{"reply":"one",}',
        '{"reply":"one"} trailing',
        '{"reply":"one","metadata": [1,]}',
        '{"reply":"one","metadata": {]}',
        '{"emotion":"焦慮"}',
    ],
)
def test_decoder_rejects_malformed_or_truncated_json(raw):
    decoder = ReplyJSONDecoder()
    with pytest.raises(StreamReplyError):
        for char in raw:
            decoder.feed(char)
        decoder.finish()


def test_decoder_limits_reply_length_before_exposing_oversized_text():
    decoder = ReplyJSONDecoder()
    assert decoder.feed('{"reply":"' + "字" * ASSISTANT_REPLY_MAX_LENGTH) == (
        "字" * ASSISTANT_REPLY_MAX_LENGTH
    )
    with pytest.raises(StreamReplyError, match="size limit"):
        decoder.feed("多")


def test_decoder_bounds_raw_input_and_nesting():
    with pytest.raises(StreamReplyError, match="size limit"):
        ReplyJSONDecoder().feed(" " * (MAX_STREAM_RESPONSE_LENGTH + 1))
    with pytest.raises(StreamReplyError, match="nesting"):
        ReplyJSONDecoder().feed('{"metadata":' + "[" * 33)


@pytest.mark.asyncio
async def test_stream_emits_decoded_reply_before_upstream_completion():
    received = []

    async def verify_before_next_chunk(index):
        if index == 1:
            assert received == ["我在"]
            assert not stream.completed

    raw = json.dumps(response("我在這裡。"), ensure_ascii=False)
    boundary = raw.index("這")
    stream = FakeStream(
        [chunk(raw[:boundary]), chunk(raw[boundary:]), chunk(finish="stop")],
        before_chunk=verify_before_next_chunk,
    )
    completions = FakeCompletions([stream])

    async def on_delta(text):
        received.append(text)

    result = await make_agent(completions).run("我很害怕", on_reply_delta=on_delta)

    assert "".join(received) == "我在這裡。"
    assert result.reply == raw
    assert completions.calls[0]["stream"] is True
    assert stream.closed
    properties = completions.calls[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert next(iter(properties)) == "reply"


@pytest.mark.asyncio
async def test_stream_never_exposes_reasoning_or_usage_chunks():
    reasoning = chunk()
    reasoning.choices[0].delta.reasoning = "hidden chain of thought"
    reasoning.choices[0].delta.reasoning_content = "hidden analysis"
    stream = FakeStream(
        [
            reasoning,
            chunk(json.dumps(response("安全回覆"))),
            chunk(finish="stop"),
            SimpleNamespace(choices=[], usage={"completion_tokens": 8}),
        ]
    )
    received = []

    async def on_delta(text):
        received.append(text)

    await make_agent(FakeCompletions([stream])).run("我很害怕", on_reply_delta=on_delta)
    assert received == ["安全回覆"]
    assert stream.closed


@pytest.mark.asyncio
async def test_stream_accumulates_tool_fragments_then_streams_only_final_reply():
    received = []
    first = FakeStream(
        [
            chunk(
                tools=[
                    tool_fragment(
                        id="tool-1", name="retrieve_harassment_", arguments='{"query":"申訴'
                    )
                ]
            ),
            chunk(tools=[tool_fragment(name="knowledge", arguments='期限","data_type":"law"}')]),
            chunk(finish="tool_calls"),
        ]
    )
    raw = json.dumps(response("可以一起了解申訴期限。"), ensure_ascii=False)

    async def before_final(index):
        if index == 0:
            assert received == []
            assert first.closed
            assert agent.rag.calls[0]["query"] == "申訴期限"

    final = FakeStream([chunk(char) for char in raw] + [chunk(finish="stop")], before_final)
    completions = FakeCompletions([first, final])
    agent = make_agent(completions)

    async def on_delta(text):
        received.append(text)

    result = await agent.run("我想了解申訴期限", on_reply_delta=on_delta)

    assert "".join(received) == "可以一起了解申訴期限。"
    assert result.rag_used
    assert result.sources[0]["doc_id"] == "law-13"
    assert result.tool_calls[0]["arguments"] == {"query": "申訴期限", "data_type": "law"}
    assert len(completions.calls) == 2
    assert all(call["stream"] is True for call in completions.calls)
    assert first.closed and final.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks,error",
    [
        ([chunk('{"reply":"partial'), chunk(finish="stop")], "Incomplete"),
        ([chunk('{"reply":"partial'), chunk(finish="length")], "length"),
        ([chunk('{"reply":"partial')], "before a complete"),
        ([chunk(refusal="I cannot answer")], "refused"),
        ([chunk(finish="content_filter")], "content_filter"),
        ([chunk('{"reply":"bad\\x')], "escape"),
        ([chunk('{"reply":"valid"}'), chunk(finish="tool_calls")], "before a complete"),
        ([RuntimeError("upstream disconnected")], "upstream disconnected"),
    ],
)
async def test_stream_failures_propagate_and_close_transport(chunks, error):
    stream = FakeStream(chunks)

    async def on_delta(text):
        pass

    with pytest.raises((AgentContractError, RuntimeError), match=error):
        await make_agent(FakeCompletions([stream])).run("我很害怕", on_reply_delta=on_delta)
    assert stream.closed


@pytest.mark.asyncio
async def test_stream_rejects_mixed_reply_and_subsequent_tool_call():
    stream = FakeStream(
        [
            chunk('{"reply":"先回答'),
            chunk(tools=[tool_fragment(id="tool-1", name="retrieve_harassment_knowledge")]),
            chunk(finish="tool_calls"),
        ]
    )

    async def on_delta(text):
        pass

    with pytest.raises(AgentContractError, match="mixed"):
        await make_agent(FakeCompletions([stream])).run("我很害怕", on_reply_delta=on_delta)
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments,error",
    [
        ("unsupported", "{}", "unsupported"),
        ("retrieve_harassment_knowledge", '{"query":', "valid JSON"),
        ("retrieve_harassment_knowledge", '{"query":"申訴","data_type":"unknown"}', "invalid"),
    ],
)
async def test_stream_rejects_invalid_tool_contract(name, arguments, error):
    stream = FakeStream(
        [
            chunk(tools=[tool_fragment(id="tool-1", name=name, arguments=arguments)]),
            chunk(finish="tool_calls"),
        ]
    )
    agent = make_agent(FakeCompletions([stream]))

    async def on_delta(text):
        pass

    with pytest.raises(AgentContractError, match=error):
        await agent.run("我想了解申訴期限", on_reply_delta=on_delta)
    assert not agent.rag.calls
    assert stream.closed


@pytest.mark.asyncio
async def test_stream_cancellation_closes_upstream():
    received = asyncio.Event()
    hold = asyncio.Event()

    async def block_after_first_chunk(index):
        if index == 1:
            await hold.wait()

    stream = FakeStream(
        [chunk('{"reply":"開始'), chunk('"}')], before_chunk=block_after_first_chunk
    )

    async def on_delta(text):
        received.set()

    task = asyncio.create_task(
        make_agent(FakeCompletions([stream])).run("我很害怕", on_reply_delta=on_delta)
    )
    await asyncio.wait_for(received.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


@pytest.mark.asyncio
async def test_streaming_run_uses_and_closes_a_request_scoped_sdk_client(monkeypatch):
    stream = FakeStream([chunk(json.dumps(response())), chunk(finish="stop")])
    clients = []

    class RequestClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions([stream]))
            self.closed = False
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.closed = True

    monkeypatch.setattr(agent_module, "AsyncOpenAI", RequestClient)
    agent = make_agent(FakeCompletions([]))
    agent.client = RequestClient()

    async def on_delta(text):
        pass

    await agent.run("我很害怕", on_reply_delta=on_delta)
    assert len(clients) == 2
    assert not clients[0].chat.completions.calls
    assert clients[1].chat.completions.calls[0]["stream"]
    assert clients[1].closed


@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_decoder_streams_guidance_at_every_character_boundary(ensure_ascii):
    payload = {
        "reply": "陪您一起整理。",
        "interaction_mode": "clarify",
        "clarifying_questions": ["發生在「學校」\\n還是工作場所？🙂"],
        "suggested_replies": ["在「學校」", "在工作場所\\t"],
        "action_buttons": [{"action": "url", "url": "https://example.test/private"}],
        "emotion": "不可混入引導預覽",
        "emotion_color": "yellow",
        "metadata": {"suggested_replies": ["不可混入引導預覽"]},
    }
    raw = json.dumps(payload, ensure_ascii=ensure_ascii)
    decoder = ReplyJSONDecoder()
    snapshots = []
    replies = []
    for char in raw:
        replies.append(decoder.feed(char))
        if (snapshot := decoder.take_guidance()) is not None:
            snapshots.append(snapshot)
    decoder.finish()
    assert "".join(replies) == payload["reply"]
    assert {"interaction_mode": "clarify"} in snapshots
    assert {"interaction_mode": "clarify", "clarifying_questions": ["發"]} in snapshots
    expected = AssistantChatResponse.model_validate(payload)
    assert snapshots[-1] == {
        "interaction_mode": expected.interaction_mode,
        "clarifying_questions": expected.clarifying_questions,
        "suggested_replies": expected.suggested_replies,
    }
    assert all(
        set(snapshot) <= {"interaction_mode", "clarifying_questions", "suggested_replies"}
        for snapshot in snapshots
    )
    assert "不可混入引導預覽" not in json.dumps(snapshots, ensure_ascii=False)
    assert "https://" not in json.dumps(snapshots)
    assert decoder.take_guidance() is None


def test_decoder_does_not_emit_partial_or_invalid_mode():
    decoder = ReplyJSONDecoder()
    assert decoder.feed('{"reply":"文字","interaction_mode":"clar') == "文字"
    assert decoder.take_guidance() is None
    decoder.feed('ify"')
    assert decoder.take_guidance() == {"interaction_mode": "clarify"}
    other = ReplyJSONDecoder()
    with pytest.raises(StreamReplyError, match="interaction mode"):
        other.feed('{"reply":"文字","interaction_mode":"unknown"')
    assert other.take_guidance() is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"reply":"文字","suggested_replies":["好",]}',
        '{"reply":"文字","suggested_replies":[5]}',
        '{"reply":"文字","suggested_replies":"好"}',
        '{"reply":"文字","clarifying_questions":[["巢狀"]]}',
        '{"reply":"文字","clarifying_questions":["\\uD83D"]}',
        '{"reply":"文字","suggested_replies":["bad\\x"]}',
        '{"reply":"文字","interaction_mode":null}',
        '{"reply":"文字","clarifying_questions":["1","2","3","4"]}',
        '{"reply":"文字","suggested_replies":["1","2","3","4","5"]}',
    ],
)
def test_decoder_rejects_malformed_guidance(raw):
    decoder = ReplyJSONDecoder()
    with pytest.raises(StreamReplyError):
        for char in raw:
            decoder.feed(char)
        decoder.finish()


def test_decoder_bounds_guidance_before_exposing_oversized_text():
    decoder = ReplyJSONDecoder()
    decoder.feed('{"reply":"文字","suggested_replies":["' + "字" * 120)
    assert decoder.take_guidance() == {"suggested_replies": ["字" * 120]}
    with pytest.raises(StreamReplyError, match="size limit"):
        decoder.feed("多")


@pytest.mark.parametrize("prefix", ["```json\n", "```\n", " \n```json\n"])
def test_decoder_streams_code_fenced_json_without_waiting_for_closing_fence(prefix):
    decoder = ReplyJSONDecoder()
    raw = prefix + json.dumps(response("第一字。"), ensure_ascii=False) + "\n```\n"
    emitted = ""
    for index, char in enumerate(raw):
        emitted += decoder.feed(char)
        if char == "第":
            assert emitted == "第"
            assert index < raw.index('"emotion"')
    decoder.finish()
    assert emitted == "第一字。"


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"reply":"字"}',
        '```json\n{"reply":"字"}\n``',
        '```json\n{"reply":"字"}\n``` trailing',
        '```python\n{"reply":"字"}\n```',
        '``xjson\n{"reply":"字"}\n```',
    ],
)
def test_decoder_rejects_incomplete_or_invalid_code_fences(raw):
    decoder = ReplyJSONDecoder()
    with pytest.raises(StreamReplyError):
        for char in raw:
            decoder.feed(char)
        decoder.finish()


@pytest.mark.asyncio
async def test_stream_emits_reply_and_partial_guidance_before_next_upstream_chunk():
    events = []
    raw_parts = [
        '{"reply":"第',
        '一字。","interaction_mode":"clarify","clarifying_questions":["發生',
        '在哪裡？"],"suggested_replies":["在工',
        '作場所","在學校"],"action_buttons":[],"emotion":"焦慮","emotion_color":"yellow"}',
    ]

    async def before_chunk(index):
        if index == 1:
            assert events == [("reply", "第")]
        elif index == 2:
            assert events[-1] == (
                "guidance",
                {"interaction_mode": "clarify", "clarifying_questions": ["發生"]},
            )
        elif index == 3:
            assert events[-1] == (
                "guidance",
                {
                    "interaction_mode": "clarify",
                    "clarifying_questions": ["發生在哪裡？"],
                    "suggested_replies": ["在工"],
                },
            )
        assert not stream.completed

    stream = FakeStream([chunk(part) for part in raw_parts] + [chunk(finish="stop")], before_chunk)

    async def on_reply(text):
        events.append(("reply", text))

    async def on_guidance(snapshot):
        events.append(("guidance", snapshot))

    completions = FakeCompletions([stream])
    result = await make_agent(completions).run(
        "我很害怕", on_reply_delta=on_reply, on_guidance=on_guidance
    )
    assert result.reply == "".join(raw_parts)
    assert "".join(value for kind, value in events if kind == "reply") == "第一字。"
    assert stream.closed
    properties = completions.calls[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert list(properties) == [
        "reply",
        "interaction_mode",
        "clarifying_questions",
        "suggested_replies",
        "action_buttons",
        "emotion",
        "emotion_color",
    ]


@pytest.mark.asyncio
async def test_stream_handles_split_code_fence_before_upstream_completion():
    received = []
    prefix = '```json\n{"reply":"第'
    raw = json.dumps(response("第一字。"), ensure_ascii=False, separators=(",", ":"))

    async def before_chunk(index):
        if index == len(prefix):
            assert received == ["第"]
            assert not stream.completed

    stream = FakeStream(
        [chunk(char) for char in prefix]
        + [chunk(raw[len('{"reply":"第') :] + "\n```"), chunk(finish="stop")],
        before_chunk,
    )

    async def on_reply(text):
        received.append(text)

    result = await make_agent(FakeCompletions([stream])).run("我很害怕", on_reply_delta=on_reply)
    assert "".join(received) == "第一字。"
    assert result.reply == raw


@pytest.mark.asyncio
async def test_guidance_callback_alone_enables_upstream_streaming():
    snapshots = []
    stream = FakeStream([chunk(json.dumps(response())), chunk(finish="stop")])
    completions = FakeCompletions([stream])

    async def on_guidance(snapshot):
        snapshots.append(snapshot)

    await make_agent(completions).run("我很害怕", on_guidance=on_guidance)
    assert completions.calls[0]["stream"] is True
    assert snapshots == [
        {
            "suggested_replies": ["我想繼續說", "我想先休息"],
            "interaction_mode": "answer",
            "clarifying_questions": [],
        }
    ]
    assert stream.closed


@pytest.mark.asyncio
async def test_stream_logs_upstream_and_reply_ready_timing_without_response_text(monkeypatch):
    records = []
    original_info = agent_module.logger.info

    def collect(message, *args, **kwargs):
        if kwargs.get("extra", {}).get("event") == "openrouter_stream_timing":
            records.append(kwargs["extra"])
        original_info(message, *args, **kwargs)

    monkeypatch.setattr(agent_module.logger, "info", collect)
    times = iter([1.0, 1.01, 1.2, 1.3])
    monkeypatch.setattr(agent_module, "monotonic", lambda: next(times))
    reasoning = chunk()
    reasoning.choices[0].delta.reasoning = "私人推理"
    stream = FakeStream(
        [chunk(), reasoning, chunk(json.dumps(response("私人內容"))), chunk(finish="stop")]
    )

    async def on_reply(text):
        pass

    await make_agent(FakeCompletions([stream])).run("私人問題", on_reply_delta=on_reply)
    assert len(records) == 1
    assert records[0]["phase"] == "initial"
    assert records[0]["first_upstream_chunk_ms"] == 10
    assert records[0]["first_reply_delta_ms"] == 200
    assert records[0]["duration_ms"] == 300
    assert "私人" not in json.dumps(records, ensure_ascii=False)
