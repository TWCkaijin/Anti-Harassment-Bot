"""V4 production-route regressions with in-memory providers and no network."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import backend.app.agents.guided_v4 as v4
from backend.app.agents.openrouter_agent import AgentContractError, OpenRouterAgent
from backend.app.core.agent_prompts import assemble_service_instruction, get_default_prompt_sections
from backend.app.core.case_context import CaseContext
from backend.app.core.clarification_tokens import sign_clarification, validate_clarification_answer
from backend.app.core.streaming_reply import StreamReplyError
from backend.app.core.streaming_units import AnswerUnitJSONDecoder
from backend.app.rag.base import RAGDocument, RAGUnavailableError
from tests.test_agent import (
    FakeClient,
    FakeCompletions,
    FakeMessage,
    FakeResponse,
    fake_runtime_config,
)


def understanding(**changes):
    return {
        "intent": "support",
        "scope": "personal",
        "evidence": [],
        "sufficient": True,
        "limitation": "",
        "immediate_safety": False,
        **changes,
    }


def final_answer(**changes):
    return {
        "answer_units": [{"text": "可以先照顧自己的感受，再決定想談哪些事情。", "source_ids": []}],
        "summary_update": None,
        "question": None,
        "suggested_replies": ["我想先整理情況", "我想暫時休息"],
        "action_buttons": [],
        "emotion": "未知",
        "emotion_color": "gray",
        **changes,
    }


def plan(**changes):
    # The order is intentional: headers can commit before final-answer metadata.
    return {
        "understanding": understanding(),
        "requires_synthesis": False,
        "retrieval": None,
        **final_answer(),
        **changes,
    }


def legal_plan(**changes):
    return plan(
        understanding=understanding(intent="legal_direction"),
        requires_synthesis=True,
        retrieval={"query": "職場行為 法律方向", "data_types": ["law"]},
        answer_units=[],
        **changes,
    )


def response(value, **extra):
    return FakeResponse(FakeMessage(json.dumps(value, ensure_ascii=False), **extra))


class Rag:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    async def retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.fail:
            raise RAGUnavailableError("do not disclose private provider details")
        return [
            RAGDocument(
                content="合成測試條文，不是真實法源。",
                doc_id="law12",
                metadata={
                    "collection": "laws",
                    "source": "性別平等工作法第12條",
                    "law_name": "性別平等工作法",
                    "article_number": "12",
                    "version": "test",
                    "checked_at": "2026-10-01",
                },
            )
        ]


class Stream:
    def __init__(self, chunks, before=None):
        self.chunks = chunks
        self.before = before
        self.closed = False

    def __aiter__(self):
        async def generate():
            for index, chunk in enumerate(self.chunks):
                if self.before:
                    await self.before(index)
                yield chunk

        return generate()

    async def close(self):
        self.closed = True


def chunk(text=None, *, reasoning=None, details=None, finish=None):
    delta = SimpleNamespace(content=text, reasoning=reasoning, reasoning_details=details)
    return SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta, finish_reason=finish)])


def streamed(value, before=None, chunks=None):
    return Stream(
        chunks or [chunk(json.dumps(value, ensure_ascii=False)), chunk(finish="stop")], before
    )


def agent_for(responses, rag=None):
    agent = object.__new__(OpenRouterAgent)
    agent.client = FakeClient(FakeCompletions(responses))
    agent.rag = rag or Rag()
    return agent


async def run(agent, **changes):
    kwargs = {
        "user_message": "我想先談談感受",
        "case_context": {"schema_version": 3},
        "contract_version": 4,
        "runtime_config": fake_runtime_config(),
        **changes,
    }
    return await agent.run(**kwargs)


@pytest.fixture(autouse=True)
def no_external_skills(monkeypatch):
    monkeypatch.setattr(v4, "get_matching_scenario_scripts", lambda *args, **kwargs: ())


def test_unit_parser_publishes_complete_object_before_response_finishes():
    decoder = AnswerUnitJSONDecoder()
    assert decoder.feed('{"answer_units":[{"text":"開始\\n') == []
    ready = decoder.feed('下一行\\ud83d\\ude00","source_ids":[]},')
    assert ready == [{"text": "開始\n下一行😀", "source_ids": []}]
    with pytest.raises(StreamReplyError, match="Incomplete"):
        decoder.finish()
    decoder.feed('{"text":"完成","source_ids":[]}],"question":null}')
    assert decoder.finish()["question"] is None


@pytest.mark.parametrize(
    "body",
    [
        '{"answer_units":[],"answer_units":[]}',
        '{"answer_units":[{"text":"one","text":"two","source_ids":[]}]}',
        '{"answer_units":[{"text":"one","source_ids":[]},]}',
        '{"answer_units":[]} trailing',
    ],
)
def test_unit_parser_rejects_duplicate_or_invalid_json(body):
    decoder = AnswerUnitJSONDecoder()
    with pytest.raises(StreamReplyError):
        decoder.feed(body)
        decoder.finish()


@pytest.mark.asyncio
async def test_simple_support_one_call_and_no_fixed_headings_or_legacy_fact_fields():
    agent = agent_for([response(plan())])
    result = await run(agent)
    assert len(agent.client.chat.completions.calls) == 1
    assert not agent.rag.calls
    assert "###" not in json.loads(result.reply)["reply"]
    assert {"fact_updates", "answer_sections", "facts_revision"}.isdisjoint(result.guidance)
    assert result.guidance["context_revision"] == 0


@pytest.mark.asyncio
async def test_legal_request_uses_understanding_before_retrieval_and_actual_documents():
    agent = agent_for(
        [
            response(legal_plan()),
            response(
                final_answer(
                    answer_units=[
                        {
                            "text": "性別平等工作法第12條可作為討論方向。",
                            "source_ids": ["laws/law12"],
                        }
                    ]
                )
            ),
        ]
    )
    result = await run(agent)
    assert len(agent.client.chat.completions.calls) == 2
    assert len(agent.rag.calls) == 1
    assert result.guidance["execution"]["route"] == "understand_retrieve_answer"
    handoff = json.loads(agent.client.chat.completions.calls[-1]["messages"][-1]["content"])
    assert handoff["untrusted_retrieved_documents"][0]["content"] == "合成測試條文，不是真實法源。"
    assert result.sources[0]["doc_id"] == "laws/law12"


@pytest.mark.asyncio
async def test_final_stage_receives_explicit_answer_task_after_understanding_without_retry():
    agent = agent_for([response(legal_plan()), response(final_answer())])
    await run(agent, user_message="請說明主要法律方向與一般下一步")
    calls = agent.client.chat.completions.calls
    assert len(calls) == 2 and len(agent.rag.calls) == 1
    first_messages, final_messages = calls[0]["messages"], calls[1]["messages"]
    assert all(item.get("content") != v4.V4_FINAL_STAGE_INSTRUCTION for item in first_messages)
    assert final_messages[-2] == {"role": "system", "content": v4.V4_FINAL_STAGE_INSTRUCTION}
    handoff = json.loads(final_messages[-1]["content"])
    assert handoff["retrieval_status"] == "ok"
    assert handoff["untrusted_retrieved_documents"][0]["source"]["doc_id"] == "laws/law12"
    assert any(
        item["role"] == "user" and "請說明主要法律方向與一般下一步" in item["content"]
        for item in final_messages
    )
    assert calls[0]["response_format"]["json_schema"]["name"] == "guided_chat_v4_understanding"
    assert calls[1]["response_format"]["json_schema"]["name"] == "guided_chat_v4_answer"


@pytest.mark.asyncio
async def test_final_unit_is_sent_before_later_units_and_metadata_arrive():
    observed = []
    legal = legal_plan()
    final = final_answer(
        answer_units=[
            {"text": "先說明可用的資料。", "source_ids": []},
            {"text": "接著整理你的需求。", "source_ids": []},
        ]
    )
    raw = json.dumps(final, ensure_ascii=False)
    boundary = raw.index("}, {") + 1

    async def before(index):
        if index == 1:
            assert observed == ["先說明可用的資料。"]

    stream = streamed(
        final,
        before=before,
        chunks=[chunk(raw[:boundary]), chunk(raw[boundary:]), chunk(finish="stop")],
    )
    agent = agent_for([streamed(legal), stream])

    async def delta(text):
        observed.append(text)

    result = await run(agent, on_reply_delta=delta)
    assert "".join(observed) == json.loads(result.reply)["reply"]
    assert stream.closed


@pytest.mark.asyncio
async def test_simple_support_units_can_stream_before_final_metadata():
    observed = []
    raw = json.dumps(plan(), ensure_ascii=False)
    boundary = raw.index('], "summary_update"') + 1

    async def before(index):
        if index == 1:
            assert observed and "照顧" in observed[0]

    stream = streamed(
        plan(),
        before=before,
        chunks=[chunk(raw[:boundary]), chunk(raw[boundary:]), chunk(finish="stop")],
    )
    agent = agent_for([stream])

    async def delta(text):
        observed.append(text)

    result = await run(agent, on_reply_delta=delta)
    assert result.guidance["execution"]["model_calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_id", ["invented", "性別平等工作法第12條", "law12"])
async def test_unknown_source_never_leaves_the_unit_boundary(invalid_id):
    observed = []
    agent = agent_for(
        [
            streamed(legal_plan()),
            streamed(
                final_answer(
                    answer_units=[
                        {"text": "性別平等工作法第12條適用。", "source_ids": [invalid_id]}
                    ]
                )
            ),
        ]
    )

    async def delta(text):
        observed.append(text)

    with pytest.raises(AgentContractError, match="citation"):
        await run(agent, on_reply_delta=delta)
    assert observed == []


@pytest.mark.asyncio
async def test_repeated_unsupported_claims_keep_local_notices_and_other_text():
    units = [
        {"text": "性別平等工作法第13條是依據。", "source_ids": []},
        {"text": "刑法第224條必然成立。你可以先休息。", "source_ids": []},
        {"text": "民法第195條一定適用。", "source_ids": []},
    ]
    agent = agent_for([response(legal_plan()), response(final_answer(answer_units=units))])
    result = await run(agent, use_rag=False)
    reply = json.loads(result.reply)["reply"]
    assert "第13條" not in reply and "第224條" not in reply and "第195條" not in reply
    assert "性別平等工作法" in reply and "刑法" in reply and "民法" in reply
    assert reply.count("〔條號待核對〕") == 3
    assert "你可以先休息。" in reply
    assert not agent.rag.calls
    assert result.sources == []


@pytest.mark.asyncio
async def test_disabled_rag_is_zero_calls_even_if_model_requests_it():
    agent = agent_for([response(legal_plan()), response(final_answer())])
    result = await run(agent, use_rag=False)
    assert not agent.rag.calls
    assert result.rag_used is False
    handoff = json.loads(agent.client.chat.completions.calls[-1]["messages"][-1]["content"])
    assert handoff["retrieval_status"] == "disabled"
    assert handoff["untrusted_retrieved_documents"] == []


@pytest.mark.asyncio
async def test_source_failure_produces_truthful_limit_without_private_error_details():
    agent = agent_for([response(legal_plan()), response(final_answer())], Rag(fail=True))
    result = await run(agent)
    source_event = next(item for item in result.guidance["analysis"] if item["stage"] == "sources")
    assert "未能完成" in source_event["summary"]
    assert result.rag_used is False
    assert "private provider" not in json.dumps(result.guidance)
    schema = agent.client.chat.completions.calls[-1]["response_format"]["json_schema"]["schema"]
    assert schema["$defs"]["AnswerUnit"]["properties"]["source_ids"]["maxItems"] == 0


def test_summary_requires_own_supported_content_and_preserves_user_edit():
    context = CaseContext(
        schema_version=3, revision=8, summary="目前不想申訴", summary_origin="user"
    )
    hallucinated = v4.SummaryProposal(summary="已經申訴", evidence=["目前不想申訴"])
    assert v4.validated_summary(hallucinated, context, ["我是學生"], "personal") is None
    overwrites = v4.SummaryProposal(summary="我是學生", evidence=["我是學生"])
    assert v4.validated_summary(overwrites, context, ["我是學生"], "personal") is None
    supported = v4.SummaryProposal(
        summary="目前不想申訴\n我是學生", evidence=["目前不想申訴", "我是學生"]
    )
    assert v4.validated_summary(supported, context, ["我是學生"], "personal")["base_revision"] == 8


def test_long_user_edited_summary_can_be_kept_without_requoting_it_as_new_evidence():
    original = "使用者已編輯的長摘要。" * 100
    context = CaseContext(schema_version=3, summary=original, summary_origin="user")
    proposal = v4.SummaryProposal(summary=original + "\n我想先休息", evidence=["我想先休息"])
    accepted = v4.validated_summary(proposal, context, ["我想先休息"], "personal")
    assert accepted["summary"] == original + "\n我想先休息"


@pytest.mark.asyncio
async def test_summary_does_not_repopulate_from_old_history_and_scenario_is_labeled():
    proposal = {"summary": "甲是主管", "evidence": ["甲是主管"]}
    first = plan(
        understanding=understanding(scope="hypothetical", evidence=["甲是主管"]),
        summary_update=proposal,
    )
    result = await run(
        agent_for([response(first)]),
        user_message="虛構例題：甲是主管",
        history=[{"role": "user", "content": "我在學校"}],
    )
    update = result.guidance["summary_update"]
    assert update["summary"] == "[假設情境] 甲是主管"
    assert "學校" not in update["summary"]
    old = plan(summary_update={"summary": "我在學校", "evidence": ["我在學校"]})
    result = await run(
        agent_for([response(old)]), history=[{"role": "user", "content": "我在學校"}]
    )
    assert result.guidance["summary_update"] is None


@pytest.mark.asyncio
async def test_question_token_uses_revision_after_supported_summary_update():
    question = {
        "question": "你希望哪類協助？可複選。",
        "reason": "需知道目前協助需求。",
        "options": [{"label": "支持", "value": "支持"}, {"label": "程序", "value": "程序"}],
        "selection_mode": "multiple",
        "max_selections": 2,
    }
    first = plan(
        understanding=understanding(sufficient=False),
        question=question,
        summary_update={"summary": "我是學生", "evidence": ["我是學生"]},
    )
    result = await run(agent_for([response(first)]), user_message="我是學生")
    issued = result.guidance["clarification"]
    assert issued["context_revision"] == 1
    assert "fact_key" not in issued
    answer = {
        "question_id": issued["question_id"],
        "context_revision": 1,
        "status": "provided",
        "value": ["支持", "程序"],
        "validation_token": issued["validation_token"],
    }
    assert validate_clarification_answer(answer, contract_version=4)["context_revision"] == 1


@pytest.mark.asyncio
async def test_reasoning_whitelist_bounded_and_not_in_next_model_history():
    reasoning_events = []
    first = streamed(
        legal_plan(),
        chunks=[
            chunk(
                reasoning="a" * 2300, details=[{"type": "reasoning.encrypted", "data": "SECRET"}]
            ),
            chunk(
                json.dumps(legal_plan(), ensure_ascii=False),
                details=[
                    {"type": "reasoning.summary", "summary": "b" * 2300},
                    {"type": "reasoning.signature", "text": "SIGNATURE"},
                ],
            ),
            chunk(finish="stop"),
        ],
    )
    agent = agent_for([first, streamed(final_answer())])

    async def reasoning(value):
        reasoning_events.append(value)

    result = await run(
        agent,
        on_reasoning=reasoning,
        runtime_config=replace(fake_runtime_config(), reasoning_effort="low"),
    )
    assert sum(len(item["text"]) for item in reasoning_events) == 4000
    assert all(len(item["text"]) <= 2000 for item in reasoning_events)
    assert {item["kind"] for item in reasoning_events} == {"text", "summary"}
    assert result.guidance["reasoning"] == reasoning_events
    calls = agent.client.chat.completions.calls
    assert calls[0]["extra_body"]["reasoning"]["exclude"] is False
    assert "a" * 100 not in json.dumps(calls[1]["messages"])
    assert "SECRET" not in json.dumps(result.guidance)


@pytest.mark.asyncio
async def test_analysis_off_never_publishes_or_requests_reasoning_text():
    events = []
    config = replace(
        fake_runtime_config(), reasoning_effort="low", pipeline={"enable_analysis": False}
    )
    agent = agent_for([response(plan())])

    async def reasoning(value):
        events.append(value)

    result = await run(agent, on_reasoning=reasoning, runtime_config=config)
    assert events == [] and result.guidance["analysis"] == []
    assert "reasoning" not in result.guidance
    assert agent.client.chat.completions.calls[0]["extra_body"]["reasoning"]["exclude"] is True


@pytest.mark.asyncio
async def test_cancel_closes_provider_stream_without_replaying():
    entered = asyncio.Event()

    async def before(index):
        entered.set()
        await asyncio.Event().wait()

    stream = streamed(plan(), before=before)
    agent = agent_for([stream])

    async def delta(text):
        pass

    task = asyncio.create_task(run(agent, on_reply_delta=delta))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
    assert len(agent.client.chat.completions.calls) == 1


@pytest.mark.asyncio
async def test_communication_settings_reach_both_stages_but_output_format_does_not():
    config = replace(
        fake_runtime_config(),
        agent_prompt_sections={
            "communication_principles": "先支持再說明",
            "output_format": "OLD_FORMAT_SHOULD_NOT_APPEAR",
        },
    )
    agent = agent_for([response(legal_plan()), response(final_answer())])
    await run(agent, runtime_config=config)
    for call in agent.client.chat.completions.calls:
        prompt = json.dumps(call["messages"], ensure_ascii=False)
        service = call["messages"][0]["content"]
        assert service == assemble_service_instruction(config.agent_prompt_sections)
        assert get_default_prompt_sections()["core_mission"] in service
        assert get_default_prompt_sections()["retrieval_instructions"] in service
        assert "先支持再說明" in prompt
        assert "OLD_FORMAT_SHOULD_NOT_APPEAR" not in prompt
        assert '"reply":' not in service


@pytest.mark.asyncio
async def test_regeneration_uses_only_edited_summary_as_facts_and_never_updates_it():
    first = plan(
        understanding=understanding(evidence=["我是受僱者"]),
        summary_update={"summary": "我是主管", "evidence": ["我是主管"]},
    )
    agent = agent_for([response(first)])
    result = await run(
        agent,
        user_message="我是主管，想知道怎麼辦",
        regenerate_from_summary=True,
        case_context={
            "schema_version": 3,
            "revision": 3,
            "summary": "我是受僱者",
            "summary_origin": "user",
        },
        history=[{"role": "user", "content": "以前摘要還說我是老師"}],
    )
    assert result.guidance["summary_update"] is None
    assert result.guidance["context_revision"] == 3
    messages = agent.client.chat.completions.calls[0]["messages"]
    sent = json.loads(messages[-1]["content"])
    assert sent["message"] == "請依目前摘要重新回答原始需求"
    assert sent["original_request_for_intent_only"] == "我是主管，想知道怎麼辦"
    assert sent["conversation_summary"] == "我是受僱者"
    assert "以前摘要還說我是老師" not in json.dumps(messages, ensure_ascii=False)


@pytest.mark.asyncio
async def test_regeneration_rejects_old_original_role_as_new_understanding_evidence():
    agent = agent_for([response(plan(understanding=understanding(evidence=["我是主管"])))])
    diagnostics = {}
    result = await run(
        agent,
        user_message="我是主管",
        regenerate_from_summary=True,
        case_context={"schema_version": 3, "summary": "我是受僱者", "summary_origin": "user"},
        diagnostics=diagnostics,
    )
    assert "照顧自己的感受" in json.loads(result.reply)["reply"]
    assert result.guidance["summary_update"] is None
    assert result.guidance["analysis"][0]["facts"] == []
    assert diagnostics["metadata_validation"][0]["reason"] == "unsupported_evidence"


@pytest.mark.asyncio
async def test_pii_query_before_after_is_diagnostics_only(monkeypatch):
    import backend.app.core.anonymizer as anonymizer

    monkeypatch.setattr(
        anonymizer,
        "anonymize",
        lambda value: SimpleNamespace(
            anonymized=value.replace("student@example.com", "[EMAIL]"),
            detected_types=["email"],
        ),
    )
    first = legal_plan()
    first["retrieval"]["query"] = "student@example.com 工作問題"
    agent = agent_for([response(first), response(final_answer())])
    diagnostics = {}
    result = await run(
        agent,
        diagnostics=diagnostics,
        runtime_config=replace(fake_runtime_config(), enable_anonymization=True),
    )
    assert agent.rag.calls[0][0] == "[EMAIL] 工作問題"
    assert diagnostics["pii"]["retrieval_query"]["before"] == "student@example.com 工作問題"
    assert diagnostics["pii"]["retrieval_query"]["after"] == "[EMAIL] 工作問題"
    assert "student@example.com" not in result.reply + json.dumps(result.guidance)


@pytest.mark.asyncio
async def test_immediate_safety_preface_is_published_before_retrieval():
    displayed = []

    class SafetyRag(Rag):
        async def retrieve(self, query, **kwargs):
            assert displayed and "不必等候" in displayed[0]
            return await super().retrieve(query, **kwargs)

    first = legal_plan()
    first["understanding"]["immediate_safety"] = True
    agent = agent_for([streamed(first), streamed(final_answer())], SafetyRag())

    async def delta(text):
        displayed.append(text)

    result = await run(agent, on_reply_delta=delta)
    assert json.loads(result.reply)["reply"].startswith(v4._SAFETY_TEXT)
    assert result.guidance["clarification"] is None


@pytest.mark.asyncio
async def test_invalid_trailing_metadata_cannot_publish_a_successful_result():
    displayed = []
    value = plan(suggested_replies=["重複", "重複"])
    stream = streamed(value)
    agent = agent_for([stream])

    async def delta(text):
        displayed.append(text)

    with pytest.raises(AgentContractError, match="projection"):
        await run(agent, on_reply_delta=delta)
    assert displayed and stream.closed


@pytest.mark.asyncio
async def test_model_never_receives_signature_or_unmasked_option_constraints():
    question = {
        "question_id": "conversation.fixture",
        "context_revision": 0,
        "selection_mode": "single",
        "max_selections": 1,
        "context_scope": "scenario",
        "options": [{"label": "測試選項", "value": "student@example.com"}],
    }
    token = sign_clarification(question, contract_version=4)
    original_answer = {
        "question_id": question["question_id"],
        "context_revision": 0,
        "status": "provided",
        "value": "student@example.com",
        "validation_token": token,
    }
    constraints = validate_clarification_answer(original_answer, contract_version=4)
    masked_answer = {**original_answer, "value": "[EMAIL]"}
    agent = agent_for([response(plan())])
    await run(
        agent,
        user_message="[EMAIL]",
        clarification_answer=masked_answer,
        clarification_constraints=constraints,
    )
    messages = agent.client.chat.completions.calls[0]["messages"]
    prompt = json.dumps(messages, ensure_ascii=False)
    assert "student@example.com" not in prompt and token not in prompt
    assert "allowed_values" not in prompt and "validation_token" not in prompt
    assert json.loads(messages[-1]["content"])["clarification_answer"] == {
        "status": "provided",
        "value": "[EMAIL]",
        "context_scope": "scenario",
    }


@pytest.mark.asyncio
async def test_default_service_guidance_reaches_both_v4_stages_without_legacy_format():
    agent = agent_for([response(legal_plan()), response(final_answer())])
    await run(agent)
    expected = assemble_service_instruction()
    for call in agent.client.chat.completions.calls:
        assert call["messages"][0] == {"role": "system", "content": expected}
        assert "溫暖但專業" in expected and "按情境取用" in expected
        assert "每句法律主張明寫法名與條號" not in str(call["messages"])
        assert "強制輸出格式" not in expected


@pytest.mark.asyncio
@pytest.mark.parametrize("rag_enabled", [False, True])
async def test_general_direction_and_next_steps_survive_without_sources(rag_enabled):
    text = (
        "依你描述的主管關係，可能涉及《性別平等工作法》的職場性騷擾處理方向。\n\n"
        "你可以先記下事件經過、保留訊息，再決定是否詢問公司申訴窗口。"
    )

    class EmptyRag(Rag):
        async def retrieve(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return []

    agent = agent_for(
        [
            response(legal_plan()),
            response(final_answer(answer_units=[{"text": text, "source_ids": []}])),
        ],
        EmptyRag(),
    )
    result = await run(
        agent,
        user_message="主管對我說黃色笑話，怎麼辦？",
        use_rag=rag_enabled,
    )
    assert json.loads(result.reply)["reply"] == text
    assert result.sources == [] and not result.rag_used
    assert len(agent.rag.calls) == int(rag_enabled)
    schema = agent.client.chat.completions.calls[-1]["response_format"]["json_schema"]["schema"]
    ids = schema["$defs"]["AnswerUnit"]["properties"]["source_ids"]
    assert ids["type"] == "array" and ids["maxItems"] == 0
    assert "enum" not in ids["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("synthesis", [False, True])
@pytest.mark.parametrize("evidence", [["未曾說過的私人敘述"], None, [17], "不是摘錄陣列"])
async def test_invalid_understanding_evidence_keeps_answer_and_current_summary(
    streaming, synthesis, evidence
):
    current = "目前不想申訴"
    proposal = {
        "summary": current + "\n我想先談談感受",
        "evidence": [current, "我想先談談感受"],
    }
    first = legal_plan() if synthesis else plan()
    first["understanding"]["evidence"] = evidence
    first["summary_update"] = proposal
    values = [first, final_answer(summary_update=proposal)] if synthesis else [first]
    agent = agent_for([(streamed if streaming else response)(value) for value in values])
    diagnostics, deltas = {}, []

    async def delta(value):
        deltas.append(value)

    result = await run(
        agent,
        case_context={
            "schema_version": 3,
            "revision": 5,
            "summary": current,
            "summary_origin": "user",
        },
        diagnostics=diagnostics,
        on_reply_delta=delta if streaming else None,
    )
    reply = json.loads(result.reply)["reply"]
    assert "照顧自己的感受" in reply
    assert result.guidance["summary_update"] is None
    assert result.guidance["context_revision"] == 5
    assert result.guidance["analysis"][0]["facts"] == []
    rejected = diagnostics["metadata_validation"]
    assert rejected[0]["field"] == "understanding.evidence"
    assert rejected[0]["reason"] in {"unsupported_evidence", "invalid_shape"}
    assert "私人敘述" not in json.dumps(rejected, ensure_ascii=False)
    if streaming:
        assert "".join(deltas) == reply
    if synthesis:
        handoff = json.loads(agent.client.chat.completions.calls[-1]["messages"][-1]["content"])
        assert handoff["validated_understanding"]["evidence"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("synthesis", [False, True])
@pytest.mark.parametrize(
    "proposal",
    [
        "not an object",
        {"summary": ["wrong shape"], "evidence": ["我想先談談感受"]},
        {"summary": "我想先談談感受", "evidence": [7]},
        {"summary": "我想先談談感受", "evidence": [], "extra": "private"},
    ],
)
async def test_invalid_summary_shape_keeps_valid_answer_in_each_route(
    streaming, synthesis, proposal
):
    first = legal_plan() if synthesis else plan(summary_update=proposal)
    if synthesis:
        # A malformed final proposal must not revive an earlier proposal.
        first["summary_update"] = {
            "summary": "我想先談談感受",
            "evidence": ["我想先談談感受"],
        }
    values = [first, final_answer(summary_update=proposal)] if synthesis else [first]
    agent = agent_for([(streamed if streaming else response)(value) for value in values])
    diagnostics, deltas = {}, []

    async def delta(value):
        deltas.append(value)

    result = await run(
        agent,
        diagnostics=diagnostics,
        on_reply_delta=delta if streaming else None,
        case_context={"schema_version": 3, "revision": 4, "summary": "原摘要"},
    )
    reply = json.loads(result.reply)["reply"]
    assert "照顧自己的感受" in reply
    assert result.guidance["summary_update"] is None
    assert result.guidance["context_revision"] == 4
    assert diagnostics["metadata_validation"][-1] == {
        "stage": "answer" if synthesis else "understanding",
        "field": "summary_update",
        "reason": "invalid_shape",
        "action": "discarded",
    }
    if streaming:
        assert "".join(deltas) == reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("proposal", "reason"),
    [
        ({"summary": "我是老師", "evidence": ["我是老師"]}, "unsupported_evidence"),
        (
            {"summary": "我是老師", "evidence": ["我想先談談感受"]},
            "unsupported_summary",
        ),
        (
            {"summary": "我想先談談感受", "evidence": ["我想先談談感受"]},
            "user_summary_overwrite",
        ),
    ],
)
async def test_semantically_invalid_summary_has_diagnostics_and_preserves_answer(proposal, reason):
    diagnostics = {}
    result = await run(
        agent_for([response(plan(summary_update=proposal))]),
        case_context={
            "schema_version": 3,
            "revision": 6,
            "summary": "原本是學生",
            "summary_origin": "user",
        },
        diagnostics=diagnostics,
    )
    assert "照顧自己的感受" in json.loads(result.reply)["reply"]
    assert result.guidance["summary_update"] is None
    assert result.guidance["context_revision"] == 6
    assert diagnostics["metadata_validation"][-1]["reason"] == reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto_clarify", "safety", "accepted"),
    [(True, False, True), (False, False, False), (True, True, False)],
)
async def test_final_question_can_refine_sufficient_plan_but_respects_controls(
    auto_clarify, safety, accepted
):
    first = legal_plan()
    first["understanding"].update(sufficient=True, immediate_safety=safety)
    question = {
        "question": "當時是否正在執行工作？",
        "reason": "檢索資料顯示執行職務與否會影響進一步處理方向。",
        "options": [{"label": "是", "value": "是"}, {"label": "否", "value": "否"}],
        "selection_mode": "single",
        "max_selections": 1,
    }
    agent = agent_for([response(first), response(final_answer(question=question))])
    result = await run(
        agent,
        case_context={"schema_version": 3, "revision": 9},
        runtime_config=replace(fake_runtime_config(), pipeline={"auto_clarify": auto_clarify}),
    )
    issued = result.guidance["clarification"]
    assert bool(issued) is accepted
    if accepted:
        assert issued["context_revision"] == 9
        assert issued["question"] == question["question"]
        assert (
            validate_clarification_answer(
                {
                    "question_id": issued["question_id"],
                    "context_revision": 9,
                    "status": "provided",
                    "value": "是",
                    "validation_token": issued["validation_token"],
                },
                contract_version=4,
            )["context_revision"]
            == 9
        )


@pytest.mark.asyncio
async def test_final_provider_schema_uses_retrieved_ids_and_explains_label_boundary():
    agent = agent_for([response(legal_plan()), response(final_answer())])
    await run(agent)
    call = agent.client.chat.completions.calls[-1]
    schema = call["response_format"]["json_schema"]["schema"]
    ids = schema["$defs"]["AnswerUnit"]["properties"]["source_ids"]
    handoff = json.loads(call["messages"][-1]["content"])
    actual = [doc["source"]["doc_id"] for doc in handoff["untrusted_retrieved_documents"]]
    assert ids["items"]["enum"] == actual == ["laws/law12"]
    assert ids["maxItems"] == 12
    assert "source.doc_id" in ids["description"]
    instruction = next(
        message["content"]
        for message in call["messages"]
        if message["role"] == "system" and message["content"] == v4.V4_FINAL_STAGE_INSTRUCTION
    )
    assert "逐字複製" in instruction and "source.label" in instruction
    assert "不可放入source_ids" in instruction


@pytest.mark.asyncio
async def test_request_specific_source_enums_do_not_leak_across_parallel_requests():
    original = json.loads(json.dumps(v4.V4_FINAL_FORMAT))

    class SpecificRag(Rag):
        def __init__(self, identifier):
            super().__init__()
            self.identifier = identifier

        async def retrieve(self, query, **kwargs):
            if self.identifier is None:
                return []
            return [
                RAGDocument(
                    content="合成測試資料。",
                    doc_id=self.identifier,
                    metadata={"collection": "fixture", "source": "相同顯示名稱"},
                )
            ]

    agents = [
        agent_for([response(legal_plan()), response(final_answer())], SpecificRag(identifier))
        for identifier in ["first", None, "second"]
    ]
    await asyncio.gather(*(run(agent) for agent in agents))
    values = [
        agent.client.chat.completions.calls[-1]["response_format"]["json_schema"]["schema"][
            "$defs"
        ]["AnswerUnit"]["properties"]["source_ids"]
        for agent in agents
    ]
    assert values[0]["items"]["enum"] == ["fixture/first"]
    assert values[1]["maxItems"] == 0 and "enum" not in values[1]["items"]
    assert values[2]["items"]["enum"] == ["fixture/second"]
    assert original == v4.V4_FINAL_FORMAT
    assert (
        "enum"
        not in v4.V4_PLAN_FORMAT["json_schema"]["schema"]["$defs"]["AnswerUnit"]["properties"][
            "source_ids"
        ]["items"]
    )
