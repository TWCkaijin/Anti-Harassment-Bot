"""Engineering contract regressions; these fixtures are not legal expert labels."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from backend.app.agents import guided_chat as guided
from backend.app.core.case_context import CaseContext, FactUpdate, validate_fact_updates
from backend.app.core.clarification_tokens import sign_clarification, validate_clarification_answer
from backend.app.rag.base import RAGDocument
from tests.test_agent import fake_runtime_config
from tests.test_guided_chat import agent_for, answer, model_answer


def v3_answer(**overrides):
    result = answer(
        understanding={
            "intent": "legal_direction",
            "scope": "personal",
            "evidence": [],
            "sufficient": True,
            "limitation": "",
        },
        question=None,
    )
    result.update(overrides)
    return result


def question(key="behavior", mode="multiple", limit=3):
    return {
        "fact_key": key,
        "question": "涉及哪些行為？可複選。" if mode == "multiple" else "主要是哪一類行為？",
        "reason": "需要依具體行為確認所需資訊。",
        "options": [{"label": item, "value": item} for item in ("言語", "訊息", "跟蹤")],
        "selection_mode": mode,
        "max_selections": limit,
    }


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(guided, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(guided, "get_matching_scenario_scripts", lambda *args, **kwargs: ())


@pytest.mark.parametrize(
    "text,explicit",
    [
        ("我是受僱者，請問適用什麼法律？", True),
        ("我是受僱者，同事說「你不准告訴別人」，可以申訴嗎？", True),
        ("她說「我是受僱者」", False),
        ("如果我是受僱者，如何申訴？", False),
        ("我不確定我是受僱者", False),
        ("我是受僱者嗎？", False),
        ("例題：她說我是受僱者", False),
    ],
)
def test_evidence_ownership_is_clause_local(text, explicit):
    update = FactUpdate(
        fact_key="subject_role",
        status="provided",
        value="受僱者",
        evidence="我是受僱者",
        kind="explicit",
    )
    accepted = validate_fact_updates([update], CaseContext(), [text])
    assert (accepted[0]["kind"] == "explicit") is explicit


def test_multi_values_require_schema_two_and_do_not_split_old_strings():
    old = CaseContext.model_validate(
        {"facts": {"behavior": {"status": "provided", "value": "言語,訊息"}}}
    )
    assert old.facts["behavior"].value == "言語,訊息"
    with pytest.raises(ValidationError):
        CaseContext.model_validate(
            {"facts": {"behavior": {"status": "provided", "value": ["言語", "訊息"]}}}
        )
    new = CaseContext.model_validate(
        {
            "schema_version": 2,
            "facts": {"behavior": {"status": "provided", "value": ["言語", "訊息"]}},
        }
    )
    assert new.facts["behavior"].value == ["言語", "訊息"]


def issued_answer(values):
    issued = {"question_id": "case.behavior.1", **question()}
    return {
        "question_id": issued["question_id"],
        "fact_key": "behavior",
        "status": "provided",
        "value": values,
        "validation_token": sign_clarification(issued),
    }


def test_signed_multiple_answer_allows_one_freeform_and_preset():
    validate_clarification_answer(issued_answer(["言語", "試圖環抱"]))


@pytest.mark.parametrize(
    "values",
    [
        ["言語", "訊息", "跟蹤", "試圖環抱"],
        ["言語", "言語"],
        ["言語", "不確定"],
        ["試圖環抱", "其他自述"],
    ],
)
def test_signed_multiple_answer_rejects_limits_duplicates_reserved_and_two_other(values):
    with pytest.raises(ValueError):
        validate_clarification_answer(issued_answer(values))


def test_signed_answer_cannot_change_question_shape():
    submitted = issued_answer(["言語", "訊息"])
    submitted["max_selections"] = 4
    with pytest.raises(ValueError):
        validate_clarification_answer(submitted)
    submitted.pop("max_selections")
    submitted["fact_key"] = "subject_role"
    with pytest.raises(ValueError):
        validate_clarification_answer(submitted)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,mode,limit", [("subject_role", "multiple", 3), ("behavior", "single", 1)]
)
async def test_model_selects_mode_from_question_not_fact_whitelist(key, mode, limit):
    data = v3_answer(question_fact=key, question=question(key, mode, limit))
    data["understanding"]["sufficient"] = False
    agent = agent_for([model_answer(data)])
    result = await agent.run("我想補充資料", contract_version=3, case_context={"schema_version": 2})
    issued = result.guidance["clarification"]
    assert issued["selection_mode"] == mode
    assert issued["max_selections"] == limit
    assert issued["validation_token"]
    assert result.guidance["execution"]["model_calls"] == 1


@pytest.mark.asyncio
async def test_sufficient_answer_cancels_residual_question_without_erasing_answer():
    data = v3_answer(question_fact="work_related", question=question("work_related", "single", 1))
    data["answer_sections"][0]["text"] = "部屬對主管的行為應依實際工作關係評估。"
    data["answer_sections"][2]["text"] = "可以保留對話紀錄。請問是否在工作中？"
    agent = agent_for([model_answer(data)])
    result = await agent.run("女主管遭男部屬試圖環抱，適用什麼法律？", contract_version=3)
    payload = json.loads(result.reply)
    assert payload["interaction_mode"] == "answer"
    assert result.guidance["clarification"] is None
    assert "部屬對主管" in payload["reply"]
    assert "保留對話紀錄" in payload["reply"]
    assert "請問是否" not in payload["reply"]


@pytest.mark.asyncio
async def test_blocked_repeated_question_keeps_valid_legal_answer_and_citation():
    data = answer(question_fact="other_role")
    data["answer_sections"][0] = {
        "kind": "direction",
        "text": "可能涉及《性別平等工作法》第12條的職場性騷擾評估。",
        "source_ids": ["rag_documents/work-12"],
    }
    data["answer_sections"][2]["text"] = "先保存事件紀錄。請告訴我對方身分。"
    agent = agent_for([model_answer(data)])

    async def retrieve(*args, **kwargs):
        return [
            RAGDocument(
                content="工程測試法源",
                doc_id="work-12",
                metadata={
                    "source": "性別平等工作法第12條",
                    "article": "12",
                    "collection": "rag_documents",
                    "version": "2023-08-16",
                    "checked_at": "2026-10-01",
                },
            )
        ]

    agent.rag.retrieve = retrieve
    result = await agent.run(
        "我想了解法律方向",
        contract_version=2,
        case_context={
            "facts": {
                "other_role": {"status": "declined"},
                "subject_role": {"status": "provided", "value": "受僱者"},
            }
        },
    )
    assert "第12條" in result.reply
    assert "先保存事件紀錄" in result.reply
    assert "請告訴我" not in result.reply
    assert result.sources[0]["doc_id"] == "rag_documents/work-12"


def test_claim_repair_keeps_unrelated_support_and_resource_duration():
    data = answer()
    data["answer_sections"][1]["text"] = (
        "先保留證據。申訴期限為一年。可查看24小時支持資源與現行資源資訊。"
    )
    sections = guided._legal_sections(guided.GuidedAnswer.model_validate(data), [], "法律資訊")
    text = sections[1]["text"]
    assert "先保留證據" in text
    assert "24小時支持資源" in text
    assert "現行資源資訊" in text
    assert "申訴期限為一年" not in text


def test_annotation_mode_keeps_raw_claim_in_diagnostics_without_displaying_false_precision():
    data = answer()
    data["answer_sections"][0]["text"] = "依刑法第999條可以申訴。"
    diagnostics = {}
    sections = guided._legal_sections(
        guided.GuidedAnswer.model_validate(data),
        [],
        "法律資訊",
        content_policy="annotate",
        diagnostics=diagnostics,
    )
    assert "第999條" not in sections[0]["text"]
    assert "待核對" in sections[0]["text"]
    assert diagnostics["rewrites"][0]["before"] == "依刑法第999條可以申訴。"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [2, 3])
async def test_disabled_rag_makes_zero_retrieval_calls(version):
    agent = agent_for([model_answer(v3_answer() if version == 3 else answer())])
    result = await agent.run("請說明申訴法律", contract_version=version, use_rag=False)
    assert agent.rag.calls == []
    assert "tools" not in agent.client.chat.completions.calls[0]
    assert not result.rag_used


@pytest.mark.asyncio
async def test_analysis_only_echoes_verified_user_evidence_and_actual_sources():
    data = v3_answer()
    data["understanding"]["evidence"] = ["我是受僱者", "不存在的敘述"]
    agent = agent_for([model_answer(data)])
    events = []

    async def event(item):
        events.append(item)

    result = await agent.run("我是受僱者，請說明法律方向？", contract_version=3, on_analysis=event)
    assert events == result.guidance["analysis"]
    assert events[0]["facts"] == ["我是受僱者"]
    assert events[2]["source_labels"] == []
    assert "實際引用 0 筆" in events[2]["summary"]
    assert "不存在的敘述" not in json.dumps(events, ensure_ascii=False)


@pytest.mark.asyncio
async def test_third_person_case_does_not_update_personal_summary():
    data = v3_answer()
    data["understanding"].update(scope="third_person", evidence=["女受僱者", "男部屬"])
    data["fact_updates"] = [
        {
            "fact_key": "subject_role",
            "status": "provided",
            "value": "受僱者",
            "evidence": "女受僱者",
            "kind": "explicit",
        }
    ]
    agent = agent_for([model_answer(data)])
    result = await agent.run("女受僱者遭男部屬試圖環抱，請分析法律方向。", contract_version=3)
    assert result.guidance["fact_updates"] == []
    assert "第三人稱情境" in result.guidance["analysis"][0]["summary"]


@pytest.mark.asyncio
async def test_single_snapshot_and_disabled_extraction_analysis_skills(monkeypatch):
    config = fake_runtime_config()
    config = replace(
        config,
        pipeline={
            **config.pipeline,
            "extract_facts": False,
            "enable_analysis": False,
            "enable_skills": False,
        },
    )
    monkeypatch.setattr(guided, "get_runtime_config", lambda: pytest.fail("Do not re-read config"))
    monkeypatch.setattr(
        guided, "get_matching_scenario_scripts", lambda *a, **k: pytest.fail("Skills disabled")
    )
    agent = agent_for([model_answer(v3_answer())])
    result = await agent.run("我是受僱者", contract_version=3, runtime_config=config)
    assert result.guidance["fact_updates"] == []
    assert result.guidance["analysis"] == []
