"""Offline v4 boundaries: text summaries, signed choices, transient reasoning and PII."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from backend.app.agents.openrouter_agent import AgentResult
from backend.app.api import admin, chat, health
from backend.app.core.case_context import CaseContext, SummaryUpdate
from backend.app.core.clarification_tokens import sign_clarification, validate_clarification_answer
from backend.app.core.runtime_config import RuntimeConfig
from backend.app.main import app


def runtime_config(**changes):
    values = {
        "openrouter_model": "offline/model",
        "rag_retrieval_top_k": 3,
        "enable_anonymization": True,
        "temperature": 0,
        "top_p": 1,
        "max_tokens": 0,
    }
    return RuntimeConfig(**(values | changes))


def context(revision=0, summary="", **changes):
    return {
        "schema_version": 3,
        "revision": revision,
        "summary": summary,
        "summary_origin": "user",
        "facts": {},
        **changes,
    }


def request_payload(**changes):
    return {
        "message": "合成測試問題",
        "contract_version": 4,
        "case_context": context(),
        **changes,
    }


def result(**guidance):
    return AgentResult(
        reply=json.dumps(
            {
                "reply": "先整理現在需要的協助。",
                "emotion": "未知",
                "emotion_color": "gray",
                "suggested_replies": ["了解下一步", "補充情況"],
                "action_buttons": [],
                "interaction_mode": "answer",
                "clarifying_questions": [],
            },
            ensure_ascii=False,
        ),
        guidance={
            "contract_version": 4,
            "context_revision": 0,
            "summary_update": None,
            "clarification": None,
            **guidance,
        },
    )


def question(*, revision=0, mode="multiple", limit=3):
    issued = {
        "question_id": f"clarification.context.{revision}",
        "question": "你目前希望取得哪些協助？",
        "reason": "依你的需求安排下一步。",
        "options": [{"label": value, "value": value} for value in ["聯絡資源", "整理證據"]],
        "selection_mode": mode,
        "max_selections": limit,
        "context_scope": "personal",
        "context_revision": revision,
    }
    issued["validation_token"] = sign_clarification(issued, contract_version=4)
    return issued


def selected_answer(issued=None, **changes):
    issued = issued or question()
    return {
        "question_id": issued["question_id"],
        "context_revision": issued["context_revision"],
        "context_scope": issued["context_scope"],
        "status": "provided",
        "value": ["聯絡資源", "自行寫下的需求"],
        "validation_token": issued["validation_token"],
        **changes,
    }


def frames(response):
    return [
        (chunk.split("\n", 1)[0][7:], json.loads(chunk.split("\ndata: ", 1)[1]))
        for chunk in response.get_data(as_text=True).split("\n\n")
        if chunk.startswith("event: ")
    ]


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    config = runtime_config()
    monkeypatch.setattr(admin.settings, "admin_api_key", "offline-admin-key")
    monkeypatch.setattr(chat, "get_runtime_config", lambda: config)
    monkeypatch.setattr(admin, "get_runtime_config", lambda **kwargs: config)
    monkeypatch.setattr(health, "get_runtime_config", lambda: config)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)
    monkeypatch.setattr(chat, "get_agent", lambda: pytest.fail("Unexpected model invocation"))
    return config


def test_schema_three_round_trips_summary_without_legacy_fields():
    snapshot = CaseContext.model_validate(context(4, "已知情境。" * 500))
    assert CaseContext.model_validate(snapshot.model_dump()) == snapshot
    assert len(snapshot.summary) > 300
    assert snapshot.facts == {}
    for schema in (1, 2):
        old = CaseContext(schema_version=schema)
        assert "summary" not in old.model_dump()
        assert "summary_origin" not in json.loads(old.model_dump_json())
        assert CaseContext.model_validate(old.model_dump()) == old


@pytest.mark.parametrize(
    "snapshot",
    [
        {"schema_version": 1, "summary": ""},
        {"schema_version": 2, "summary": "舊版不可帶摘要"},
        {"schema_version": 2, "summary_origin": "model"},
        context(summary="字" * 4001),
        context(summary_origin="unverified"),
        context(facts={"subject_role": {"status": "provided", "value": "受僱者"}}),
    ],
)
def test_context_rejects_ambiguous_version_or_summary(snapshot):
    with pytest.raises(ValidationError):
        CaseContext.model_validate(snapshot)


@pytest.mark.parametrize("version,schema", [(2, 1), (3, 2), (4, 3)])
def test_request_versions_use_only_their_matching_context_schema(version, schema):
    req = chat.ChatRequest(message="合成問題", contract_version=version)
    arguments, _ = chat._prepare_agent_input(req, runtime_config())
    assert arguments["case_context"]["schema_version"] == schema
    for other_schema in {1, 2, 3} - {schema}:
        with pytest.raises(ValidationError):
            chat.ChatRequest(
                message="合成問題", contract_version=version, case_context={"schema_version": other_schema}
            )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_requests_cannot_regenerate_from_a_text_summary(version):
    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "原始需求", "contract_version": version, "regenerate_from_summary": True},
    )
    assert response.status_code == 422


def test_v4_regeneration_passes_only_the_request_snapshot_to_the_agent(monkeypatch):
    calls = []

    class Agent:
        async def run(self, **kwargs):
            calls.append(kwargs)
            return result(context_revision=4)

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post(
        "/api/v1/chat/",
        json=request_payload(
            message="原始需求與已刪除敘述",
            case_context=context(4, "使用者更正後的唯一摘要"),
            regenerate_from_summary=True,
        ),
    )
    assert response.status_code == 200
    assert calls[0]["regenerate_from_summary"] is True
    assert calls[0]["case_context"]["summary"] == "使用者更正後的唯一摘要"
    assert response.json["summary_update"] is None


def test_v4_regeneration_cannot_return_a_replacement_summary(monkeypatch):
    class Agent:
        async def run(self, **kwargs):
            return result(summary_update={"base_revision": 0, "summary": "不能恢復的舊資料", "evidence": []})

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post(
        "/api/v1/chat/", json=request_payload(regenerate_from_summary=True)
    )
    assert response.status_code == 502


def test_v4_regeneration_rejects_simultaneous_clarification_before_model_call():
    response = app.test_client().post(
        "/api/v1/chat/",
        json=request_payload(regenerate_from_summary=True, clarification_answer=selected_answer()),
    )
    assert response.status_code == 422


def test_v4_choice_accepts_bound_revision_presets_and_one_freeform():
    issued = question(revision=4)
    answer = selected_answer(issued)
    req = chat.ChatRequest(**request_payload(case_context=context(4), clarification_answer=answer))
    arguments, _ = chat._prepare_agent_input(req, runtime_config())
    assert "fact_key" not in arguments["clarification_answer"]
    assert arguments["clarification_constraints"]["context_revision"] == 4
    assert "allowed_values" not in arguments["clarification_constraints"]
    assert validate_clarification_answer(answer, contract_version=4)["context_revision"] == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"fact_key": "behavior"},
        {"fact_key": None},
        {"context_revision": 1},
        {"context_revision": False},
        {"context_scope": "scenario"},
        {"question_id": "other.question"},
        {"max_selections": 4},
        {"value": ["聯絡資源", "聯絡資源"]},
        {"value": ["聯絡資源", "不確定"]},
        {"value": ["自由輸入一", "自由輸入二"]},
        {"value": ["聯絡資源", "整理證據", "自由輸入", "第四項"]},
        {"status": "unknown", "value": ["聯絡資源"]},
        {"validation_token": None},
    ],
)
def test_v4_choice_rejects_fact_keys_stale_or_tampered_shape_and_conflicts(changes):
    response = app.test_client().post(
        "/api/v1/chat/",
        json=request_payload(clarification_answer=selected_answer(**changes)),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("status", ["unknown", "declined"])
def test_v4_choice_can_decline_or_be_unknown_without_a_value(status):
    req = chat.ChatRequest(
        **request_payload(clarification_answer=selected_answer(status=status, value=None))
    )
    assert req.clarification_answer.status == status


def test_v4_single_choice_rejects_multiple_values():
    with pytest.raises(ValidationError):
        chat.ChatRequest(
            **request_payload(
                clarification_answer=selected_answer(question(mode="single", limit=1))
            )
        )


def test_v3_and_v4_tokens_are_not_interchangeable():
    old_question = {
        "question_id": "case.behavior.0",
        "fact_key": "behavior",
        "selection_mode": "multiple",
        "max_selections": 3,
        "context_scope": "personal",
        "options": [{"value": "聯絡資源"}],
    }
    old_token = sign_clarification(old_question)
    with pytest.raises(ValueError):
        validate_clarification_answer(
            selected_answer(validation_token=old_token), contract_version=4
        )
    new = selected_answer()
    with pytest.raises(ValueError):
        validate_clarification_answer({**new, "fact_key": "behavior"})
    old = {**new, "question_id": old_question["question_id"], "fact_key": "behavior", "validation_token": old_token}
    old.pop("context_revision")
    assert validate_clarification_answer(old)["fact_key"] == "behavior"


def test_summary_masking_keeps_more_than_300_characters_and_semantic_tail():
    text = "已知情境。" * 650 + "聯絡電話0912345678；日期20260930-20261001；現在不安全。"
    req = chat.ChatRequest(**request_payload(case_context=context(8, text)))
    diagnostics = {}
    arguments, changed = chat._prepare_agent_input(req, runtime_config(), diagnostics)
    summary = arguments["case_context"]["summary"]
    assert changed
    assert len(summary) > 3000
    assert "0912345678" not in summary
    assert "20260930-20261001" in summary
    assert summary.endswith("現在不安全。")
    assert arguments["case_context"]["summary_origin"] == "user"
    assert arguments["case_context"]["revision"] == 8
    assert "input_stages" not in diagnostics


def test_admin_input_stages_are_authenticated_ephemeral_and_not_logged(monkeypatch, caplog):
    captured = []

    class Agent:
        async def run(self, **kwargs):
            captured.append(kwargs)
            return result()

    monkeypatch.setattr(chat, "get_agent", Agent)
    write = Mock(side_effect=AssertionError("must not persist test input or settings"))
    monkeypatch.setattr(admin, "update_runtime_config", write)
    body = request_payload(
        message="ADMIN_INPUT_CANARY電話0912345678",
        history=[{"role": "user", "content": "信箱test@example.com"}],
        case_context=context(summary="摘要的電話0912345678"),
    )
    response = app.test_client().post(
        "/api/v1/admin/chat-test",
        headers={"Authorization": "Bearer offline-admin-key"},
        json={"request": body},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    stages = response.json["diagnostics"]["input_stages"]
    assert stages["message"]["before"] == body["message"]
    assert "[手機號碼]" in stages["message"]["after"]
    assert stages["history"]["before"] == stages["history"]["after_trim"]
    assert "test@example.com" not in json.dumps(stages["history"]["after"])
    assert stages["summary"]["changed"] is True
    assert "ADMIN_INPUT_CANARY" not in caplog.text
    assert "0912345678" not in caplog.text
    normal = app.test_client().post("/api/v1/chat/", json=body)
    assert normal.status_code == 200
    assert "diagnostics" not in normal.json
    assert "diagnostics" not in captured[-1]
    write.assert_not_called()


def test_v4_natural_response_uses_summary_revision_and_drops_legacy_or_private_fields(monkeypatch):
    update = {"base_revision": 4, "summary": "新的合成情境。", "evidence": ["合成測試問題"]}

    class Agent:
        async def run(self, **kwargs):
            return result(
                context_revision=4,
                summary_update=update,
                clarification=question(revision=5),
                facts_revision=99,
                fact_updates=[{"secret": "LEGACY_CANARY"}],
                answer_sections=[{"secret": "LEGACY_CANARY"}],
                provider_payload="PRIVATE_PROVIDER_CANARY",
            )

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post(
        "/api/v1/chat/", json=request_payload(case_context=context(4, "舊摘要。"))
    )
    assert response.status_code == 200
    assert response.json["context_revision"] == 4
    assert response.json["summary_update"] == update
    assert response.json["clarification"]["context_revision"] == 5
    assert not {"facts_revision", "fact_updates", "answer_sections", "provider_payload"} & response.json.keys()
    assert "CANARY" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "guidance",
    [
        {"context_revision": 1},
        {"context_revision": False},
        {"summary_update": {"base_revision": 1, "summary": "情境", "evidence": []}},
        {"summary_update": {"base_revision": 0, "summary": "字" * 4001, "evidence": []}},
        {"summary_update": {"base_revision": 0, "summary": "新情境", "evidence": []}, "clarification": question(revision=0)},
        {"clarification": {**question(), "fact_key": "behavior"}},
    ],
)
def test_v4_response_rejects_wrong_revision_oversized_summary_and_legacy_question(monkeypatch, guidance):
    class Agent:
        async def run(self, **kwargs):
            return result(**guidance)

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post("/api/v1/chat/", json=request_payload())
    assert response.status_code == 502
    assert response.json["code"] == "upstream_invalid_response"


def test_unchanged_summary_does_not_advance_context(monkeypatch):
    class Agent:
        async def run(self, **kwargs):
            return result(
                context_revision=2,
                summary_update=SummaryUpdate(base_revision=2, summary="同樣摘要").model_dump(),
                clarification=question(revision=2),
            )

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post(
        "/api/v1/chat/", json=request_payload(case_context=context(2, "同樣摘要"))
    )
    assert response.status_code == 200
    assert response.json["summary_update"] is None
    assert response.json["clarification"]["context_revision"] == 2


def test_reasoning_stream_exposes_only_public_fields_preserves_additive_text_and_done(monkeypatch):
    emitted = [
        {"text": "公開", "kind": "text", "stage": "understanding"},
        {"text": " ", "kind": "text", "stage": "understanding"},
        {"text": "摘要", "kind": "summary", "stage": "answer"},
    ]

    class Agent:
        async def run(self, on_reasoning, on_reply_delta, **kwargs):
            await on_reasoning({"kind": "encrypted", "data": "ENCRYPTED_CANARY", "stage": "answer"})
            for entry in emitted:
                await on_reasoning({**entry, "signature": "SIGNATURE_CANARY", "tool_args": "ARGS_CANARY"})
            await on_reply_delta("先整理現在需要的協助。")
            return result(reasoning=[{"text": "PRIVATE_REPLACEMENT", "kind": "text", "stage": "answer"}])

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post("/api/v1/chat/", json=request_payload(stream=True))
    events = frames(response)
    assert [payload for event, payload in events if event == "reasoning"] == emitted
    done = next(payload for event, payload in events if event == "done")
    assert done["reasoning"] == emitted
    assert "CANARY" not in response.get_data(as_text=True)
    assert "PRIVATE_REPLACEMENT" not in response.get_data(as_text=True)


def test_nonstream_reasoning_ignores_encrypted_data_and_caps_public_text(monkeypatch):
    class Agent:
        async def run(self, **kwargs):
            return result(reasoning=[
                {"type": "reasoning.encrypted", "data": "ENCRYPTED_CANARY"},
                {"text": "公" * (chat.MAX_REASONING_CHARACTERS + 1), "kind": "summary", "stage": "answer", "signature": "SIGNATURE_CANARY"},
            ])

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post("/api/v1/chat/", json=request_payload())
    assert response.status_code == 200
    assert response.json["reasoning"] == [{"text": "公" * chat.MAX_REASONING_CHARACTERS, "kind": "summary", "stage": "answer"}]
    assert "CANARY" not in response.get_data(as_text=True)


@pytest.mark.parametrize("stream", [False, True])
def test_disabling_analysis_hides_reasoning_and_analysis_on_all_transports(monkeypatch, stream):
    disabled = runtime_config(pipeline={"enable_analysis": False})
    monkeypatch.setattr(chat, "get_runtime_config", lambda: disabled)
    entry = {"text": "HIDDEN_REASONING", "kind": "text", "stage": "answer"}
    analysis = {"stage": "understanding", "summary": "HIDDEN_ANALYSIS"}

    class Agent:
        async def run(self, on_reasoning=None, on_analysis=None, **kwargs):
            if on_reasoning:
                await on_reasoning(entry)
                await on_analysis(analysis)
            return result(reasoning=[entry], analysis=[analysis])

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post("/api/v1/chat/", json=request_payload(stream=stream))
    assert "HIDDEN_" not in response.get_data(as_text=True)
    if stream:
        events = frames(response)
        assert not any(event in ("reasoning", "analysis") for event, _ in events)
        payload = next(payload for event, payload in events if event == "done")
    else:
        assert response.status_code == 200
        payload = response.json
    assert payload["analysis"] == []
    assert "reasoning" not in payload


@pytest.mark.parametrize("published", ["reasoning", "analysis", "delta"])
def test_any_visible_v4_output_disables_retry_without_removing_published_text(monkeypatch, published):
    class Agent:
        async def run(self, on_reasoning, on_analysis, on_reply_delta, **kwargs):
            if published == "reasoning":
                await on_reasoning({"text": "已提供的說明", "kind": "text", "stage": "answer"})
            elif published == "analysis":
                await on_analysis({"stage": "understanding", "summary": "已完成的整理"})
            else:
                await on_reply_delta("已完成的段落。")
            raise TimeoutError("PRIVATE_PROVIDER_CANARY")

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post("/api/v1/chat/", json=request_payload(stream=True))
    events = [(event, payload) for event, payload in frames(response) if event != "progress"]
    assert [event for event, _ in events] == [published, "error"]
    assert events[-1][1]["retryable"] is False
    assert "PRIVATE_PROVIDER_CANARY" not in response.get_data(as_text=True)


def test_disconnect_after_reasoning_cancels_producer(monkeypatch):
    state = {}

    class Agent:
        async def run(self, on_reasoning, **kwargs):
            try:
                await on_reasoning({"text": "公開摘要", "kind": "summary", "stage": "understanding"})
                await asyncio.Event().wait()
            finally:
                state["closed"] = True

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post(
        "/api/v1/chat/", json=request_payload(stream=True), buffered=False
    )
    for frame in response.response:
        if b"event: reasoning\n" in frame:
            break
    response.close()
    assert state["closed"] is True


def test_v4_provider_errors_do_not_log_or_return_case_data_in_development(monkeypatch, caplog):
    monkeypatch.setattr(chat, "get_runtime_config", lambda: replace(runtime_config(), development_mode=True))

    class Agent:
        async def run(self, **kwargs):
            raise RuntimeError("PRIVATE_V4_CANARY")

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post("/api/v1/chat/", json=request_payload(message="PRIVATE_V4_CANARY"))
    assert response.status_code == 500
    assert "PRIVATE_V4_CANARY" not in response.get_data(as_text=True)
    assert "PRIVATE_V4_CANARY" not in caplog.text


def test_health_negotiates_v4_without_exposing_admin_configuration():
    response = app.test_client().get("/api/v1/health/")
    assert response.json["capabilities"]["chat_contract_versions"] == [1, 2, 3, 4]
    assert response.json["client_settings"]["contract_version"] == 4
    assert "agent_prompt_sections" not in response.json["client_settings"]


def snapshot_v4_agent(responses):
    from tests.test_guided_v4 import agent_for

    agent = agent_for(responses)
    complete = agent.client.chat.completions.create
    snapshots = []

    async def capture(**kwargs):
        # The real agent may append retrieval handoff messages later. Capture
        # the request at this boundary, not a mutable reference to that list.
        snapshots.append(deepcopy(kwargs))
        return await complete(**kwargs)

    agent.client.chat.completions.create = capture
    return agent, snapshots


@pytest.mark.parametrize(
    "message",
    [
        "幫我判斷以下情景是用哪一條法律「女受僱者在公司茶水間倒咖啡時，男部屬突然從後方靠近並將雙手撐在流理台上將其困住。男部屬湊在女主管耳邊說：『其實我一直覺得妳穿套裝的樣子很勾人，如果我們私下試試，或許我會更聽妳的話。』語畢，伸手試圖環抱女主管的腰部。」",
        "我剛剛在公車上被人摸屁股了，我該怎麼辦？",
    ],
)
def test_controlled_pii_ab_keeps_inputs_and_complete_first_provider_payload_equal(monkeypatch, message):
    from tests.test_guided_v4 import final_answer, legal_plan, response

    plan = legal_plan()
    plan["retrieval"]["query"] = message
    comparisons = []
    for masking in (True, False):
        agent, snapshots = snapshot_v4_agent([response(plan), response(final_answer())])
        monkeypatch.setattr(chat, "get_agent", lambda agent=agent: agent)
        reply = app.test_client().post(
            "/api/v1/admin/chat-test",
            headers={"Authorization": "Bearer offline-admin-key"},
            json={
                "request": request_payload(message=message),
                "overrides": {"enable_anonymization": masking, "pipeline": {"enable_skills": False}},
            },
        )
        assert reply.status_code == 200
        diagnostics = reply.json["diagnostics"]
        assert not any(stage["changed"] for stage in diagnostics["input_stages"].values())
        assert diagnostics["pii"]["message"]["changed_values"] == 0
        assert diagnostics["pii"]["case_context"]["changed_values"] == 0
        assert diagnostics["pii"]["retrieval_query"]["changed_values"] == 0
        assert diagnostics["execution"]["model_calls"] == 2
        assert diagnostics["retrieved_count"] == 1
        assert diagnostics["cited_count"] == 0
        comparisons.append((diagnostics, snapshots[0], agent.rag.calls[0][0]))
    before, after = comparisons
    first_config = dict(before[0]["effective_config"])
    second_config = dict(after[0]["effective_config"])
    assert first_config.pop("enable_anonymization") is True
    assert second_config.pop("enable_anonymization") is False
    assert first_config == second_config
    assert before[0]["input_stages"] == after[0]["input_stages"]
    assert before[1] == after[1]
    assert before[2] == after[2] == message


def test_api_signed_email_choices_mask_values_and_keep_tokens_out_of_provider_prompt(monkeypatch):
    from tests.test_guided_v4 import plan, response

    issued = question()
    issued["max_selections"] = 2
    issued["options"] = [
        {"label": "合成聯絡一", "value": "a@example.com"},
        {"label": "合成聯絡二", "value": "b@example.com"},
    ]
    issued["validation_token"] = sign_clarification(issued, contract_version=4)
    answer = selected_answer(
        issued,
        value=["a@example.com", "b@example.com"],
        allowed_values=["a@example.com", "b@example.com"],
    )
    agent, snapshots = snapshot_v4_agent([response(plan())])
    monkeypatch.setattr(chat, "get_agent", lambda: agent)
    monkeypatch.setattr(chat, "get_runtime_config", lambda: runtime_config(pipeline={"enable_skills": False}))
    reply = app.test_client().post(
        "/api/v1/chat/", json=request_payload(clarification_answer=answer)
    )
    assert reply.status_code == 200
    assert reply.json["anonymized"] is True
    prompt = json.dumps(snapshots[0]["messages"], ensure_ascii=False)
    assert "a@example.com" not in prompt and "b@example.com" not in prompt
    assert issued["validation_token"] not in prompt
    assert "validation_token" not in prompt and "allowed_values" not in prompt
    assert "[電子郵件]" in prompt and "另一已遮罩值2" in prompt
