"""Tests for deterministic Firestore scenario-script selection."""

import backend.app.core.scenario_scripts as scenario_module


def test_phone_support_script_matches_call_intent(monkeypatch):
    script = scenario_module._parse_script(
        "call_support",
        {
            "enabled": True,
            "priority": 100,
            "trigger_keywords": ["撥打", "113"],
            "instruction": "提供已核准的電話動作。",
            "actions": [{"action": "tel", "phone_number": "113", "label": "撥打 113 保護專線"}],
        },
    )
    assert script is not None
    monkeypatch.setattr(scenario_module, "get_scenario_scripts", lambda: (script,))

    scripts = scenario_module.get_matching_scenario_scripts("我想撥打 113")

    assert scripts == (script,)
    assert scenario_module.available_actions(scripts) == [
        {"action": "tel", "phone_number": "113", "label": "撥打 113 保護專線"}
    ]


def test_scenario_script_rejects_unknown_actions():
    script = scenario_module._parse_script(
        "unsafe",
        {
            "enabled": True,
            "trigger_keywords": ["測試"],
            "instruction": "測試腳本",
            "actions": [{"action": "url", "phone_number": "https://example.com", "label": "未知"}],
        },
    )

    assert script is not None
    assert script.actions == ()


def test_disabled_scripts_are_not_used_at_runtime(monkeypatch):
    disabled = scenario_module._parse_script(
        "disabled_skill",
        {
            "enabled": False,
            "trigger_keywords": ["測試"],
            "instruction": "不應該使用",
            "actions": [],
        },
    )
    assert disabled is not None
    monkeypatch.setattr(
        scenario_module, "list_scenario_scripts", lambda force_refresh=False: (disabled,)
    )

    assert scenario_module.get_scenario_scripts() == ()


def test_example_scenario_document_is_valid():
    examples = scenario_module._example_scenario_documents()

    assert set(examples) == {"call_support"}
    for script_id, payload in examples.items():
        assert scenario_module.validate_scenario_script(script_id, payload)["name"]
