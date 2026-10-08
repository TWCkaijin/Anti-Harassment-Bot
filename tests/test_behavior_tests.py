"""Behavior-test contract pinning; synthetic providers only."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import Mock

import pytest

import backend.app.agents.guided_v4 as v4
from backend.app.agents.openrouter_agent import OpenRouterAgent
from backend.app.api import admin, chat
from backend.app.core.agent_prompts import get_default_prompt_sections
from backend.app.core.scenario_scripts import ScenarioScript
from backend.app.main import app
from tests.test_agent import FakeClient, FakeCompletions, fake_runtime_config
from tests.test_guided_v4 import Rag, plan, response


@pytest.fixture
def setup(monkeypatch):
    config = fake_runtime_config()
    monkeypatch.setattr(admin.settings, "admin_api_key", "behavior-test-token")
    monkeypatch.setattr(admin, "get_runtime_config", lambda **kwargs: config)
    monkeypatch.setattr(admin, "get_enabled_scenario_catalog", lambda **kwargs: ())
    monkeypatch.setattr(v4, "get_scenario_scripts", lambda **kwargs: ())
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)
    agent = object.__new__(OpenRouterAgent)
    agent.client = FakeClient(FakeCompletions([response(plan())]))
    agent.rag = Rag()
    monkeypatch.setattr(chat, "get_agent", lambda: agent)
    return config, agent


def request_body(fingerprint):
    return {
        "request": {
            "message": "合成訊息",
            "history": [],
            "contract_version": 4,
            "case_context": {"schema_version": 3},
        },
        "expected_chat_test_contract_sha256": fingerprint,
    }


def test_admin_exposes_an_independent_chat_fingerprint(setup):
    config, _ = setup
    result = app.test_client().get(
        "/api/v1/admin/config", headers={"Authorization": "Bearer behavior-test-token"}
    )
    assert result.json["chat_test_contract_sha256"] == v4.chat_test_contract_sha256(config)
    assert (
        result.json["chat_test_contract_sha256"] != result.json["applicable_laws_contract_sha256"]
    )
    effective = {**get_default_prompt_sections(), **config.agent_prompt_sections}
    assert v4.chat_test_contract_sha256(
        replace(config, agent_prompt_sections=effective)
    ) == v4.chat_test_contract_sha256(config)


@pytest.mark.parametrize(
    "attribute",
    ["V4_INSTRUCTION", "V4_FINAL_STAGE_INSTRUCTION", "V4_PLAN_FORMAT", "V4_FINAL_FORMAT"],
)
def test_changed_schema_or_instruction_changes_chat_fingerprint(attribute, monkeypatch):
    config = fake_runtime_config()
    before = v4.chat_test_contract_sha256(config)
    value = deepcopy(getattr(v4, attribute))
    if isinstance(value, str):
        value += "合成變更"
    else:
        value["json_schema"]["schema"]["description"] = "Synthetic change"
    monkeypatch.setattr(v4, attribute, value)
    assert before != v4.chat_test_contract_sha256(config)


def test_mismatch_fails_before_any_model_or_rag_call(setup):
    _, agent = setup
    result = app.test_client().post(
        "/api/v1/admin/chat-test",
        headers={"Authorization": "Bearer behavior-test-token"},
        json=request_body("0" * 64),
    )
    assert result.status_code == 409
    assert result.json["code"] == "chat_test_contract_mismatch"
    assert result.headers["Cache-Control"] == "no-store"
    assert agent.client.chat.completions.calls == []
    assert agent.rag.calls == []


def test_matching_contract_runs_with_diagnostics_and_frozen_catalog(setup, monkeypatch):
    config, agent = setup
    match = Mock(return_value=())
    monkeypatch.setattr(v4, "get_matching_scenario_scripts", match)
    result = app.test_client().post(
        "/api/v1/admin/chat-test",
        headers={"Authorization": "Bearer behavior-test-token"},
        json=request_body(v4.chat_test_contract_sha256(config)),
    )
    assert result.status_code == 200
    assert len(agent.client.chat.completions.calls) == 1
    assert match.call_args.kwargs["catalog"] == ()
    assert result.json["diagnostics"]["chat_test_contract_sha256"] == v4.chat_test_contract_sha256(
        config
    )
    assert result.json["diagnostics"]["corpus_version"] == "unknown"
    assert result.json["diagnostics"]["prompt_overlap"] == []


@pytest.mark.parametrize(
    "change",
    [
        {"output_contract": "applicable_laws_v1"},
        {"request": {"message": "test", "contract_version": 3}},
    ],
)
def test_chat_fingerprint_cannot_be_used_for_another_contract(setup, change):
    config, agent = setup
    result = app.test_client().post(
        "/api/v1/admin/chat-test",
        headers={"Authorization": "Bearer behavior-test-token"},
        json={**request_body(v4.chat_test_contract_sha256(config)), **change},
    )
    assert result.status_code == 422
    assert not agent.client.chat.completions.calls


def test_behavior_contract_still_requires_admin_before_loading_any_settings(setup, monkeypatch):
    load = Mock(side_effect=AssertionError("must authenticate first"))
    monkeypatch.setattr(admin, "get_runtime_config", load)
    result = app.test_client().post("/api/v1/admin/chat-test", json=request_body("0" * 64))
    assert result.status_code == 401
    load.assert_not_called()


def test_catalog_changes_change_fingerprint_but_disabled_skills_do_not():
    config = fake_runtime_config()
    script = ScenarioScript(
        script_id="synthetic",
        name="test",
        enabled=True,
        priority=1,
        trigger_keywords=("test",),
        instruction="合成指令",
        actions=(),
    )
    assert v4.chat_test_contract_sha256(config, (script,)) != v4.chat_test_contract_sha256(config)
    disabled = replace(config, pipeline={**config.pipeline, "enable_skills": False})
    assert v4.chat_test_contract_sha256(disabled, (script,)) == v4.chat_test_contract_sha256(
        disabled
    )


def test_overlap_evidence_contains_only_text_already_visible_in_reply():
    secret = "合成系統提示" * 30
    assert v4._visible_prompt_overlap("不會提供系統提示", [secret]) == []
    fragment = secret[:80]
    matches = v4._visible_prompt_overlap(fragment + "測試回覆", [secret + "不可加入證據的尾端"])
    assert matches == [{"kind": "prompt_overlap", "text": fragment, "matched_characters": 80}]
