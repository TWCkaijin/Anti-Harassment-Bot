"""測試：Flask API endpoints。"""

import base64
import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.app.agents import AgentResult
from backend.app.api import chat as chat_module
from backend.app.core.runtime_config import RuntimeConfig
from backend.app.main import app
from backend.app.rag.base import RAGVectorSearchError

VALID_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\nminimal-test-payload"
).decode("ascii")


def fake_runtime_config(**overrides):
    data = {
        "openrouter_model": "test/model",
        "rag_retrieval_top_k": 3,
        "enable_anonymization": True,
        "temperature": 0.2,
        "top_p": 1.0,
        "max_tokens": 1200,
        "rag_collections": {
            "law": "rag_documents",
            "judgment": "rag_judgments",
            "remedy": "rag_remedies",
        },
        "enable_image_upload": True,
    }
    data.update(overrides)
    return RuntimeConfig(**data)


def test_health_check():
    client = app.test_client()
    response = client.get("/api/v1/health/")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert "timestamp" in data
    assert "version" in data


def test_root():
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "service" in response.get_json()


def test_chat_response_shape(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply='{"emotion":"冷靜","emotion_color":"green","reply":"我會陪你整理下一步。","suggested_replies":["我想先了解申訴流程","我需要緊急協助"]}',
                rag_used=True,
                sources=[
                    {
                        "label": "性騷擾防治法第13條",
                        "type": "law",
                        "collection": "rag_documents",
                    }
                ],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    client = app.test_client()
    response = client.post(
        "/api/v1/chat/",
        json={"message": "我想知道申訴期限", "history": [], "use_rag": True},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["reply"] == "我會陪你整理下一步。"
    assert data["rag_used"] == {
        "status": True,
        "sources": [
            {
                "label": "性騷擾防治法第13條",
                "type": "law",
                "collection": "rag_documents",
            }
        ],
    }
    assert data["emotion"] == "冷靜"
    assert data["emotion_color"] == "green"
    assert data["suggested_replies"] == ["我想先了解申訴流程", "我需要緊急協助"]
    assert "session_id" in data
    assert "debug_tool_calls" not in data


def test_chat_returns_tool_call_diagnostics_in_development_mode(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply=(
                    '{"emotion":"冷靜","emotion_color":"green","reply":"我已完成查詢。",'
                    '"suggested_replies":["我想看更多資料","我想知道下一步"]}'
                ),
                tool_calls=[
                    {
                        "name": "retrieve_harassment_knowledge",
                        "arguments": {"query": "申訴期限", "data_type": "law"},
                        "result_count": 2,
                    }
                ],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(
        chat_module, "get_runtime_config", lambda: fake_runtime_config(development_mode=True)
    )

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "申訴期限多久", "history": [], "use_rag": True},
    )

    assert response.status_code == 200
    assert response.get_json()["debug_tool_calls"] == [
        {
            "name": "retrieve_harassment_knowledge",
            "arguments": {"query": "申訴期限", "data_type": "law"},
            "result_count": 2,
        }
    ]


def test_chat_passes_use_rag_false(monkeypatch):
    captured = {}

    class FakeAgent:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return AgentResult(
                reply='{"emotion":"未知","emotion_color":"gray","reply":"好的。","suggested_replies":["我想多說一些","我想了解下一步"]}',
                rag_used=False,
                sources=[],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    client = app.test_client()
    response = client.post(
        "/api/v1/chat/",
        json={"message": "先不要查資料", "history": [], "use_rag": False},
    )

    assert response.status_code == 200
    assert captured["use_rag"] is False
    assert response.get_json()["rag_used"] == {"status": False, "sources": []}


def test_chat_returns_only_scenario_approved_phone_actions(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply=(
                    '{"emotion":"焦慮","emotion_color":"yellow","reply":"可以撥打 113。",'
                    '"suggested_replies":["我想撥打 113","我想先了解流程"],'
                    '"action_buttons":[{"action":"tel","phone_number":"113"},'
                    '{"action":"tel","phone_number":"000"}]}'
                ),
                available_actions=[
                    {
                        "action": "tel",
                        "phone_number": "113",
                        "label": "撥打 113 保護專線",
                    }
                ],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "我想撥打 113", "history": [], "use_rag": False},
    )

    assert response.status_code == 200
    assert response.get_json()["action_buttons"] == [
        {
            "action": "tel",
            "phone_number": "113",
            "label": "撥打 113 保護專線",
        }
    ]


def test_chat_resolves_generic_action_selectors_from_skill_definitions(monkeypatch):
    approved = [
        {"action": "tel", "phone_number": "113", "label": "撥打保護專線"},
        {"action": "url", "url": "https://www.pthg.gov.tw/", "label": "前往屏東縣政府網頁"},
        {
            "action": "options",
            "id": "next_step",
            "label": "選擇下一步",
            "title": "您想先了解什麼？",
            "options": [
                {"label": "求助管道", "value": "我想先了解求助管道"},
                {"label": "繼續說明", "value": "我想再說明我的情況"},
            ],
        },
    ]

    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply=json.dumps(
                    {
                        "emotion": "冷靜",
                        "emotion_color": "green",
                        "reply": "您可以開啟網頁，或選擇希望了解的方向。",
                        "suggested_replies": ["我想了解求助管道", "我想繼續說明"],
                        "action_buttons": [
                            {"action": "tel", "phone_number": "113"},
                            {"action": "url", "url": "https://www.pthg.gov.tw/"},
                            {"action": "options", "id": "next_step"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                available_actions=approved,
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())
    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "我想看看有哪些選項與網站", "history": [], "use_rag": False},
    )

    assert response.status_code == 200
    assert response.get_json()["action_buttons"] == approved


@pytest.mark.parametrize(
    "selectors,expected",
    [
        ([{"action": "url", "url": "https://unapproved.example/"}], []),
        ([{"action": "options", "id": "unapproved_options"}], []),
        (
            [{"action": "url", "url": "https://approved.example/"}] * 3,
            [{"action": "url", "url": "https://approved.example/", "label": "已核准網站"}],
        ),
    ],
)
def test_chat_omits_unapproved_and_duplicate_actions(monkeypatch, selectors, expected):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply=json.dumps(
                    {
                        "emotion": "冷靜",
                        "emotion_color": "green",
                        "reply": "我會陪您整理。",
                        "suggested_replies": ["我想了解更多", "我想再說明"],
                        "action_buttons": selectors,
                    }
                ),
                available_actions=[
                    {"action": "url", "url": "https://approved.example/", "label": "已核准網站"}
                ],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())
    response = app.test_client().post(
        "/api/v1/chat/", json={"message": "提供網站", "history": [], "use_rag": False}
    )
    assert response.status_code == 200
    assert response.get_json()["action_buttons"] == expected


def test_chat_repairs_bare_newlines_inside_json_string(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply='{"emotion":"冷靜","emotion_color":"green","reply":"第一段\n\n第二段","suggested_replies":["我想補充細節","我想了解可用資源"]}',
                rag_used=False,
                sources=[],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    client = app.test_client()
    response = client.post(
        "/api/v1/chat/",
        json={"message": "測試換行", "history": [], "use_rag": False},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["reply"] == "第一段\n\n第二段"
    assert data["emotion"] == "冷靜"
    assert data["emotion_color"] == "green"


def test_chat_renders_literal_escape_sequences_before_markdown_response(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply=(
                    '{"emotion":"冷靜","emotion_color":"green",'
                    '"reply":"第一段\\\\n\\\\n## 下一步\\\\n- 保留訊息紀錄",'
                    '"suggested_replies":["我想補充細節","我想知道申訴期限"]}'
                )
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "測試跳脫字元", "history": [], "use_rag": False},
    )

    assert response.status_code == 200
    assert response.get_json()["reply"] == "第一段\n\n## 下一步\n- 保留訊息紀錄"


def test_chat_returns_clarification_questions(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply=(
                    '{"emotion":"焦慮","emotion_color":"yellow","reply":"我想先了解情況。",'
                    '"suggested_replies":["我可以補充關係","我可以補充發生地點"],'
                    '"interaction_mode":"clarify",'
                    '"clarifying_questions":["對方和您是什麼關係？","事件發生在哪個場域？"]}'
                )
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "我不知道這算不算", "history": [], "use_rag": False},
    )

    assert response.status_code == 200
    assert response.get_json()["interaction_mode"] == "clarify"
    assert response.get_json()["clarifying_questions"] == [
        "對方和您是什麼關係？",
        "事件發生在哪個場域？",
    ]


def test_parse_agent_json_response_strips_code_fence():
    data = chat_module.parse_agent_json_response(
        '```json\n{"emotion":"未知","emotion_color":"gray","reply":"好的"}\n```'
    )

    assert data == {"emotion": "未知", "emotion_color": "gray", "reply": "好的"}


def test_chat_returns_retryable_error_for_invalid_model_schema(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(reply='{"reply":"缺少必要欄位"}')

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(
        chat_module, "get_runtime_config", lambda: fake_runtime_config(development_mode=True)
    )

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "測試 schema", "history": [], "use_rag": False},
    )

    assert response.status_code == 502
    assert response.get_json()["detail"] == "伺服器回傳錯誤，正在重試中"
    assert response.get_json()["retryable"] is True
    assert "ValidationError" in response.get_json()["debug_message"]


def test_chat_marks_unexpected_error_non_retryable_with_opaque_id(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            raise RuntimeError("upstream model rejected the JSON schema")

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "測試 schema", "history": [], "use_rag": False},
    )

    assert response.status_code == 500
    payload = response.get_json()
    assert payload == {
        "code": "internal_error",
        "detail": "伺服器無法完成請求",
        "error_id": payload["error_id"],
        "retryable": False,
    }
    assert UUID(hex=payload["error_id"]).hex == payload["error_id"]


def test_chat_returns_retryable_error_for_transient_upstream_failure(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            raise TimeoutError("upstream timed out")

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "測試 timeout", "history": [], "use_rag": False},
    )

    assert response.status_code == 502
    assert response.get_json()["code"] == "upstream_invalid_response"
    assert response.get_json()["retryable"] is True


def test_chat_returns_retryable_503_for_typed_rag_failure(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            raise RAGVectorSearchError("vector index unavailable")

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "請查申訴期限", "history": [], "use_rag": True},
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "code": "rag_unavailable",
        "detail": "檢索服務暫時無法使用，請稍後再試",
        "retryable": True,
    }


def test_chat_accepts_image_only_request(monkeypatch):
    captured = {}

    class FakeAgent:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return AgentResult(
                reply=(
                    '{"emotion":"未知","emotion_color":"gray","reply":"我已收到圖片。",'
                    '"suggested_replies":["我想補充背景","請協助我整理"]}'
                )
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "", "history": [], "image_base64": VALID_PNG_DATA_URL},
    )

    assert response.status_code == 200
    assert captured["user_message"] == ""
    assert captured["image_base64"] == VALID_PNG_DATA_URL


def test_chat_requires_message_or_image():
    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "   ", "history": []},
    )

    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_request"
    assert response.get_json()["retryable"] is False


@pytest.mark.parametrize(
    "image_data_url",
    [
        "not-a-data-url",
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        "data:image/png;base64,bm90LWEtcG5n",
        "data:image/png;base64,***",
    ],
)
def test_chat_rejects_invalid_image_data(image_data_url):
    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "", "history": [], "image_base64": image_data_url},
    )

    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_request"


def test_image_validation_rejects_decoded_payload_over_limit():
    oversized = b"\x89PNG\r\n\x1a\n" + b"x" * chat_module.MAX_IMAGE_BYTES
    data_url = "data:image/png;base64," + base64.b64encode(oversized).decode("ascii")

    with pytest.raises(ValueError, match="decoded size"):
        chat_module._validate_image_data_url(data_url)


def test_chat_rejects_image_when_runtime_upload_is_disabled(monkeypatch):
    monkeypatch.setattr(
        chat_module,
        "get_runtime_config",
        lambda: fake_runtime_config(enable_image_upload=False),
    )
    monkeypatch.setattr(
        chat_module,
        "get_agent",
        lambda: (_ for _ in ()).throw(AssertionError("agent must not be initialized")),
    )

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "圖片內容", "history": [], "image_base64": VALID_PNG_DATA_URL},
    )

    assert response.status_code == 422
    assert response.get_json()["retryable"] is False


def test_chat_returns_explicit_maintenance_response_without_initializing_agent(monkeypatch):
    monkeypatch.setattr(
        chat_module,
        "get_runtime_config",
        lambda: fake_runtime_config(maintenance_message="系統維護中，請稍後再試。"),
    )
    monkeypatch.setattr(
        chat_module,
        "get_agent",
        lambda: (_ for _ in ()).throw(AssertionError("agent must not be initialized")),
    )

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "你好", "history": []},
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "code": "maintenance",
        "detail": "系統維護中，請稍後再試。",
        "retryable": False,
    }


def test_history_accepts_full_assistant_reply_but_keeps_user_limit():
    request_model = chat_module.ChatRequest(
        message="下一步",
        history=[{"role": "assistant", "content": "a" * 6000}],
    )
    assert len(request_model.history[0].content) == 6000

    with pytest.raises(ValidationError, match="user history content"):
        chat_module.ChatRequest(
            message="下一步",
            history=[{"role": "user", "content": "u" * 2001}],
        )


def test_chat_rejects_history_over_total_character_budget():
    response = app.test_client().post(
        "/api/v1/chat/",
        json={
            "message": "下一步",
            "history": [
                {"role": "assistant", "content": "a" * 6000},
                {"role": "assistant", "content": "b" * 6000},
                {"role": "assistant", "content": "c" * 6000},
                {"role": "assistant", "content": "d" * 6000},
                {"role": "assistant", "content": "e" * 6000},
                {"role": "assistant", "content": "f"},
            ],
        },
    )

    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_request"
    assert any(
        error["field"] == "" and "history content" in error["message"]
        for error in response.get_json()["errors"]
    )
