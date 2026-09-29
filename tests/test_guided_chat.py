"""V2 contract, privacy, routing and citation regression tests; no external services."""

import json

import pytest
from pydantic import ValidationError

import backend.app.agents.guided_chat as guided
from backend.app.agents.openrouter_agent import AgentContractError, OpenRouterAgent
from backend.app.api import chat as chat_api
from backend.app.core.case_context import CaseContext, FactUpdate, validate_fact_updates
from backend.app.main import app
from backend.app.rag.base import RAGDocument
from backend.app.rag.firestore_vector import FirestoreVectorRAG
from tests.test_agent import (
    FakeClient,
    FakeCompletions,
    FakeMessage,
    FakeResponse,
    FakeToolCall,
    fake_runtime_config,
)
from tests.test_rag import FakeDB


def answer(**overrides):
    result = {
        "answer_sections": [
            {"kind": "direction", "text": "可以先整理你希望取得的協助。", "source_ids": []},
            {"kind": "basis", "text": "相關資料仍需核對。", "source_ids": []},
            {"kind": "next_steps", "text": "你可以自行決定是否繼續說明。", "source_ids": []},
        ],
        "fact_updates": [],
        "question_fact": None,
        "suggested_replies": ["我想先整理情況", "我想了解下一步"],
        "action_buttons": [],
        "emotion": "未知",
        "emotion_color": "gray",
    }
    result.update(overrides)
    return result


class Rag:
    def __init__(self):
        self.calls = []

    async def retrieve(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return [
            RAGDocument(
                content="測試資料，不是真實法律標準答案。",
                doc_id="fixture",
                metadata={"source": "合成來源", "collection": "rag_documents"},
            )
        ]


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(guided, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(guided, "get_matching_scenario_scripts", lambda *args, **kwargs: ())
    monkeypatch.setattr(chat_api, "get_runtime_config", fake_runtime_config)


def agent_for(responses):
    agent = object.__new__(OpenRouterAgent)
    agent.client = FakeClient(FakeCompletions(responses))
    agent.rag = Rag()
    return agent


def model_answer(data=None):
    return FakeResponse(FakeMessage(json.dumps(data or answer(), ensure_ascii=False)))


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


def test_local_facts_cannot_be_silently_sent_to_legacy_contract():
    with pytest.raises(ValidationError):
        chat_api.ChatRequest(message="你好", case_context={})


def test_health_advertises_supported_contracts():
    assert app.test_client().get("/api/v1/health/").json["capabilities"][
        "chat_contract_versions"
    ] == [1, 2]


@pytest.mark.asyncio
async def test_known_missing_fact_uses_no_model_or_retrieval():
    agent = agent_for([])
    events = []

    async def progress(event):
        events.append(event["phase"])

    async def delta(text):
        events.append("delta")

    result = await agent.run(
        "我在公司被騷擾，要怎麼申訴",
        contract_version=2,
        on_progress=progress,
        on_reply_delta=delta,
    )
    assert result.guidance["execution"]["route"] == "short_clarify"
    assert result.guidance["execution"]["model_calls"] == 0
    assert result.guidance["clarification"]["fact_key"] == "other_role"
    assert agent.rag.calls == []
    assert agent.client.chat.completions.calls == []
    assert events[:2] == ["preparing", "validating"]
    assert events[2:] and set(events[2:]) == {"delta"}


@pytest.mark.asyncio
async def test_processing_reports_actual_steps_without_provider_reasoning(monkeypatch):
    monkeypatch.setattr(
        guided, "get_runtime_config", lambda: fake_runtime_config(reasoning_effort="high")
    )
    message = FakeMessage(json.dumps(answer(), ensure_ascii=False))
    message.reasoning_content = "PRIVATE_REASONING_CANARY"
    message.reasoning_details = [{"text": "PRIVATE_REASONING_CANARY"}]
    agent = agent_for([FakeResponse(message)])
    events = []

    async def progress(event):
        events.append(event)

    async def delta(text):
        events.append({"text": text})

    result = await agent.run(
        "請介紹一般性騷擾法律",
        contract_version=2,
        on_progress=progress,
        on_reply_delta=delta,
    )
    assert [event.get("phase") for event in events[:4]] == [
        "preparing",
        "retrieving",
        "waiting_model",
        "validating",
    ]
    assert all(set(event) == {"text"} for event in events[4:])
    assert events[4:]
    assert "PRIVATE_REASONING_CANARY" not in json.dumps(events)
    assert "PRIVATE_REASONING_CANARY" not in result.reply
    assert agent.client.chat.completions.calls[0]["extra_body"]["reasoning"]["exclude"] is True


@pytest.mark.asyncio
async def test_explicit_roles_avoid_repeated_questions():
    agent = agent_for([model_answer()])
    result = await agent.run("我是學生，對方是老師，我想了解申訴流程", contract_version=2)
    assert result.guidance["clarification"] is None
    assert {item["fact_key"] for item in result.guidance["fact_updates"]} == {
        "subject_role",
        "other_role",
    }
    assert all(item["kind"] == "explicit" for item in result.guidance["fact_updates"])


@pytest.mark.asyncio
async def test_general_law_question_direct_retrieval_one_generation():
    agent = agent_for([model_answer()])
    result = await agent.run("請介紹一般性騷擾法律", contract_version=2)
    assert result.guidance["execution"]["route"] == "direct_retrieval"
    assert result.guidance["execution"]["model_calls"] == 1
    assert "tools" not in agent.client.chat.completions.calls[0]
    assert agent.rag.calls[0][1]["selected_data_types"] == ["law"]
    assert result.sources[0]["version"] is None


@pytest.mark.asyncio
async def test_mixed_query_reserves_requested_evidence_types():
    agent = agent_for([model_answer()])
    await agent.run("申訴流程相關的法律與判決案例", contract_version=2)
    assert agent.rag.calls[0][1]["selected_data_types"] == ["law", "remedy", "judgment"]
    assert agent.rag.calls[0][1]["preserve_data_types"]


@pytest.mark.asyncio
async def test_unknown_request_keeps_tool_fallback():
    agent = agent_for(
        [FakeResponse(FakeMessage(tool_calls=[FakeToolCall("求助資源", "remedy")])), model_answer()]
    )
    result = await agent.run("這件事讓我很困擾", contract_version=2)
    assert result.guidance["execution"]["route"] == "tool"
    assert result.guidance["execution"]["model_calls"] == 2


@pytest.mark.asyncio
async def test_typed_answer_continues_request_and_asks_next_missing_fact():
    agent = agent_for([])
    result = await agent.run(
        "對方和你的關係？\n主管",
        contract_version=2,
        history=[{"role": "user", "content": "我在公司被騷擾，怎麼申訴"}],
        case_context={
            "revision": 1,
            "facts": {"other_role": {"status": "provided", "value": "主管"}},
        },
        clarification_answer={
            "question_id": "case.other_role.0",
            "fact_key": "other_role",
            "status": "provided",
            "value": "主管",
        },
    )
    assert result.guidance["clarification"]["fact_key"] == "subject_role"
    assert result.guidance["execution"]["model_calls"] == 0


@pytest.mark.asyncio
async def test_completed_typed_intake_retrieves_original_request_once():
    agent = agent_for([model_answer()])
    result = await agent.run(
        "你的身分？\n受僱者",
        contract_version=2,
        history=[{"role": "user", "content": "我在公司被騷擾，怎麼申訴"}],
        case_context={
            "revision": 2,
            "facts": {
                "other_role": {"status": "provided", "value": "主管"},
                "subject_role": {"status": "provided", "value": "受僱者"},
            },
        },
        clarification_answer={
            "question_id": "case.subject_role.1",
            "fact_key": "subject_role",
            "status": "provided",
            "value": "受僱者",
        },
    )
    assert result.guidance["execution"]["model_calls"] == 1
    assert agent.rag.calls[0][1]["selected_data_types"] == ["law", "remedy"]
    assert "怎麼申訴" in agent.rag.calls[0][0]


@pytest.mark.asyncio
async def test_declined_fact_not_repeated_and_revision_echoed():
    data = answer(question_fact="other_role")
    data["answer_sections"][2]["text"] = "請告訴我對方身分。"
    agent = agent_for([model_answer(data)])
    context = {
        "revision": 7,
        "facts": {"other_role": {"status": "declined"}, "subject_role": {"status": "unknown"}},
    }
    result = await agent.run("想了解申訴流程", contract_version=2, case_context=context)
    assert result.guidance["facts_revision"] == 7
    assert result.guidance["clarification"] is None
    assert "請告訴我對方身分" not in result.reply


@pytest.mark.asyncio
async def test_urgent_message_is_never_blocked_by_intake():
    agent = agent_for([model_answer()])
    result = await agent.run("我現在被限制行動，要怎麼辦", contract_version=2)
    assert result.guidance["clarification"] is None
    assert result.guidance["execution"]["route"] == "tool"


@pytest.mark.asyncio
async def test_unclear_legal_query_retains_tool_flow():
    agent = agent_for([model_answer()])
    result = await agent.run("不確定法律問題同時涉及不同情境，想看相關判決", contract_version=2)
    assert result.guidance["execution"]["route"] == "tool"
    assert agent.client.chat.completions.calls[0]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_fabricated_citation_never_reaches_sse():
    data = answer()
    data["answer_sections"][0]["source_ids"] = ["invented"]
    agent = agent_for([model_answer(data)])
    emitted = []

    async def delta(text):
        emitted.append(text)

    with pytest.raises(AgentContractError, match="Invalid guided"):
        await agent.run("法律資訊", contract_version=2, on_reply_delta=delta)
    assert emitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim",
    [
        "依性騷擾防治法第13條一律在一年內申訴。",
        "依性騷擾防治法第13條，你應於事發後一年提出申訴。",
        "請在事件發生後30天提出申訴。",
    ],
)
async def test_unknown_version_deadline_replaced_before_streaming(claim):
    data = answer()
    data["answer_sections"][1] = {
        "kind": "basis",
        "text": claim,
        "source_ids": ["rag_documents/fixture"],
    }
    agent = agent_for([model_answer(data)])
    emitted = []

    async def delta(text):
        emitted.append(text)

    result = await agent.run("一般申訴期限", contract_version=2, on_reply_delta=delta)
    assert "一年內" not in "".join(emitted)
    assert claim not in "".join(emitted)
    assert guided.LIMITATION in "".join(emitted)
    assert "自行決定" in "".join(emitted)
    assert json.loads(result.reply)["reply"] == "".join(emitted)


def test_known_old_version_does_not_establish_current_law_or_deadline():
    data = answer()
    data["answer_sections"][1] = {
        "kind": "basis",
        "text": "依現行性騷擾防治法第13條，你應於事發後一年提出申訴。",
        "source_ids": ["law/old"],
    }
    sources = [{"doc_id": "law/old", "version": "2009"}]
    sections = guided._legal_sections(guided.GuidedAnswer.model_validate(data), sources, "申訴")
    assert sections[1]["text"] == guided.LIMITATION


@pytest.mark.parametrize(
    "text",
    [
        "我不是學生，但她說我是學生",
        "假如我是學生",
        "我是學生嗎？",
        "若我是學生，適用什麼法律",
        "我不確定我是學生",
        "有人說我是學生",
    ],
)
def test_quoted_negated_or_hypothetical_facts_require_confirmation(text):
    update = FactUpdate(
        fact_key="subject_role",
        status="provided",
        value="學生",
        evidence="我是學生",
        kind="explicit",
    )
    result = validate_fact_updates([update], CaseContext(), [text])
    assert result[0]["kind"] == "confirmation"


@pytest.mark.parametrize(
    "key,value,evidence",
    [
        ("age_group", "成年", "我未成年"),
        ("education_related", "是", "我是學生"),
        ("other_role", "主管", "我不確定對方是主管"),
    ],
)
def test_categories_cannot_be_inferred_from_substrings_or_roles(key, value, evidence):
    update = FactUpdate(
        fact_key=key, status="provided", value=value, evidence=evidence, kind="explicit"
    )
    assert validate_fact_updates([update], CaseContext(), [evidence])[0]["kind"] == "confirmation"


def test_facts_require_user_evidence_and_cannot_overwrite_corrections():
    update = FactUpdate(
        fact_key="other_role",
        status="provided",
        value="主管",
        evidence="對方是主管",
        kind="explicit",
    )
    assert validate_fact_updates([update], CaseContext(), ["對方是老師"]) == []
    context = CaseContext(facts={"other_role": {"status": "provided", "value": "顧客"}})
    assert validate_fact_updates([update], context, ["對方是主管"])[0]["kind"] == "confirmation"


def test_new_case_text_redacted_even_with_legacy_toggle_off():
    request = chat_api.ChatRequest(
        message="想了解程序",
        contract_version=2,
        case_context={
            "facts": {"desired_help": {"status": "provided", "value": "please call 0912345678 now"}}
        },
    )
    kwargs, modified = chat_api._prepare_agent_input(
        request, fake_runtime_config(enable_anonymization=False)
    )
    assert modified
    assert "0912345678" not in json.dumps(kwargs["case_context"])


def test_v2_provider_error_never_logs_case_canary(monkeypatch, caplog):
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
            "contract_version": 2,
            "case_context": {
                "facts": {
                    "desired_help": {"status": "provided", "value": "PRIVATE_CASE_CANARY_8123"}
                }
            },
        },
    )
    assert response.status_code == 500
    assert "PRIVATE_CASE_CANARY_8123" not in caplog.text
    assert "PRIVATE_CASE_CANARY_8123" not in response.get_data(as_text=True)
    assert "debug_message" not in response.json


@pytest.mark.asyncio
async def test_mixed_retrieval_uses_one_embedding_and_no_unrequested_collection(monkeypatch):
    rag = object.__new__(FirestoreVectorRAG)
    rag.db = FakeDB()
    embeddings = []

    async def embed(query):
        embeddings.append(query)
        return [0.1, 0.2]

    monkeypatch.setattr(rag, "_get_embedding", embed)
    results = await rag.retrieve(
        "query", top_k=1, selected_data_types=["law", "remedy"], preserve_data_types=True
    )
    assert len(embeddings) == 1
    assert len(results) == 2
    assert set(rag.db.collections) == {"rag_documents", "rag_remedies"}


def test_vector_provider_error_body_is_not_logged(caplog):
    from backend.app.rag.base import RAGVectorSearchError

    class FailingDB:
        def collection(self, name):
            raise RuntimeError("PRIVATE_VECTOR_CANARY_925")

    rag = object.__new__(FirestoreVectorRAG)
    rag.db = FailingDB()
    with pytest.raises(RAGVectorSearchError):
        rag._retrieve_from_collection("rag_documents", [0.1], 3)
    assert "PRIVATE_VECTOR_CANARY_925" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_v2_http_uses_stateless_agent_result(monkeypatch):
    agent = agent_for([])
    monkeypatch.setattr(chat_api, "get_agent", lambda: agent)
    response = app.test_client().post(
        "/api/v1/chat/",
        json={
            "message": "我被騷擾，怎麼申訴",
            "contract_version": 2,
            "case_context": {"revision": 4, "facts": {}},
        },
    )
    assert response.status_code == 200
    assert response.json["contract_version"] == 2
    assert response.json["facts_revision"] == 4
    assert response.json["clarification"]["fact_key"] == "other_role"
    assert response.json["execution"]["model_calls"] == 0


def test_v2_sse_only_publishes_facts_in_done(monkeypatch):
    monkeypatch.setattr(chat_api, "get_agent", lambda: agent_for([]))
    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "我是學生，被騷擾想申訴", "contract_version": 2, "stream": True},
    )
    body = response.get_data(as_text=True)
    before_done, done = body.split("event: done")
    assert "fact_updates" not in before_done
    assert '"fact_updates"' in done
    assert "event: error" not in body


def test_v2_progress_reports_new_field_masking_when_legacy_toggle_is_off(monkeypatch):
    monkeypatch.setattr(chat_api, "get_agent", lambda: agent_for([]))
    monkeypatch.setattr(
        chat_api, "get_runtime_config", lambda: fake_runtime_config(enable_anonymization=False)
    )
    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "我是學生，被騷擾想申訴", "contract_version": 2, "stream": True},
    )
    frames = response.get_data(as_text=True).split("\n\n")
    progress = [
        json.loads(frame.split("data: ", 1)[1])
        for frame in frames
        if frame.startswith("event: progress")
    ]
    assert progress[0]["phase"] == "anonymizing"
    assert progress[1]["phase"] == "preparing"
    assert all(set(event) == {"phase", "elapsed_ms"} for event in progress)
