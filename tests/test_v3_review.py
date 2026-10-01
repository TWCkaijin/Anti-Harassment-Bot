"""Independent v3 regressions using synthetic evidence, without legal correctness claims."""

import json

import pytest

from backend.app.agents import guided_chat as guided
from backend.app.core.clarification_tokens import validate_clarification_answer
from tests.test_agent import fake_runtime_config
from tests.test_guided_chat import agent_for, answer, model_answer
from tests.test_guided_v3 import question, v3_answer


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(guided, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(guided, "get_matching_scenario_scripts", lambda *args, **kwargs: ())


def source(identifier, law, article):
    return {
        "doc_id": identifier,
        "law_name": law,
        "article_number": article,
        "label": f"{law}第{article}條",
        "version": "synthetic-version",
        "checked_at": "2026-10-01",
    }


def check_claim(claim, sources, documents=None):
    data = answer()
    data["answer_sections"][0] = {
        "kind": "direction",
        "text": claim,
        "source_ids": [item["doc_id"] for item in sources],
    }
    diagnostics = {}
    sections = guided._legal_sections(
        guided.GuidedAnswer.model_validate(data),
        sources,
        "合成問題",
        documents=documents,
        diagnostics=diagnostics,
    )
    return sections[0], diagnostics


def test_law_and_article_must_match_the_same_source():
    # Neither supplied document supports this law/article pair.
    claim = "合成測試：依性別平等工作法第12條評估。"
    section, _ = check_claim(
        claim,
        [
            source("law/work-13", "性別平等工作法", "13"),
            source("law/civil-12", "民法", "12"),
        ],
    )
    assert claim not in section["text"]


def test_article_matching_is_exact_and_normalizes_chinese_numbers():
    unsupported = "合成測試：依性別平等工作法第12條評估。"
    section, _ = check_claim(unsupported, [source("law/work-112", "性別平等工作法", "112")])
    assert unsupported not in section["text"]
    supported = "合成測試：依性別平等工作法第十二條評估。"
    section, _ = check_claim(supported, [source("law/work-12", "性別平等工作法", "12")])
    assert supported == section["text"]


def test_checked_metadata_does_not_support_a_contradicting_deadline():
    evidence = source("law/work-12", "性別平等工作法", "12")
    claim = "合成測試：依性別平等工作法第12條，申訴期限為一年。"
    section, _ = check_claim(
        claim,
        [evidence],
        documents=[{"source": evidence, "content": "合成測試資料：申訴期限為二年。"}],
    )
    assert claim not in section["text"]


def test_repair_does_not_leave_a_citation_attached_only_to_removed_claim():
    section, _ = check_claim(
        "合成測試：依性別平等工作法第999條評估。你可以先休息。",
        [source("law/work-12", "性別平等工作法", "12")],
    )
    assert "你可以先休息" in section["text"]
    assert "第999條" not in section["text"]
    assert section["source_ids"] == []


def test_v3_outbound_schema_uses_supported_strict_composition():
    schema = guided.GUIDED_V3_RESPONSE_FORMAT["json_schema"]["schema"]
    assert schema["type"] == "object"

    def check(node):
        if isinstance(node, dict):
            assert not set(node).intersection(
                {
                    "oneOf",
                    "discriminator",
                    "default",
                    "allOf",
                    "not",
                    "if",
                    "then",
                    "else",
                    "dependentRequired",
                    "dependentSchemas",
                }
            )
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["properties"]) == set(node["required"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


@pytest.mark.asyncio
async def test_third_person_typed_followup_never_populates_personal_facts():
    scenario = "女主管遭男部屬試圖環抱，請問適用什麼法律？"
    first = v3_answer(question_fact="subject_role", question=question("subject_role", "single", 1))
    first["understanding"].update(scope="third_person", sufficient=False)
    first["question"]["options"] = [{"label": "受僱者", "value": "受僱者"}]
    first_agent = agent_for([model_answer(first)])
    issued_result = await first_agent.run(scenario, contract_version=3)
    issued = issued_result.guidance["clarification"]
    assert issued["context_scope"] == "scenario"
    selected = {
        "question_id": issued["question_id"],
        "fact_key": issued["fact_key"],
        "status": "provided",
        "value": "受僱者",
        "context_scope": "scenario",
        "validation_token": issued["validation_token"],
    }
    validate_clarification_answer(selected)
    with pytest.raises(ValueError):
        validate_clarification_answer({**selected, "context_scope": "personal"})
    second = v3_answer()
    second["understanding"]["scope"] = "third_person"
    second_agent = agent_for([model_answer(second)])
    output = await second_agent.run(
        "本情境的身分是受僱者",
        contract_version=3,
        case_context={
            "schema_version": 2,
            "facts": {"desired_help": {"status": "provided", "value": "尋找支持資源"}},
        },
        history=[{"role": "user", "content": scenario}],
        clarification_answer=selected,
    )
    assert output.guidance["fact_updates"] == []
    prompt_messages = second_agent.client.chat.completions.calls[0]["messages"]
    text = next(
        message["content"]
        for message in reversed(prompt_messages)
        if isinstance(message, dict)
        and message.get("role") == "user"
        and "使用者目前的案件摘要" in message.get("content", "")
    )
    context_text = text.split("\n本輪訊息：\n", 1)[0].split("\n", 1)[1]
    assert "subject_role" not in json.loads(context_text)["facts"]
    assert "男部屬" in second_agent.rag.calls[0][0]
