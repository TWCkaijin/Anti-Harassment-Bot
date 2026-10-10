"""Gemini wire-schema compatibility without weakening application validation."""

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from client.tests.test_agent import FakeMessage, FakeResponse, fake_runtime_config
from client.tests.test_agent_streaming import FakeStream, chunk
from client.tests.test_agent_streaming import response as legacy_answer
from client.tests.test_guided_chat import agent_for as guided_agent
from client.tests.test_guided_chat import answer, model_answer
from client.tests.test_guided_v3 import v3_answer
from client.tests.test_guided_v4 import (
    agent_for,
    clarification_plan,
    final_answer,
    legal_plan,
    response,
    role_question,
    run,
    streamed,
    understanding,
)
from rag_core.agents import guided_chat, openrouter_agent
from rag_core.agents import guided_v4 as v4
from rag_core.agents.openrouter_agent import AgentContractError
from rag_core.agents.provider_schema import provider_response_format
from rag_core.core.agent_prompts import assemble_service_instruction
from rag_core.core.chat_response import OPENROUTER_RESPONSE_FORMAT

GEMINI = "google/gemini-2.5-pro"
OTHER = "test/model"


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch):
    for module in (guided_chat, v4, openrouter_agent):
        monkeypatch.setattr(module, "get_matching_scenario_scripts", lambda *args, **kwargs: ())
        monkeypatch.setattr(module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(v4, "get_scenario_scripts", lambda **kwargs: ())


def keywords(value, name):
    if isinstance(value, dict):
        return ([value[name]] if name in value else []) + [
            found for child in value.values() for found in keywords(child, name)
        ]
    if isinstance(value, list):
        return [found for child in value for found in keywords(child, name)]
    return []


def assert_gemini_format(response_format):
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    for name in ("minLength", "maxLength", "minItems", "pattern"):
        assert not keywords(schema, name)
    assert all(value == 0 for value in keywords(schema, "maxItems"))


@pytest.mark.parametrize(
    "original",
    [
        OPENROUTER_RESPONSE_FORMAT,
        guided_chat.GUIDED_RESPONSE_FORMAT,
        guided_chat.GUIDED_V3_RESPONSE_FORMAT,
        v4.V4_PLAN_FORMAT,
        v4.V4_FINAL_FORMAT,
        v4.V4_PREDICTION_PLAN_FORMAT,
        v4.V4_PREDICTION_FINAL_FORMAT,
        v4._final_response_format([]),
        v4._final_response_format([{"doc_id": "fixture/source"}]),
    ],
)
def test_gemini_copy_preserves_contract_structure_and_original(original):
    before = deepcopy(original)
    adapted = provider_response_format(GEMINI, original)
    assert adapted is not original
    assert_gemini_format(adapted)
    for keyword in ("enum", "const", "required", "additionalProperties", "$ref"):
        assert keywords(adapted, keyword) == keywords(original, keyword)
    assert original == before
    adapted["json_schema"]["schema"]["description"] = "request-local mutation"
    assert original == before
    assert provider_response_format(OTHER, original) is original


def test_schema_keyword_names_inside_properties_and_literal_values_are_preserved():
    original = {
        "type": "json_schema",
        "json_schema": {
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "pattern": "^x$", "maxLength": 3},
                    "maxItems": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
                    "literal": {"const": {"pattern": "kept", "maxItems": 7}},
                    "empty": {"type": "array", "items": {"type": "string"}, "maxItems": 0},
                },
                "$defs": {"minLength": {"type": "string", "minLength": 1}},
                "required": ["pattern", "maxItems", "literal", "empty"],
                "additionalProperties": False,
            },
        },
    }
    adapted = provider_response_format(GEMINI, original)["json_schema"]["schema"]
    assert set(adapted["properties"]) == {"pattern", "maxItems", "literal", "empty"}
    assert adapted["properties"]["pattern"] == {"type": "string"}
    assert adapted["properties"]["literal"]["const"] == {"pattern": "kept", "maxItems": 7}
    assert adapted["properties"]["empty"]["maxItems"] == 0
    assert adapted["$defs"] == {"minLength": {"type": "string"}}


@pytest.mark.parametrize("model", [OTHER, "google/other-model", "gemini-2.5-pro"])
def test_non_gemini_formats_are_unchanged(model):
    assert provider_response_format(model, v4.V4_PLAN_FORMAT) is v4.V4_PLAN_FORMAT


@pytest.mark.parametrize("streaming", [False, True])
async def test_legacy_actual_request_uses_gemini_copy(streaming):
    raw = json.dumps(legacy_answer(), ensure_ascii=False)
    result = (
        FakeStream([chunk(raw), chunk(finish="stop")])
        if streaming
        else FakeResponse(FakeMessage(raw))
    )
    agent = agent_for([result])

    async def delta(_text):
        pass

    await agent.run(
        "我想先談談感受",
        use_rag=False,
        runtime_config=fake_runtime_config(openrouter_model=GEMINI),
        on_reply_delta=delta if streaming else None,
    )
    call = agent.client.chat.completions.calls[0]
    assert_gemini_format(call["response_format"])
    assert call["response_format"] == provider_response_format(GEMINI, OPENROUTER_RESPONSE_FORMAT)


@pytest.mark.parametrize("version", [2, 3])
@pytest.mark.parametrize("model", [GEMINI, OTHER])
async def test_v2_v3_actual_requests_apply_model_specific_schema(version, model):
    value = answer() if version == 2 else v3_answer(understanding=understanding())
    if version == 3:
        value["understanding"].pop("immediate_safety")
    agent = guided_agent([model_answer(value)])
    config = fake_runtime_config(openrouter_model=model)
    await agent.run(
        "我想先談談感受",
        use_rag=False,
        contract_version=version,
        case_context={"schema_version": version - 1},
        runtime_config=config,
    )
    call = agent.client.chat.completions.calls[0]
    original = (
        guided_chat.GUIDED_RESPONSE_FORMAT
        if version == 2
        else guided_chat.GUIDED_V3_RESPONSE_FORMAT
    )
    assert call["response_format"] == provider_response_format(model, original)
    if model == GEMINI:
        assert_gemini_format(call["response_format"])
    assert call["max_tokens"] == config.max_tokens


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("model", [GEMINI, OTHER])
async def test_v4_both_stages_preserve_dynamic_sources_and_route_union(model, streaming):
    values = [legal_plan(), final_answer()]
    agent = agent_for([streamed(value) if streaming else response(value) for value in values])

    async def delta(_text):
        pass

    await run(
        agent,
        runtime_config=fake_runtime_config(openrouter_model=model),
        on_reply_delta=delta if streaming else None,
    )
    calls = agent.client.chat.completions.calls
    assert len(calls) == 2
    first = calls[0]["response_format"]["json_schema"]["schema"]
    assert (
        first["properties"]["plan"]["anyOf"]
        == v4.V4_PLAN_FORMAT["json_schema"]["schema"]["properties"]["plan"]["anyOf"]
    )
    assert first["$defs"]["UncitedAnswerUnit"]["properties"]["source_ids"]["maxItems"] == 0
    assert first["$defs"]["SynthesisPlan"]["properties"]["answer_units"]["maxItems"] == 0
    final_ids = calls[1]["response_format"]["json_schema"]["schema"]["$defs"]["AnswerUnit"][
        "properties"
    ]["source_ids"]
    assert final_ids["items"]["enum"] == ["laws/law12"]
    for call in calls:
        if model == GEMINI:
            assert_gemini_format(call["response_format"])
        else:
            assert keywords(call["response_format"], "maxLength")
        assert call["model"] == model and call["max_tokens"] == 1200


async def test_gemini_final_request_without_retrieved_sources_forbids_citations():
    agent = agent_for([response({**legal_plan(), "retrieval": None}), response(final_answer())])
    await run(agent, use_rag=False, runtime_config=fake_runtime_config(openrouter_model=GEMINI))
    schema = agent.client.chat.completions.calls[-1]["response_format"]["json_schema"]["schema"]
    ids = schema["$defs"]["AnswerUnit"]["properties"]["source_ids"]
    assert ids["maxItems"] == 0 and "enum" not in ids["items"]


async def test_prediction_both_actual_requests_apply_gemini_schema():
    values = [
        {"understanding": understanding(intent="legal_direction"), "retrieval": None},
        {"predicted_laws": ["性工法"], "decision_status": "predicted", "decision_reason": None},
    ]
    agent = agent_for([FakeResponse(FakeMessage(json.dumps(value))) for value in values])
    await run(
        agent,
        use_rag=False,
        output_contract="applicable_laws_v1",
        runtime_config=fake_runtime_config(openrouter_model=GEMINI),
    )
    calls = agent.client.chat.completions.calls
    assert len(calls) == 2
    for call, original in zip(
        calls, (v4.V4_PREDICTION_PLAN_FORMAT, v4.V4_PREDICTION_FINAL_FORMAT), strict=True
    ):
        assert call["stream"] is False
        assert call["response_format"] == provider_response_format(GEMINI, original)
        assert_gemini_format(call["response_format"])


@pytest.mark.parametrize("streaming", [False, True])
async def test_gemini_disabled_clarification_stays_removed_and_rejected(streaming):
    value = clarification_plan()
    agent = agent_for([streamed(value) if streaming else response(value)])
    published = []

    async def delta(text):
        published.append(text)

    with pytest.raises(AgentContractError):
        await run(
            agent,
            runtime_config=fake_runtime_config(
                openrouter_model=GEMINI, pipeline={"auto_clarify": False}
            ),
            on_reply_delta=delta if streaming else None,
        )
    schema = agent.client.chat.completions.calls[0]["response_format"]["json_schema"]["schema"]
    assert {branch["$ref"] for branch in schema["properties"]["plan"]["anyOf"]} == {
        "#/$defs/SynthesisPlan",
        "#/$defs/DirectPlan",
    }
    assert not published


@pytest.mark.parametrize(
    "invalid", ["text", "options", "source_length", "source_count", "invented_source"]
)
async def test_gemini_still_rejects_invalid_final_answers_locally(invalid):
    value = final_answer()
    if invalid == "text":
        value["answer_units"][0]["text"] = "字" * 2001
    elif invalid == "options":
        value["question"] = role_question("你")
        value["question"]["options"].append({"label": "第四", "value": "第四"})
    elif invalid == "source_length":
        value["answer_units"][0]["source_ids"] = ["x" * 501]
    elif invalid == "source_count":
        value["answer_units"][0]["source_ids"] = ["laws/law12"] * 13
    else:
        value["answer_units"][0]["source_ids"] = ["laws/not-retrieved"]
    agent = agent_for([response(legal_plan()), response(value)])
    with pytest.raises(AgentContractError):
        await run(agent, runtime_config=fake_runtime_config(openrouter_model=GEMINI))
    assert len(agent.client.chat.completions.calls) == 2


def test_local_empty_source_and_question_bounds_remain_strict():
    provider_response_format(GEMINI, v4.V4_PLAN_FORMAT)
    with pytest.raises(ValidationError):
        v4.UncitedAnswerUnit.model_validate({"text": "合成", "source_ids": ["laws/law12"]})
    with pytest.raises(ValidationError):
        v4.QuestionV4.model_validate({**role_question("字" * 501)})


@pytest.mark.parametrize(
    "fingerprint,formats",
    [
        (v4.chat_test_contract_sha256, ("V4_PLAN_FORMAT", "V4_FINAL_FORMAT")),
        (
            v4.applicable_laws_contract_sha256,
            ("V4_PREDICTION_PLAN_FORMAT", "V4_PREDICTION_FINAL_FORMAT"),
        ),
    ],
)
def test_fingerprints_track_provider_format_and_local_constraints(
    fingerprint, formats, monkeypatch
):
    config = fake_runtime_config(openrouter_model=GEMINI)
    before = fingerprint(config)
    original = v4.provider_response_format

    def changed(model, response_format):
        result = deepcopy(original(model, response_format))
        result["json_schema"]["schema"]["description"] = "Changed provider compilation"
        return result

    monkeypatch.setattr(v4, "provider_response_format", changed)
    assert fingerprint(config) != before
    monkeypatch.setattr(v4, "provider_response_format", original)
    local = deepcopy(getattr(v4, formats[0]))
    local["json_schema"]["schema"]["maxLength"] = 456
    # This local bound is removed on the wire, but must still change the hash.
    assert original(GEMINI, local) == original(GEMINI, getattr(v4, formats[0]))
    monkeypatch.setattr(v4, formats[0], local)
    assert fingerprint(config) != before


@pytest.mark.parametrize("kind", ["chat", "prediction"])
def test_non_gemini_contract_fingerprint_preserves_previous_format(kind):
    config = fake_runtime_config(openrouter_model=OTHER)
    chat = kind == "chat"
    contract = {
        "service_instruction": assemble_service_instruction(config.agent_prompt_sections),
        "understanding_instruction": v4.V4_INSTRUCTION if chat else v4.V4_PREDICTION_INSTRUCTION,
        "final_instruction": v4.V4_FINAL_STAGE_INSTRUCTION
        if chat
        else v4.V4_PREDICTION_FINAL_INSTRUCTION,
        "understanding_schema": (v4.V4_PLAN_FORMAT if chat else v4.V4_PREDICTION_PLAN_FORMAT)[
            "json_schema"
        ]["schema"],
        "final_schema": (v4.V4_FINAL_FORMAT if chat else v4.V4_PREDICTION_FINAL_FORMAT)[
            "json_schema"
        ]["schema"],
        "scenario_catalog_sha256": v4.applicable_laws_scenario_catalog_sha256(config),
    }
    previous = hashlib.sha256(
        json.dumps(
            contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    fingerprint = v4.chat_test_contract_sha256 if chat else v4.applicable_laws_contract_sha256
    assert fingerprint(config) == previous
