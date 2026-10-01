"""Presentation guidance and v4 Markdown compatibility; no model or network calls."""

import json

import pytest

import backend.app.agents.guided_v4 as v4
from backend.app.agents.openrouter_agent import AgentContractError
from backend.app.core.agent_prompts import (
    ADAPTIVE_PRESENTATION_INSTRUCTION,
    assemble_legacy_instruction,
    assemble_service_instruction,
    get_default_prompt_sections,
)
from tests.test_agent import fake_runtime_config
from tests.test_guided_v4 import agent_for, final_answer, legal_plan, plan, response, run, streamed


@pytest.fixture(autouse=True)
def no_external_skills(monkeypatch):
    monkeypatch.setattr(v4, "get_matching_scenario_scripts", lambda *args, **kwargs: ())


def test_presentation_rule_follows_admin_templates_without_removing_domain_overrides():
    overrides = {
        "communication_principles": "每次固定分成三段，開頭安慰，結尾列電話。",
        "analysis_rules": "保留管理員提供的法律與安全參考規則。",
        "output_format": "舊版JSON契約與固定標題。",
    }
    before = dict(overrides)
    merged = assemble_service_instruction(overrides)

    assert merged.endswith(ADAPTIVE_PRESENTATION_INSTRUCTION)
    assert merged.index(overrides["communication_principles"]) < merged.index(
        ADAPTIVE_PRESENTATION_INSTRUCTION
    )
    assert overrides["analysis_rules"] in merged
    assert overrides["output_format"] not in merged
    assert "僅回覆呈現方式以本節為準" in merged
    assert "法律、安全、資料界線與各版本JSON輸出契約仍依原規則" in merged
    assert overrides == before


def test_legacy_keeps_its_json_contract_and_default_domain_guidance():
    defaults = get_default_prompt_sections()
    merged = assemble_legacy_instruction()

    assert defaults["analysis_rules"] in merged
    assert defaults["important_resources"] in merged
    assert defaults["output_format"] in merged
    assert merged.count(ADAPTIVE_PRESENTATION_INSTRUCTION) == 1
    assert "answer_units" not in merged


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_both_v4_stages_receive_adaptive_guidance_and_keep_complete_markdown_items(stream):
    units = [
        {
            "text": "1. **先整理想釐清的情況。** 可以記下對方與你的關係。\n\n   也可以分開記錄每次互動，方便後續說明。",
            "source_ids": ["laws/law12"],
        },
        {
            "text": "2. **選擇適合自己的下一步。** 可以先向可信任的人談談，再決定是否進一步求助。",
            "source_ids": [],
        },
    ]
    provider_response = streamed if stream else response
    agent = agent_for(
        [provider_response(legal_plan()), provider_response(final_answer(answer_units=units))]
    )
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    result = await run(
        agent,
        user_message="請分別說明可以整理哪些情況及如何選擇下一步。",
        runtime_config=fake_runtime_config(
            agent_prompt_sections={"communication_principles": "舊提示要求一律固定三段。"}
        ),
        **({"on_reply_delta": on_delta} if stream else {}),
    )
    calls = agent.client.chat.completions.calls
    assert len(calls) == 2
    for call in calls:
        shared = call["messages"][0]["content"]
        assert shared.endswith(ADAPTIVE_PRESENTATION_INSTRUCTION)
        assert "舊提示要求一律固定三段。" in shared
        contract = call["messages"][1]["content"]
        assert v4.V4_ANSWER_UNIT_INSTRUCTION in contract
        assert "每個unit是一個完整句子或短段落" not in contract
    final_stage = calls[1]["messages"][-2]["content"]
    assert v4.V4_ANSWER_UNIT_INSTRUCTION in final_stage
    properties = calls[1]["response_format"]["json_schema"]["schema"]["$defs"]["AnswerUnit"][
        "properties"
    ]
    assert set(properties) == {"text", "source_ids"}
    assert properties["source_ids"]["items"]["enum"] == ["laws/law12"]
    expected = "\n\n".join(unit["text"] for unit in units)
    assert json.loads(result.reply)["reply"] == expected
    assert [source["doc_id"] for source in result.sources] == ["laws/law12"]
    if stream:
        assert deltas == [units[0]["text"], "\n\n" + units[1]["text"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_single_support_paragraph_remains_valid_without_forced_bullets_or_bold(stream):
    text = "你可以按自己的步調來，不必現在就做決定。想先休息一下也可以。"
    provider_response = streamed if stream else response
    agent = agent_for([provider_response(plan(answer_units=[{"text": text, "source_ids": []}]))])

    async def on_delta(_text):
        pass

    result = await run(agent, **({"on_reply_delta": on_delta} if stream else {}))
    assert json.loads(result.reply)["reply"] == text
    assert len(agent.client.chat.completions.calls) == 1
    assert not agent.rag.calls


@pytest.mark.asyncio
async def test_markdown_does_not_allow_an_unknown_source_id():
    agent = agent_for(
        [
            response(legal_plan()),
            response(
                final_answer(
                    answer_units=[
                        {
                            "text": "- **可先整理情況。** 保留必要說明。",
                            "source_ids": ["unknown/source"],
                        }
                    ]
                )
            ),
        ]
    )
    with pytest.raises(AgentContractError, match="Invalid answer unit or citation"):
        await run(agent)
