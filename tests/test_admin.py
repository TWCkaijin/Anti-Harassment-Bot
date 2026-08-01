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
