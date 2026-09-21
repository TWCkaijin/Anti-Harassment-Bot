"""Tests for runtime Skill loading, generic actions and persisted overrides."""

from types import SimpleNamespace

import pytest

import backend.app.core.scenario_scripts as scenario_module


@pytest.fixture(autouse=True)
def clear_cache():
    scenario_module.invalidate_scenario_scripts_cache()
    yield
    scenario_module.invalidate_scenario_scripts_cache()


def _payload(**changes):
    return {
        "name": "測試 Skill",
        "trigger_keywords": ["測試"],
        "instruction": "依情境提供已設定的動作。",
        "actions": [],
        **changes,
    }


def _fake_firestore(monkeypatch, documents):
    writes = {}
    deletes = []

    class Document:
        def __init__(self, script_id):
            self.id = script_id

        def get(self):
            return SimpleNamespace(exists=self.id in documents)

        def set(self, data):
            writes[self.id] = data
            documents[self.id] = data

        def delete(self):
            deletes.append(self.id)
            documents.pop(self.id, None)

    class Collection:
        def stream(self):
            return [
                SimpleNamespace(id=script_id, to_dict=lambda data=data: data)
                for script_id, data in documents.items()
            ]

        def document(self, script_id):
            return Document(script_id)

    monkeypatch.setattr(
        scenario_module.firestore,
        "client",
        lambda: SimpleNamespace(collection=lambda name: Collection()),
    )
    return writes, deletes


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

    assert set(examples) == {"call_support", "official_website", "choose_next_step"}
    for script_id, payload in examples.items():
        assert scenario_module.validate_scenario_script(script_id, payload)["name"]


def test_packaged_skills_work_without_firestore_seeding(monkeypatch):
    _fake_firestore(monkeypatch, {})

    scripts = scenario_module.get_matching_scenario_scripts("請前往屏東縣政府網頁")

    assert scenario_module.available_actions(scripts) == [
        {"action": "url", "url": "https://www.pthg.gov.tw/", "label": "前往屏東縣政府網頁"}
    ]
    options = scenario_module.available_actions(
        scenario_module.get_matching_scenario_scripts("請彈出選項讓我選擇")
    )
    assert options[0]["action"] == "options"
    assert len(options[0]["options"]) >= 2


def test_matching_uses_recent_exchange_for_followup(monkeypatch):
    _fake_firestore(monkeypatch, {})

    scripts = scenario_module.get_matching_scenario_scripts(
        "好，請提供",
        history=[{"role": "assistant", "content": "你希望查看屏東縣政府的官方網站嗎？"}],
    )

    assert [script.script_id for script in scripts] == ["official_website"]
    assert (
        scenario_module.get_matching_scenario_scripts(
            "謝謝",
            history=[
                {"role": "assistant", "content": "屏東縣政府"},
                {"role": "user", "content": "我想談其他的事"},
                {"role": "assistant", "content": "請告訴我"},
            ],
        )
        == ()
    )


def test_disabled_firestore_override_suppresses_builtin(monkeypatch):
    _fake_firestore(monkeypatch, {"official_website": {"enabled": False}})

    assert scenario_module.get_matching_scenario_scripts("前往屏東縣政府網站") == ()
    assert (
        next(
            script
            for script in scenario_module.list_scenario_scripts()
            if script.script_id == "official_website"
        ).enabled
        is False
    )


def test_firestore_failure_falls_back_to_packaged_skills(monkeypatch):
    def unavailable():
        raise RuntimeError("Firestore unavailable")

    monkeypatch.setattr(scenario_module, "_scripts_from_firestore", unavailable)

    assert {script.script_id for script in scenario_module.get_scenario_scripts()} == {
        "call_support",
        "official_website",
        "choose_next_step",
    }


def test_read_outage_preserves_cached_disabled_override(monkeypatch):
    _fake_firestore(monkeypatch, {"official_website": {"enabled": False}})
    scenario_module.list_scenario_scripts()

    def unavailable():
        raise RuntimeError("Firestore unavailable")

    monkeypatch.setattr(scenario_module, "_scripts_from_firestore", unavailable)

    assert "official_website" not in {
        script.script_id for script in scenario_module.get_scenario_scripts(force_refresh=True)
    }


def test_malformed_persisted_documents_do_not_hide_other_skills(monkeypatch):
    _fake_firestore(
        monkeypatch,
        {
            "bad_document": ["not an object"],
            "bad_actions": _payload(actions=None, priority=float("inf")),
            "official_website": {"enabled": False, "trigger_keywords": None},
            "custom_skill": _payload(
                actions=[
                    {"action": "url", "label": "測試網站", "url": "https://example.com/"},
                    {"action": "url", "label": "錯誤網址", "url": "javascript:alert(1)"},
                ]
            ),
        },
    )

    scripts = {script.script_id: script for script in scenario_module.list_scenario_scripts()}

    assert "bad_document" not in scripts
    assert scripts["bad_actions"].actions == ()
    assert scripts["bad_actions"].priority == 0
    assert not scripts["official_website"].enabled
    assert len(scripts["custom_skill"].actions) == 1


def test_generic_actions_are_formatted_as_model_selectors():
    options = {
        "action": "options",
        "id": "custom_menu",
        "label": "選擇",
        "title": "想了解什麼？",
        "options": [
            {"label": "第一項", "value": "想了解第一項"},
            {"label": "第二項", "value": "想了解第二項"},
        ],
    }
    script = scenario_module._parse_script(
        "custom_skill",
        _payload(
            actions=[{"action": "url", "label": "網站", "url": "https://example.com/"}, options]
        ),
    )

    text = scenario_module.format_scenario_instruction((script,))

    assert '{"action": "url", "url": "https://example.com/"}' in text
    assert '{"action": "options", "id": "custom_menu"}' in text
    assert "想了解第一項" in text
    assert scenario_module.available_actions((script, script)) == [
        action.public_dict() for action in script.actions
    ]


def test_conflicting_menu_ids_only_expose_first_definition_to_model():
    scripts = []
    for skill_id, priority, prefix in [
        ("high_priority", 100, "優先"),
        ("low_priority", 10, "衝突"),
    ]:
        scripts.append(
            scenario_module._parse_script(
                skill_id,
                _payload(
                    priority=priority,
                    actions=[
                        {
                            "action": "options",
                            "id": "shared_menu",
                            "label": f"{prefix}按鈕",
                            "title": f"{prefix}標題",
                            "options": [
                                {"label": f"{prefix}選項一", "value": f"{prefix}訊息一"},
                                {"label": f"{prefix}選項二", "value": f"{prefix}訊息二"},
                            ],
                        }
                    ],
                ),
            )
        )

    permitted = scenario_module.available_actions(tuple(scripts))
    instructions = scenario_module.format_scenario_instruction(tuple(scripts))

    assert len(permitted) == 1
    assert permitted[0]["title"] == "優先標題"
    assert "優先標題" in instructions
    assert "優先訊息一" in instructions
    assert "衝突按鈕" not in instructions
    assert "衝突標題" not in instructions
    assert "衝突訊息一" not in instructions
    assert "不使用本段指示中與其衝突的動作定義" in instructions


@pytest.mark.parametrize("actions", [None, {}, "invalid", [{"action": "unknown"}]])
def test_admin_rejects_malformed_actions_with_value_error(actions):
    with pytest.raises(ValueError, match="actions"):
        scenario_module.validate_scenario_script("custom_skill", _payload(actions=actions))


def test_deleting_builtin_disables_it_and_deleting_custom_removes_it(monkeypatch):
    documents = {"custom_skill": _payload()}
    writes, deletes = _fake_firestore(monkeypatch, documents)

    scenario_module.delete_scenario_script("official_website")
    scenario_module.delete_scenario_script("custom_skill")

    assert writes["official_website"]["enabled"] is False
    assert deletes == ["custom_skill"]
    assert scenario_module.get_matching_scenario_scripts("屏東官網") == ()


def test_seeding_is_additive_and_does_not_replace_existing_override(monkeypatch):
    documents = {"official_website": {"enabled": False}}
    writes, _ = _fake_firestore(monkeypatch, documents)

    scripts = scenario_module.seed_example_scenario_scripts("test-admin")

    assert set(writes) == {"call_support", "choose_next_step"}
    assert documents["official_website"] == {"enabled": False}
    assert len(scripts) == 3
