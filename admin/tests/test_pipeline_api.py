"""Request-local policy, administrator isolation, and public v3 summaries."""

import json
from unittest.mock import Mock

import pytest

from admin.tests.service_helpers import run_diagnostic
from admin_app import api as admin
from client.tests.test_agent import v4_result
from client_app.api import health
from client_app.main import app
from rag_core import chat_service as chat
from rag_core.core import runtime_config as runtime_config_module
from rag_core.core.clarification_tokens import sign_clarification
from rag_core.core.runtime_config import RuntimeConfig


def config(**changes):
    return RuntimeConfig(
        openrouter_model="offline/model",
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0.2,
        top_p=1,
        max_tokens=1000,
        **changes,
    )


def result(*, analysis=None):
    response = {
        "reply": "合成測試回答。",
        "emotion": "未知",
        "emotion_color": "gray",
        "suggested_replies": ["了解下一步", "整理情境"],
        "action_buttons": [],
        "interaction_mode": "answer",
        "clarifying_questions": [],
    }
    guidance = {
        "contract_version": 4,
        "context_revision": 0,
        "summary_update": None,
        "clarification": None,
        "execution": {"route": "offline", "model_calls": 0},
        "analysis": analysis or [],
    }
    return v4_result(reply=json.dumps(response, ensure_ascii=False), guidance=guidance)


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    base = config()
    monkeypatch.setattr(admin, "get_runtime_config", lambda **kwargs: base)
    monkeypatch.setattr(admin, "get_enabled_scenario_catalog", lambda **kwargs: ())
    monkeypatch.setattr(admin, "read_scenario_catalog", lambda: ((), 0))
    monkeypatch.setattr(chat, "get_runtime_config", lambda: base)
    monkeypatch.setattr(health, "get_runtime_config", lambda: base)
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)
    monkeypatch.setattr(chat, "get_agent", lambda: pytest.fail("Unexpected agent invocation"))
    return base


def test_admin_test_requires_auth_before_settings_or_model(monkeypatch, app):
    load = Mock(side_effect=AssertionError("must not read settings"))
    monkeypatch.setattr(admin, "get_runtime_config", load)
    response = app.test_client().post(
        "/api/v1/admin/test-jobs", json={"request": {"message": "synthetic"}}
    )
    assert response.status_code == 401
    load.assert_not_called()


@pytest.mark.parametrize("version", [4])
def test_admin_can_enable_images_for_one_request_without_changing_environment(monkeypatch, version):
    # Exercise request-local override mechanics while the rollout switch is off in production.
    monkeypatch.setattr(runtime_config_module, "IMAGE_UPLOAD_ROLLOUT_ENABLED", True)
    base = config(enable_image_upload=False)
    calls = []

    class Agent:
        async def run(self, **kwargs):
            calls.append(kwargs)
            return result()

    monkeypatch.setattr(admin, "get_runtime_config", lambda **kwargs: base)
    monkeypatch.setattr(chat, "get_agent", Agent)
    write = Mock(side_effect=AssertionError("must not persist test overrides"))
    monkeypatch.setattr(admin, "update_runtime_config", write)
    image = "data:image/png;base64,iVBORw0KGgo="
    response = run_diagnostic(
        {
            "request": {
                "message": "合成文字需求",
                "contract_version": version,
                "image_base64": image,
            },
            "overrides": {
                "enable_image_upload": True,
                "enable_anonymization": False,
                "enable_client_privacy_review": False,
                "pipeline": {"mask_message": False, "mask_case_context": False},
            },
        }
    )
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["image_base64"] == image
    assert calls[0]["runtime_config"].enable_image_upload is True
    assert response.json["diagnostics"]["effective_config"]["enable_client_privacy_review"] is False
    assert base.enable_image_upload is False
    assert base.enable_anonymization is True
    assert base.enable_client_privacy_review is True
    write.assert_not_called()


@pytest.mark.parametrize("version", [4])
@pytest.mark.parametrize("override", [False, True])
def test_admin_disabled_image_policy_rejects_before_agent(monkeypatch, version, override):
    monkeypatch.setattr(
        admin, "get_runtime_config", lambda **kwargs: config(enable_image_upload=override)
    )
    prepare = Mock(side_effect=AssertionError("disabled image must not be prepared"))
    monkeypatch.setattr(chat, "_prepare_agent_input", prepare)
    response = run_diagnostic(
        {
            "request": {
                "message": "合成需求",
                "contract_version": version,
                "image_base64": "data:image/png;base64,iVBORw0KGgo=",
            },
            "overrides": {"enable_image_upload": False} if override else {},
        }
    )
    assert response.status_code == 422
    assert response.json["code"] == "image_upload_disabled"
    assert response.json["retryable"] is False
    prepare.assert_not_called()


@pytest.mark.parametrize(
    "image", ["", "data:image/png;base64,invalid!", "data:image/png;base64,bm90LWEtcG5n"]
)
def test_admin_invalid_images_cannot_be_enabled_by_an_override(monkeypatch, image):
    load = Mock(side_effect=AssertionError("must validate image before reading config"))
    monkeypatch.setattr(admin, "get_runtime_config", load)
    response = run_diagnostic(
        {
            "request": {"message": "合成需求", "image_base64": image},
            "overrides": {"enable_image_upload": True, "enable_client_privacy_review": False},
        }
    )
    assert response.status_code == 422
    assert response.json["code"] == "invalid_request"
    load.assert_not_called()


@pytest.mark.parametrize("review", [False, True])
def test_client_review_setting_does_not_change_backend_text_masking_or_images(monkeypatch, review):
    monkeypatch.setattr(runtime_config_module, "IMAGE_UPLOAD_ROLLOUT_ENABLED", True)
    image = "data:image/png;base64,iVBORw0KGgo="
    request_model = chat.ChatRequest(message="請聯絡 0912345678", image_base64=image)
    arguments, masked = chat._prepare_agent_input(
        request_model, config(enable_image_upload=True, enable_client_privacy_review=review)
    )
    assert masked is True
    assert "0912345678" not in arguments["user_message"]
    assert arguments["image_base64"] == image


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("images", [False, True])
def test_health_and_admin_expose_independent_image_and_client_review_settings(
    monkeypatch, review, images, signed_in
):
    base = config(enable_image_upload=images, enable_client_privacy_review=review)
    monkeypatch.setattr(health, "get_runtime_config", lambda: base)
    monkeypatch.setattr(admin, "get_runtime_config", lambda **kwargs: base)
    client = app.test_client()
    public = client.get("/api/v1/health/").json["client_settings"]
    administrative = (
        signed_in[0]
        .get("/api/v1/admin/config", headers={"Authorization": "Bearer offline-test-key"})
        .json
    )
    assert public["enable_image_upload"] is False
    assert administrative["enable_image_upload"] is images
    for value in (public, administrative):
        assert value["enable_client_privacy_review"] is review
    assert "agent_prompt_sections" not in public
    assert administrative["enable_anonymization"] is True


@pytest.mark.parametrize("admin_test", [False, True])
@pytest.mark.parametrize(
    "fields",
    [
        {"image_url": "https://example.test/private.png"},
        {"attachments": [{"image_base64": "private-image"}]},
        {"history": [{"role": "user", "content": "合成內容", "image_base64": "private-image"}]},
        {
            "history": [
                {"role": "user", "content": [{"type": "image_url", "image_url": "private-image"}]}
            ]
        },
        {
            "contract_version": 4,
            "case_context": {"schema_version": 3, "image_base64": "private-image"},
        },
    ],
)
def test_chat_rejects_images_in_unknown_or_multimodal_fields(monkeypatch, admin_test, fields):
    prepare = Mock(side_effect=AssertionError("must not prepare invalid request"))
    monkeypatch.setattr(chat, "_prepare_agent_input", prepare)
    payload = {"message": "合成文字需求", **fields}
    path = "/api/v1/chat/"
    if admin_test:
        payload = {"request": payload}
    response = run_diagnostic(payload) if admin_test else app.test_client().post(path, json=payload)
    assert response.status_code == 422
    assert response.json["code"] == "invalid_request"
    assert "private-image" not in response.get_data(as_text=True)
    prepare.assert_not_called()


def test_admin_override_is_ephemeral_and_public_requests_use_environment(
    monkeypatch, isolated_runtime
):
    calls = []

    class Agent:
        async def run(self, **kwargs):
            calls.append(kwargs)
            return result()

    monkeypatch.setattr(chat, "get_agent", Agent)
    write = Mock(side_effect=AssertionError("must not persist overrides"))
    monkeypatch.setattr(admin, "update_runtime_config", write)
    client = app.test_client()
    response = run_diagnostic(
        {
            "request": {"message": "請聯絡 0912345678"},
            "overrides": {"pipeline": {"mask_message": False, "enable_rag": False}},
        }
    )
    assert response.status_code == 200
    assert calls[0]["user_message"] == "請聯絡 0912345678"
    assert calls[0]["use_rag"] is False
    assert calls[0]["runtime_config"] is not isolated_runtime
    assert response.json["diagnostics"]["pii"]["message"]["enabled"] is False
    assert "0912345678" not in json.dumps(response.json["diagnostics"]["pii"])
    assert response.json["diagnostics"]["input_stages"]["message"]["before"] == "請聯絡 0912345678"
    assert response.headers["Cache-Control"] == "no-store"
    normal = client.post("/api/v1/chat/", json={"message": "請聯絡 0912345678"})
    assert normal.status_code == 200
    assert "0912345678" not in calls[1]["user_message"]
    assert calls[1]["use_rag"] is True
    assert "diagnostics" not in calls[1]
    assert isolated_runtime.pipeline["mask_message"] is True
    write.assert_not_called()


@pytest.mark.parametrize(
    "key", ["overrides", "pipeline", "diagnostics", "runtime_config", "clarification_constraints"]
)
def test_ordinary_chat_cannot_override_server_policy(key):
    response = app.test_client().post(
        "/api/v1/chat/", json={"message": "合成資料", key: {"mask_message": False}}
    )
    assert response.status_code == 422


def test_history_budget_retains_complete_recent_turn_and_only_counts_diagnostics():
    request = chat.ChatRequest(
        message="next",
        history=[
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "recent user"},
            {"role": "assistant", "content": "recent answer"},
        ],
    )
    diagnostics = {}
    arguments, _ = chat._prepare_agent_input(
        request, config(pipeline={"history_max_messages": 3}), diagnostics
    )
    assert [item["content"] for item in arguments["history"]] == ["recent user", "recent answer"]
    assert diagnostics["history"]["retained_messages"] == 2
    assert diagnostics["history"]["removed_messages"] == 2
    assert "old user" not in json.dumps(diagnostics)


def test_v3_requires_matching_schema_and_signed_selection():
    request = {
        "message": "合成選擇",
        "contract_version": 3,
        "clarification_answer": {
            "question_id": "case.behavior.0",
            "fact_key": "behavior",
            "status": "provided",
            "value": ["言語", "碰觸"],
        },
    }
    client = app.test_client()
    assert client.post("/api/v1/chat/", json=request).status_code == 422
    issued = {
        "question_id": "case.behavior.0",
        "fact_key": "behavior",
        "selection_mode": "single",
        "max_selections": 1,
        "options": [{"value": "言語"}, {"value": "碰觸"}],
    }
    request["clarification_answer"]["validation_token"] = sign_clarification(issued)
    assert client.post("/api/v1/chat/", json=request).status_code == 422
    assert (
        client.post(
            "/api/v1/chat/",
            json={
                "message": "合成情境",
                "contract_version": 3,
                "case_context": {"schema_version": 1},
            },
        ).status_code
        == 422
    )


def test_analysis_stream_and_done_match_one_snapshot(monkeypatch):
    entry = {
        "stage": "understanding",
        "summary": "使用者希望了解處理方式。",
        "facts": [],
        "source_labels": [],
        "limitations": [],
    }
    load = Mock(return_value=config())
    monkeypatch.setattr(chat, "get_runtime_config", load)

    class Agent:
        async def run(self, on_analysis, **kwargs):
            assert kwargs["runtime_config"].pipeline["enable_analysis"]
            await on_analysis(entry)
            return result(analysis=[entry])

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = app.test_client().post(
        "/api/v1/chat/", json={"message": "合成問題", "contract_version": 4, "stream": True}
    )
    body = response.get_data(as_text=True)
    frames = [frame for frame in body.split("\n\n") if frame.startswith("event:")]
    summaries = [
        json.loads(frame.split("\ndata: ")[1])
        for frame in frames
        if frame.startswith("event: analysis")
    ]
    done = json.loads(
        next(frame.split("\ndata: ")[1] for frame in frames if frame.startswith("event: done"))
    )
    assert summaries == [entry] == done["analysis"]
    load.assert_called_once_with()


def test_admin_provider_errors_never_log_or_return_submitted_content(monkeypatch, caplog):
    class Agent:
        async def run(self, **kwargs):
            raise RuntimeError("PRIVATE_ADMIN_TEST_CANARY")

    monkeypatch.setattr(chat, "get_agent", Agent)
    response = run_diagnostic(
        {
            "request": {"message": "PRIVATE_ADMIN_TEST_CANARY"},
            "overrides": {"development_mode": True},
        }
    )
    assert response.status_code == 500
    assert "PRIVATE_ADMIN_TEST_CANARY" not in caplog.text
    assert "PRIVATE_ADMIN_TEST_CANARY" not in response.get_data(as_text=True)
