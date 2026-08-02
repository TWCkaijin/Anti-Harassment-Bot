"""測試：runtime admin API 將 prompt section 寫入 Firestore。"""

import backend.app.api.admin as admin_module
from backend.app.core.runtime_config import RuntimeConfig
from backend.app.main import app


def _runtime_config():
    return RuntimeConfig(
        openrouter_model="test/model",
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0.2,
        top_p=1.0,
        max_tokens=1200,
        agent_prompt_sections={"language": "dev prompt"},
        rag_collections={
            "law": "rag_documents",
            "judgment": "rag_judgments",
            "remedy": "rag_remedies",
        },
    )


def test_admin_updates_prompt_sections(monkeypatch):
    calls = []
    monkeypatch.setattr(admin_module.settings, "admin_api_key", "test-token")
    monkeypatch.setattr(
        admin_module, "get_runtime_config", lambda force_refresh=True: _runtime_config()
    )
    monkeypatch.setattr(
        admin_module,
        "update_runtime_config",
        lambda payload, updated_by: calls.append((payload, updated_by)) or _runtime_config(),
    )

    client = app.test_client()
    response = client.put(
        "/api/v1/admin/config",
        headers={"Authorization": "Bearer test-token"},
        json={"agent_prompt_sections": {"language": "main form value"}},
    )

    assert response.status_code == 200
    assert calls == [({"agent_prompt_sections": {"language": "main form value"}}, "admin")]
    prompt_sections = response.get_json()["agent_prompt_sections"]
    assert prompt_sections["language"] == "dev prompt"
    assert prompt_sections["core_mission"]


def test_admin_resets_runtime_config_to_local_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(admin_module.settings, "admin_api_key", "test-token")
    monkeypatch.setattr(
        admin_module, "get_runtime_config", lambda force_refresh=True: _runtime_config()
    )
    monkeypatch.setattr(
        admin_module,
        "reset_runtime_config",
        lambda updated_by: calls.append(updated_by) or _runtime_config(),
    )

    response = app.test_client().post(
        "/api/v1/admin/config/reset",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert calls == ["admin"]


def test_admin_seeds_example_scenario_scripts(monkeypatch):
    monkeypatch.setattr(admin_module.settings, "admin_api_key", "test-token")
    monkeypatch.setattr(
        admin_module,
        "seed_example_scenario_scripts",
        lambda updated_by: [type("Script", (), {"script_id": "call_support"})()],
    )

    response = app.test_client().post(
        "/api/v1/admin/scenario-scripts/seed",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"script_ids": ["call_support"]}


def test_admin_lists_and_updates_shared_scenario_scripts(monkeypatch):
    script = type(
        "Script",
        (),
        {
            "script_id": "call_support",
            "public_dict": lambda self: {
                "id": "call_support",
                "name": "電話求助",
                "enabled": True,
                "priority": 100,
                "trigger_keywords": ["113"],
                "instruction": "提供電話。",
                "actions": [],
            },
        },
    )()
    calls = []
    monkeypatch.setattr(admin_module.settings, "admin_api_key", "test-token")
    monkeypatch.setattr(admin_module, "list_scenario_scripts", lambda force_refresh: (script,))
    monkeypatch.setattr(
        admin_module,
        "upsert_scenario_script",
        lambda script_id, payload, updated_by: calls.append((script_id, payload, updated_by)) or script,
    )

    client = app.test_client()
    headers = {"Authorization": "Bearer test-token"}
    assert client.get("/api/v1/admin/scenario-scripts", headers=headers).get_json()["skills"][0]["id"] == "call_support"
    response = client.put(
        "/api/v1/admin/scenario-scripts/call_support",
        headers=headers,
        json={"name": "電話求助", "trigger_keywords": ["113"], "instruction": "提供電話。", "actions": []},
    )

    assert response.status_code == 200
    assert calls[0][0] == "call_support"
    assert calls[0][2] == "admin"
