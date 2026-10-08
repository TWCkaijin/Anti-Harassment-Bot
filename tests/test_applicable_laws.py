"""Admin-only law predictions, with synthetic provider/RAG responses and no network."""

import json
import re
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

import backend.app.agents.guided_v4 as v4
import backend.app.agents.openrouter_agent as agent_module
from backend.app.agents.openrouter_agent import (
    AgentContractError,
    AgentResult,
    ApplicableLawPredictionResult,
    OpenRouterAgent,
)
from backend.app.api import admin, chat
from backend.app.core.agent_errors import ApplicableLawsContractMismatchError, ModelOutputLimitError
from backend.app.core.runtime_config import WRITABLE_FIELDS
from backend.app.core.scenario_scripts import ScenarioScript
from backend.app.main import app
from backend.app.rag.base import RAGUnavailableError
from tests.test_agent import FakeClient, FakeCompletions, fake_runtime_config
from tests.test_guided_v4 import Rag, understanding


@pytest.fixture(autouse=True)
def no_external_skills(monkeypatch):
    monkeypatch.setattr(v4, "get_matching_scenario_scripts", lambda *args, **kwargs: ())
    monkeypatch.setattr(v4, "get_scenario_scripts", lambda **kwargs: ())
    monkeypatch.setattr(admin, "get_enabled_scenario_catalog", lambda **kwargs: ())


def prediction(**changes):
    return {
        "predicted_laws": ["性騷法", "性工法"],
        "decision_status": "predicted",
        "decision_reason": None
        if changes.get("decision_status", "predicted") == "predicted"
        else "未提供當事人身分與關係。",
        **changes,
    }


def plan(*, enabled=True, **changes):
    return {
        "understanding": understanding(intent="legal_direction"),
        "retrieval": {"query": "職場 身分 法律適用", "data_types": ["law"]} if enabled else None,
        **changes,
    }


def response(value, *, finish="stop", **message_fields):
    content = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason=finish,
                message=SimpleNamespace(content=content, **message_fields),
            )
        ]
    )


def agent_for(responses, rag=None):
    agent = object.__new__(OpenRouterAgent)
    agent.client = FakeClient(FakeCompletions(responses))
    agent.rag = rag or Rag()
    return agent


async def run(agent, **changes):
    return await agent.run(
        **{
            "user_message": "合成情境：我是受僱者，另一方是主管。",
            "case_context": {"schema_version": 3},
            "contract_version": 4,
            "runtime_config": fake_runtime_config(),
            "output_contract": "applicable_laws_v1",
            **changes,
        }
    )


@pytest.mark.parametrize("laws", [["性騷法"], ["性工法", "性平法"], ["性平法", "性騷法", "性工法"]])
def test_prediction_preserves_model_order(laws):
    value = v4.ApplicableLawPrediction.model_validate(prediction(predicted_laws=laws))
    assert value.predicted_laws == laws


@pytest.mark.parametrize("status", ["insufficient_facts", "no_target_law"])
def test_valid_empty_prediction(status):
    value = v4.ApplicableLawPrediction.model_validate(
        prediction(predicted_laws=[], decision_status=status)
    )
    assert value.model_dump() == prediction(predicted_laws=[], decision_status=status)


def test_contract_fingerprint_is_stable_for_effective_service_defaults():
    config = fake_runtime_config()
    value = v4.applicable_laws_contract_sha256(config)
    assert re.fullmatch("[0-9a-f]{64}", value)
    assert value == v4.applicable_laws_contract_sha256(config)
    assert value == v4.applicable_laws_contract_sha256(replace(config, temperature=0.7))
    from backend.app.core.agent_prompts import get_default_prompt_sections

    defaults = get_default_prompt_sections()
    defaults.update(config.agent_prompt_sections)
    assert value == v4.applicable_laws_contract_sha256(
        replace(config, agent_prompt_sections=defaults)
    )
    assert value != v4.applicable_laws_contract_sha256(
        replace(config, agent_prompt_sections={"language": "合成的新指令"})
    )


@pytest.mark.parametrize(
    "attribute",
    [
        "V4_PREDICTION_INSTRUCTION",
        "V4_PREDICTION_FINAL_INSTRUCTION",
        "V4_PREDICTION_PLAN_FORMAT",
        "V4_PREDICTION_FINAL_FORMAT",
    ],
)
def test_contract_fingerprint_detects_fixed_prompt_and_schema_changes(attribute, monkeypatch):
    config = fake_runtime_config()
    original = v4.applicable_laws_contract_sha256(config)
    value = deepcopy(getattr(v4, attribute))
    if isinstance(value, str):
        value += "合成變更"
    else:
        value["json_schema"]["schema"]["description"] = "Synthetic schema change"
    monkeypatch.setattr(v4, attribute, value)
    assert original != v4.applicable_laws_contract_sha256(config)


@pytest.mark.parametrize(
    "value",
    [
        prediction(predicted_laws=[]),
        prediction(predicted_laws=["性工法", "性工法"]),
        prediction(predicted_laws=["性別平等工作法"]),
        prediction(predicted_laws=["其他法"]),
        prediction(predicted_laws=["性工法"] * 4),
        prediction(decision_status="insufficient_facts"),
        prediction(decision_status="no_target_law"),
        prediction(decision_status="unknown"),
        prediction(reply="extra chat text"),
        {"predicted_laws": ["性工法"]},
        {"decision_status": "no_target_law"},
    ],
)
def test_prediction_rejects_invalid_contract(value):
    with pytest.raises(ValidationError):
        v4.ApplicableLawPrediction.model_validate(value)


@pytest.mark.parametrize("sufficient", [False, True])
async def test_prediction_always_uses_two_calls_without_chat_or_safety_outputs(sufficient):
    first = plan(
        understanding=understanding(
            intent="legal_direction", sufficient=sufficient, immediate_safety=True
        )
    )
    agent = agent_for([response(first), response(prediction())])
    diagnostics = {}
    config = replace(
        fake_runtime_config(),
        reasoning_effort="low",
        agent_prompt_sections={"language": "TEST LANGUAGE", "output_format": "LEGACY OUTPUT"},
    )
    result = await run(agent, diagnostics=diagnostics, runtime_config=config)
    assert isinstance(result, ApplicableLawPredictionResult)
    assert not isinstance(result, AgentResult)
    assert result.prediction.model_dump() == prediction()
    calls = agent.client.chat.completions.calls
    assert len(calls) == 2 and len(agent.rag.calls) == 1
    for call in calls:
        assert call["stream"] is False
        assert call["model"] == config.openrouter_model
        assert call["temperature"] == config.temperature
        assert call["top_p"] == config.top_p
        assert call["max_tokens"] == config.max_tokens
        assert call["extra_body"]["reasoning"] == {"effort": "low", "exclude": True}
        assert "TEST LANGUAGE" in call["messages"][0]["content"]
        assert "LEGACY OUTPUT" not in json.dumps(call["messages"], ensure_ascii=False)
    assert calls[0]["response_format"] == v4.V4_PREDICTION_PLAN_FORMAT
    assert calls[1]["response_format"] == v4.V4_PREDICTION_FINAL_FORMAT
    handoff = json.loads(calls[1]["messages"][-1]["content"])
    assert handoff["validated_understanding"]["sufficient"] is sufficient
    assert handoff["retrieval_status"] == "ok"
    assert "safety_preface_already_shown" not in handoff
    assert diagnostics["execution"]["model_calls"] == 2
    assert diagnostics["retrieval_status"] == "ok"
    assert diagnostics["applicable_laws_contract_sha256"] == v4.applicable_laws_contract_sha256(
        config
    )
    assert (
        not {"final_answer", "summary_update", "clarification_decision", "reasoning"}
        & diagnostics.keys()
    )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("regenerate", [False, True])
async def test_prediction_preserves_trusted_skill_selection_and_edited_summary(
    enabled, regenerate, monkeypatch
):
    selections = []

    def match(text, *, history, catalog):
        selections.append((text, history))
        return [SimpleNamespace(script_id="fixture-script")]

    monkeypatch.setattr(v4, "get_matching_scenario_scripts", match)
    monkeypatch.setattr(
        v4, "format_scenario_instruction", lambda scripts: "TRUSTED SCENARIO GUIDANCE"
    )
    agent = agent_for([response(plan()), response(prediction())])
    diagnostics = {}
    old_history = [{"role": "user", "content": "OLD HISTORY FACT"}]
    config = replace(fake_runtime_config(), pipeline={"enable_skills": enabled})
    await run(
        agent,
        runtime_config=config,
        diagnostics=diagnostics,
        user_message="ORIGINAL FACT",
        history=old_history,
        case_context={"schema_version": 3, "summary": "EDITED SUMMARY", "summary_origin": "user"},
        regenerate_from_summary=regenerate,
    )
    assert selections == (
        [("EDITED SUMMARY", None) if regenerate else ("ORIGINAL FACT", old_history)]
        if enabled
        else []
    )
    messages = agent.client.chat.completions.calls[0]["messages"]
    assert ("TRUSTED SCENARIO GUIDANCE" in json.dumps(messages)) is enabled
    if enabled:
        assert messages[1]["content"] == "TRUSTED SCENARIO GUIDANCE"
        assert v4.V4_PREDICTION_INSTRUCTION in messages[2]["content"]
    if regenerate:
        assert "OLD HISTORY FACT" not in json.dumps(messages)
    assert diagnostics["matched_scenario_scripts"] == {
        "count": int(enabled),
        "ids": ["fixture-script"] if enabled else [],
    }


def catalog_script(**changes):
    return ScenarioScript(
        **{
            "script_id": "fixture-script",
            "name": "合成腳本",
            "enabled": True,
            "priority": 1,
            "trigger_keywords": ("受僱者",),
            "instruction": "合成法律指示",
            "actions": (),
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changed",
    [
        catalog_script(instruction="CHANGED TEMPLATE"),
        catalog_script(trigger_keywords=("新觸發詞",)),
        catalog_script(priority=7),
    ],
)
def test_contract_fingerprint_covers_enabled_catalog_changes(changed):
    config = fake_runtime_config()
    catalog = (catalog_script(),)
    original = v4.applicable_laws_contract_sha256(config, catalog)
    assert original != v4.applicable_laws_contract_sha256(config, (changed,))
    assert original != v4.applicable_laws_contract_sha256(config, ())
    disabled = replace(config, pipeline={"enable_skills": False})
    assert v4.applicable_laws_contract_sha256(
        disabled, catalog
    ) == v4.applicable_laws_contract_sha256(disabled, (changed,))
    assert v4.applicable_laws_scenario_catalog_sha256(disabled, catalog) is None


async def test_prediction_hashes_and_matches_exactly_one_catalog_snapshot(monkeypatch):
    import backend.app.core.scenario_scripts as scripts_module
    from backend.app.core.scenario_scripts import get_matching_scenario_scripts

    catalog = (catalog_script(),)
    reads = []

    def load(**kwargs):
        reads.append(kwargs)
        return catalog

    monkeypatch.setattr(v4, "get_scenario_scripts", load)
    monkeypatch.setattr(
        scripts_module,
        "get_scenario_scripts",
        lambda *a, **k: pytest.fail("Matching must reuse captured catalog"),
    )
    monkeypatch.setattr(v4, "get_matching_scenario_scripts", get_matching_scenario_scripts)
    agent = agent_for([response(plan()), response(prediction())])
    diagnostics = {}
    expected = v4.applicable_laws_contract_sha256(fake_runtime_config(), catalog)
    await run(agent, diagnostics=diagnostics, expected_applicable_laws_contract_sha256=expected)
    assert reads == [{"force_refresh": True}]
    assert diagnostics["applicable_laws_contract_sha256"] == expected
    assert diagnostics[
        "applicable_laws_scenario_catalog_sha256"
    ] == v4.applicable_laws_scenario_catalog_sha256(fake_runtime_config(), catalog)
    assert diagnostics["matched_scenario_scripts"] == {"count": 1, "ids": ["fixture-script"]}
    assert "合成法律指示" in json.dumps(
        agent.client.chat.completions.calls[0]["messages"], ensure_ascii=False
    )


def test_contract_fingerprint_tracks_catalog_order():
    config = fake_runtime_config()
    first = catalog_script()
    second = catalog_script(script_id="second-script", name="第二腳本", instruction="第二份指示")
    assert v4.applicable_laws_contract_sha256(
        config, (first, second)
    ) != v4.applicable_laws_contract_sha256(config, (second, first))


async def test_prepared_catalog_change_fails_closed_before_any_provider_call(monkeypatch):
    original = (catalog_script(),)
    changed = (catalog_script(instruction="CHANGED PROMPT"),)
    monkeypatch.setattr(v4, "get_scenario_scripts", lambda **kwargs: changed)
    agent = agent_for([])
    diagnostics = {}
    expected = v4.applicable_laws_contract_sha256(fake_runtime_config(), original)
    with pytest.raises(ApplicableLawsContractMismatchError):
        await run(agent, diagnostics=diagnostics, expected_applicable_laws_contract_sha256=expected)
    assert not agent.client.chat.completions.calls and not agent.rag.calls
    assert diagnostics["execution"]["model_calls"] == 0
    assert diagnostics["applicable_laws_contract_sha256"] != expected


async def test_prediction_empty_retrieval_still_reaches_final_decision():
    class EmptyRag(Rag):
        async def retrieve(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return []

    expected = prediction(predicted_laws=[], decision_status="insufficient_facts")
    agent = agent_for([response(plan()), response(expected)], EmptyRag())
    diagnostics = {}
    result = await run(agent, diagnostics=diagnostics)
    assert result.prediction.model_dump() == expected
    assert diagnostics["retrieval_status"] == "empty"
    assert diagnostics["retrieved_count"] == 0
    assert diagnostics["execution"]["model_calls"] == 2


async def test_retrieval_failure_is_operational_not_an_empty_prediction():
    agent = agent_for(
        [
            response(plan()),
            response(prediction(predicted_laws=[], decision_status="no_target_law")),
        ],
        Rag(fail=True),
    )
    diagnostics = {}
    with pytest.raises(RAGUnavailableError, match="^Applicable-law retrieval did not complete$"):
        await run(agent, diagnostics=diagnostics)
    assert len(agent.client.chat.completions.calls) == 1
    assert diagnostics["retrieval_status"] == "failed"
    assert diagnostics["retrieval"]["failure_type"] == "RAGUnavailableError"
    assert diagnostics["execution"]["retrieval_ms"] >= 0


async def test_disabled_retrieval_preserves_two_stage_flow():
    agent = agent_for([response(plan(enabled=False)), response(prediction())])
    diagnostics = {}
    await run(agent, use_rag=False, diagnostics=diagnostics)
    assert not agent.rag.calls
    assert len(agent.client.chat.completions.calls) == 2
    assert diagnostics["retrieval_status"] == "disabled"


@pytest.mark.parametrize("use_rag", [False, True])
async def test_prediction_plan_must_respect_retrieval_policy(use_rag):
    agent = agent_for([response(plan(enabled=not use_rag))])
    with pytest.raises(AgentContractError):
        await run(agent, use_rag=use_rag)
    assert len(agent.client.chat.completions.calls) == 1 and not agent.rag.calls


@pytest.mark.parametrize("evidence", [["未曾說過的私密內容"], None, [7]])
async def test_prediction_shares_understanding_evidence_validation(evidence):
    agent = agent_for(
        [
            response(
                plan(understanding=understanding(intent="legal_direction", evidence=evidence))
            ),
            response(prediction()),
        ]
    )
    diagnostics = {}
    await run(agent, diagnostics=diagnostics)
    handoff = json.loads(agent.client.chat.completions.calls[1]["messages"][-1]["content"])
    assert handoff["validated_understanding"]["evidence"] == []
    assert diagnostics["metadata_validation"][0]["field"] == "understanding.evidence"
    assert "私密內容" not in json.dumps(diagnostics["metadata_validation"], ensure_ascii=False)


async def test_prediction_shares_query_masking_and_legal_retrieval_types(monkeypatch):
    import backend.app.core.anonymizer as anonymizer

    monkeypatch.setattr(
        anonymizer,
        "anonymize",
        lambda value: SimpleNamespace(
            anonymized=value.replace("fixture@example.com", "[EMAIL]"), detected_types=["email"]
        ),
    )
    first = plan(
        understanding=understanding(intent="support"),
        retrieval={"query": "fixture@example.com 身分關係", "data_types": ["remedy"]},
    )
    agent = agent_for([response(first), response(prediction())])
    diagnostics = {}
    await run(
        agent,
        diagnostics=diagnostics,
        runtime_config=replace(fake_runtime_config(), enable_anonymization=True),
    )
    query, kwargs = agent.rag.calls[0]
    assert query == "[EMAIL] 身分關係"
    assert kwargs["selected_data_types"] == ["law", "remedy"]
    assert kwargs["preserve_data_types"] is True
    assert diagnostics["pii"]["retrieval_query"]["after"] == query


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"predicted_laws":["性工法"],"decision_status":"predicted"}\n```',
        '{"predicted_laws":["性工法"],"predicted_laws":[],"decision_status":"no_target_law"}',
        '{"predicted_laws":[],"decision_status":"no_target_law"} trailing text',
        '{"predicted_laws":[],"decision_status":NaN}',
        '[{"predicted_laws":[],"decision_status":"no_target_law"}]',
        '{"predicted_laws":[],"decision_status":"no_target_law","reason":"extra"}',
    ],
)
async def test_nonstream_parser_never_repairs_invalid_output(raw):
    agent = agent_for([response(plan()), response(raw)])
    with pytest.raises(AgentContractError):
        await run(agent)
    assert len(agent.client.chat.completions.calls) == 2


@pytest.mark.parametrize(
    "finish,error_type",
    [
        ("length", ModelOutputLimitError),
        (None, AgentContractError),
        ("content_filter", AgentContractError),
    ],
)
async def test_nonstream_parser_requires_completed_provider_response(finish, error_type):
    agent = agent_for([response(plan(), finish=finish)])
    with pytest.raises(error_type):
        await run(agent)
    assert len(agent.client.chat.completions.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"contract_version": 3},
        {"on_reply_delta": Mock()},
        {"on_guidance": Mock()},
        {"on_reasoning": Mock()},
    ],
)
async def test_internal_prediction_rejects_legacy_or_stream_callbacks(changes):
    agent = agent_for([])
    with pytest.raises(AgentContractError, match="non-streaming v4"):
        await run(agent, **changes)
    assert not agent.client.chat.completions.calls


async def test_prediction_sdk_transport_disables_paid_generation_retries(monkeypatch):
    constructors = []
    responses = [response(plan()), response(prediction())]

    class FakeSDK(FakeClient):
        def __init__(self, **kwargs):
            constructors.append(kwargs)
            super().__init__(FakeCompletions(responses))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(agent_module, "AsyncOpenAI", FakeSDK)
    agent = object.__new__(OpenRouterAgent)
    agent.client = FakeSDK()
    agent.rag = Rag()
    await run(agent)
    assert constructors[1]["max_retries"] == 0


@pytest.fixture
def isolated_admin(monkeypatch):
    config = fake_runtime_config()
    monkeypatch.setattr(admin.settings, "admin_api_key", "offline-key")
    monkeypatch.setattr(admin, "get_runtime_config", lambda **kwargs: config)
    monkeypatch.setattr(chat, "get_runtime_config", lambda: config)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)
    monkeypatch.setattr(chat, "get_agent", lambda: pytest.fail("Unexpected agent invocation"))
    return config


def admin_request(**changes):
    return {
        "output_contract": "applicable_laws_v1",
        "request": {"message": "合成情境", "contract_version": 4},
        **changes,
    }


def test_admin_config_exposes_readonly_contract_fingerprint_without_writes(
    isolated_admin, monkeypatch
):
    write = Mock(side_effect=AssertionError("GET fingerprint must not persist config"))
    monkeypatch.setattr(admin, "update_runtime_config", write)
    response = app.test_client().get(
        "/v1/admin/config", headers={"Authorization": "Bearer offline-key"}
    )
    assert response.status_code == 200
    assert response.json["applicable_laws_contract_sha256"] == v4.applicable_laws_contract_sha256(
        isolated_admin
    )
    assert "applicable_laws_contract_sha256" not in WRITABLE_FIELDS
    assert "applicable_laws_contract_sha256" not in isolated_admin.public_dict()
    result = app.test_client().post(
        "/v1/admin/chat-test",
        headers={"Authorization": "Bearer offline-key"},
        json=admin_request(
            overrides={
                "applicable_laws_contract_sha256": response.json["applicable_laws_contract_sha256"]
            }
        ),
    )
    assert result.status_code == 422
    write.assert_not_called()


def test_admin_prepared_contract_mismatch_returns_fixed_operational_error_without_model(
    isolated_admin, monkeypatch
):
    original = (catalog_script(),)
    monkeypatch.setattr(
        v4, "get_scenario_scripts", lambda **kwargs: (catalog_script(instruction="NEW TEMPLATE"),)
    )
    agent = agent_for([])
    monkeypatch.setattr(chat, "get_agent", lambda: agent)
    expected = v4.applicable_laws_contract_sha256(isolated_admin, original)
    result = app.test_client().post(
        "/v1/admin/chat-test",
        headers={"Authorization": "Bearer offline-key"},
        json=admin_request(expected_applicable_laws_contract_sha256=expected),
    )
    assert result.status_code == 409
    assert (
        result.json["code"] == "applicable_laws_contract_mismatch"
        and result.json["retryable"] is False
    )
    assert "prediction" not in result.json and result.headers["Cache-Control"] == "no-store"
    assert not agent.client.chat.completions.calls and not agent.rag.calls


def test_admin_config_hashes_one_enabled_catalog_snapshot(isolated_admin, monkeypatch):
    catalog = (catalog_script(),)
    loads = []

    def load(**kwargs):
        loads.append(kwargs)
        return catalog

    monkeypatch.setattr(admin, "get_enabled_scenario_catalog", load)
    result = app.test_client().get(
        "/v1/admin/config", headers={"Authorization": "Bearer offline-key"}
    )
    assert result.status_code == 200 and loads == [{"force_refresh": True}]
    assert result.json["applicable_laws_contract_sha256"] == v4.applicable_laws_contract_sha256(
        isolated_admin, catalog
    )
    assert result.json[
        "applicable_laws_scenario_catalog_sha256"
    ] == v4.applicable_laws_scenario_catalog_sha256(isolated_admin, catalog)


@pytest.mark.parametrize("expected", ["", "ABCDEF" * 11, "g" * 64, 13])
def test_admin_expected_contract_hash_is_strict_before_agent(expected, isolated_admin):
    result = app.test_client().post(
        "/v1/admin/chat-test",
        headers={"Authorization": "Bearer offline-key"},
        json=admin_request(expected_applicable_laws_contract_sha256=expected),
    )
    assert result.status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/admin/chat-test", "/v1/admin/chat-test"])
def test_admin_prediction_is_ephemeral_separate_and_no_store(path, isolated_admin, monkeypatch):
    calls = []

    class Agent:
        async def run(self, **kwargs):
            calls.append(kwargs)
            kwargs["diagnostics"].update(retrieval_status="empty", execution={"model_calls": 2})
            return ApplicableLawPredictionResult(
                v4.ApplicableLawPrediction.model_validate(prediction())
            )

    monkeypatch.setattr(chat, "get_agent", Agent)
    monkeypatch.setattr(chat, "_response_payload", lambda *a: pytest.fail("No chat projection"))
    write = Mock(side_effect=AssertionError("must not persist overrides"))
    monkeypatch.setattr(admin, "update_runtime_config", write)
    result = app.test_client().post(
        path,
        headers={"Authorization": "Bearer offline-key"},
        json=admin_request(overrides={"temperature": 0.6}),
    )
    assert result.status_code == 200
    assert set(result.json) == {"output_contract", "prediction", "diagnostics"}
    assert result.json["prediction"] == prediction()
    assert result.headers["Cache-Control"] == "no-store"
    assert calls[0]["output_contract"] == "applicable_laws_v1"
    assert calls[0]["runtime_config"].temperature == 0.6
    assert isolated_admin.temperature != 0.6
    write.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        admin_request(request={"message": "fixture", "contract_version": 3}),
        admin_request(request={"message": "fixture", "contract_version": 4, "stream": True}),
        admin_request(output_contract="unsupported"),
    ],
)
def test_admin_prediction_rejects_invalid_mode_before_agent(body, isolated_admin):
    result = app.test_client().post(
        "/v1/admin/chat-test", headers={"Authorization": "Bearer offline-key"}, json=body
    )
    assert result.status_code == 422
    assert result.headers["Cache-Control"] == "no-store"


def test_prediction_contract_is_admin_only_and_authenticated(isolated_admin, monkeypatch):
    load = Mock(side_effect=AssertionError("Unauthorized mode must not load config"))
    monkeypatch.setattr(admin, "get_runtime_config", load)
    result = app.test_client().post("/v1/admin/chat-test", json=admin_request())
    assert result.status_code == 401 and result.headers["Cache-Control"] == "no-store"
    load.assert_not_called()
    result = app.test_client().post(
        "/v1/chat/",
        json={"message": "fixture", "contract_version": 4, "output_contract": "applicable_laws_v1"},
    )
    assert result.status_code == 422


def test_admin_retrieval_error_remains_operational_with_no_prediction(isolated_admin, monkeypatch):
    class Agent:
        async def run(self, **kwargs):
            raise RAGUnavailableError("private synthetic service detail")

    monkeypatch.setattr(chat, "get_agent", Agent)
    result = app.test_client().post(
        "/v1/admin/chat-test", headers={"Authorization": "Bearer offline-key"}, json=admin_request()
    )
    assert result.status_code == 503
    assert result.json["code"] == "rag_unavailable"
    assert "prediction" not in result.json
    assert "private synthetic" not in json.dumps(result.json)
    assert result.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("reason", [None, "", " \n", "字" * 301, 123])
@pytest.mark.parametrize("status", ["insufficient_facts", "no_target_law"])
def test_abstention_requires_a_short_nonempty_reason(reason, status):
    with pytest.raises(ValidationError):
        v4.ApplicableLawPrediction.model_validate(
            prediction(predicted_laws=[], decision_status=status, decision_reason=reason)
        )


def test_prediction_reason_is_required_and_null_for_success():
    missing = prediction()
    del missing["decision_reason"]
    for value in (missing, prediction(decision_reason="不應有原因")):
        with pytest.raises(ValidationError):
            v4.ApplicableLawPrediction.model_validate(value)
    schema = v4.V4_PREDICTION_FINAL_FORMAT["json_schema"]["schema"]
    assert "decision_reason" in schema["required"]
