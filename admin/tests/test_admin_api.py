"""測試：runtime admin API 將 prompt section 寫入 Firestore。"""

import logging

import pytest

import admin_app.api as admin_module
from rag_core.core.runtime_config import RuntimeConfig


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


@pytest.fixture(autouse=True)
def no_fingerprint_catalog_network(monkeypatch):
    monkeypatch.setattr(admin_module, "get_enabled_scenario_catalog", lambda **kwargs: ())
    monkeypatch.setattr(admin_module, "read_scenario_catalog", lambda: ((), 0))
    monkeypatch.setattr(admin_module, "get_scenario_revision", lambda: 1)


def test_admin_updates_prompt_sections(monkeypatch, signed_in):
    calls = []
    monkeypatch.setattr(admin_module, "get_runtime_config", lambda **kwargs: _runtime_config())
    monkeypatch.setattr(
        admin_module,
        "update_runtime_config",
        lambda payload, updated_by, expected_revision: (
            calls.append((payload, updated_by)) or _runtime_config()
        ),
    )

    client, headers = signed_in
    headers = {**headers, "If-Match": "0"}
    response = client.put(
        "/api/v1/admin/config",
        headers={**signed_in[1], "If-Match": "0"},
        json={"agent_prompt_sections": {"language": "main form value"}},
    )

    assert response.status_code == 200
    assert calls == [({"agent_prompt_sections": {"language": "main form value"}}, "shared-admin")]
    prompt_sections = response.get_json()["agent_prompt_sections"]
    assert prompt_sections["language"] == "dev prompt"
    assert prompt_sections["core_mission"]


def test_admin_resets_runtime_config_to_local_defaults(monkeypatch, signed_in):
    calls = []
    monkeypatch.setattr(admin_module, "get_runtime_config", lambda **kwargs: _runtime_config())
    monkeypatch.setattr(
        admin_module,
        "reset_runtime_config",
        lambda updated_by, expected_revision: calls.append(updated_by) or _runtime_config(),
    )

    response = signed_in[0].post(
        "/api/v1/admin/config/reset",
        headers={**signed_in[1], "If-Match": "0"},
    )

    assert response.status_code == 200
    assert calls == ["shared-admin"]


def test_admin_seeds_example_scenario_scripts(monkeypatch, signed_in):
    monkeypatch.setattr(
        admin_module,
        "seed_example_scenario_scripts",
        lambda updated_by, expected_revision: [type("Script", (), {"script_id": "call_support"})()],
    )

    response = signed_in[0].post(
        "/api/v1/admin/scenario-scripts/seed",
        headers={**signed_in[1], "If-Match": "0"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"script_ids": ["call_support"], "revision": 1}


def test_admin_lists_and_updates_shared_scenario_scripts(monkeypatch, signed_in):
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
    monkeypatch.setattr(admin_module, "read_scenario_catalog", lambda: ((script,), 0))
    monkeypatch.setattr(
        admin_module,
        "upsert_scenario_script",
        lambda script_id, payload, updated_by, expected_revision: (
            calls.append((script_id, payload, updated_by)) or script
        ),
    )

    client, headers = signed_in
    headers = {**headers, "If-Match": "0"}
    headers = {**headers, "If-Match": "0"}
    assert (
        client.get("/api/v1/admin/scenario-scripts", headers=headers).get_json()["skills"][0]["id"]
        == "call_support"
    )
    response = client.put(
        "/api/v1/admin/scenario-scripts/call_support",
        headers=headers,
        json={
            "name": "電話求助",
            "trigger_keywords": ["113"],
            "instruction": "提供電話。",
            "actions": [],
        },
    )

    assert response.status_code == 200
    assert calls[0][0] == "call_support"
    assert calls[0][2] == "shared-admin"


@pytest.mark.parametrize(
    ("method", "path", "dependency", "operation", "public_detail"),
    [
        (
            "PUT",
            "/config",
            "update_runtime_config",
            "update_runtime_config",
            "update runtime config",
        ),
        (
            "POST",
            "/config/reset",
            "reset_runtime_config",
            "reset_runtime_config",
            "reset runtime config",
        ),
        (
            "POST",
            "/config/seed",
            "seed_runtime_config_if_missing",
            "seed_runtime_config",
            "seed runtime config",
        ),
        (
            "GET",
            "/scenario-scripts",
            "read_scenario_catalog",
            "list_scenario_scripts",
            "load scenario scripts",
        ),
        (
            "PUT",
            "/scenario-scripts/test_skill",
            "upsert_scenario_script",
            "upsert_scenario_script",
            "save scenario script",
        ),
        (
            "DELETE",
            "/scenario-scripts/test_skill",
            "delete_scenario_script",
            "delete_scenario_script",
            "delete scenario script",
        ),
        (
            "POST",
            "/scenario-scripts/seed",
            "seed_example_scenario_scripts",
            "seed_scenario_scripts",
            "seed scenario scripts",
        ),
    ],
)
def test_admin_operation_failure_logs_cause_without_changing_public_error(
    monkeypatch, caplog, method, path, dependency, operation, public_detail, signed_in
):
    failure = RuntimeError("Firestore write failed because the test database is unavailable")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(admin_module, dependency, fail)

    with caplog.at_level(logging.ERROR, logger=admin_module.__name__):
        response = signed_in[0].open(
            f"/api/v1/admin{path}",
            method=method,
            headers={
                **signed_in[1],
                "If-Match": "0",
                "Authorization": "Bearer private-admin-token",
                "X-Admin-User": "private-admin-identity",
                "X-Admin-Token": "private-header-token",
            },
            json={"instruction": "private-payload-value"},
        )

    assert response.status_code == 500
    assert response.get_json() == {"detail": f"Failed to {public_detail}: RuntimeError"}
    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_operation_failed"
    ]
    assert len(failures) == 1
    record = failures[0]
    assert record.levelno == logging.ERROR
    assert record.operation == operation
    assert record.error_type == "RuntimeError"
    assert record.exc_info is not None
    assert record.exc_info[1] is failure
    assert record.exc_info[2] is not None
    assert str(failure) in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert operation in record.getMessage()
    for private_value in (
        "private-admin-token",
        "private-header-token",
        "private-admin-identity",
        "private-payload-value",
    ):
        assert private_value not in caplog.text
        assert private_value not in repr(record.__dict__)
