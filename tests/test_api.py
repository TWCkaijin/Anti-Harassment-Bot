"""測試：Flask API endpoints。"""

import base64
import json
from unittest.mock import Mock
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


@pytest.fixture(autouse=True)
def isolate_endpoint_tests_from_process_rate_limits(monkeypatch):
    # These tests exercise payloads and responses; dedicated security tests
    # cover rate limits without counters leaking between endpoint test cases.
    monkeypatch.setattr(app.extensions["chat_request_security"], "rate_limit_enabled", False)


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


def test_health_check(monkeypatch):
    from backend.app.api import health

    monkeypatch.setattr(health, "get_runtime_config", fake_runtime_config)
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


def test_chat_returns_retryable_error_for_invalid_model_schema(monkeypatch, caplog):
    caplog.set_level("ERROR")

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
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "chat_request_failed"
    )
    assert failure.levelname == "ERROR"
    assert failure.error_type == "ValidationError"
    assert failure.exc_info is not None
    assert "缺少必要欄位" not in caplog.text
    assert "input_value" not in caplog.text


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


def test_chat_returns_retryable_error_for_transient_upstream_failure(monkeypatch, caplog):
    caplog.set_level("ERROR")

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
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "chat_request_failed"
    )
    assert failure.error_message == "upstream timed out"
    assert failure.error_type == "TimeoutError"
    assert failure.http_status == 502
    assert failure.exc_info is not None


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


@pytest.mark.parametrize("path", ["/api/v1/chat/", "/v1/chat/"])
@pytest.mark.parametrize("version", [1, 2, 3, 4])
@pytest.mark.parametrize("stream", [False, True])
def test_chat_passes_enabled_images_to_agent_for_every_contract(monkeypatch, path, version, stream):
    captured = []

    class FakeAgent:
        async def run(self, **kwargs):
            captured.append(kwargs)
            guidance = {}
            if version == 4:
                guidance = {
                    "contract_version": 4,
                    "context_revision": 0,
                    "summary_update": None,
                    "clarification": None,
                }
            elif version in (2, 3):
                guidance = {
                    "contract_version": version,
                    "facts_revision": 0,
                    "fact_updates": [],
                    "clarification": None,
                    "answer_sections": [],
                }
            return AgentResult(
                reply='{"emotion":"未知","emotion_color":"gray","reply":"已收到測試圖片。","suggested_replies":["整理圖片內容","了解下一步"]}',
                guidance=guidance,
            )

    monkeypatch.setattr(chat_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(chat_module, "get_agent", FakeAgent)

    response = app.test_client().post(
        path,
        json={
            "message": "",
            "history": [],
            "image_base64": VALID_PNG_DATA_URL,
            "contract_version": version,
            "stream": stream,
        },
    )

    assert response.status_code == 200
    if stream:
        body = response.get_data(as_text=True)
        assert "event: done\n" in body
        assert "event: error\n" not in body
    else:
        assert response.json["reply"] == "已收到測試圖片。"
    assert len(captured) == 1
    assert captured[0]["user_message"] == ""
    assert captured[0]["image_base64"] == VALID_PNG_DATA_URL
    assert captured[0]["runtime_config"].enable_image_upload is True


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
        "",
        False,
        {"url": "https://example.test/private.png"},
        "not-a-data-url",
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        "data:image/png;base64,bm90LWEtcG5n",
        "data:image/png;base64,***",
    ],
)
def test_chat_rejects_invalid_image_data(monkeypatch, image_data_url):
    get_config = Mock(side_effect=AssertionError("invalid image must not load config"))
    get_agent = Mock(side_effect=AssertionError("invalid image must not reach agent"))
    monkeypatch.setattr(chat_module, "get_runtime_config", get_config)
    monkeypatch.setattr(chat_module, "get_agent", get_agent)
    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "", "history": [], "image_base64": image_data_url},
    )

    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_request"
    get_config.assert_not_called()
    get_agent.assert_not_called()


def test_chat_schema_accepts_optional_image_string_and_forbids_extra_history_fields():
    schema = chat_module.ChatRequest.model_json_schema()
    assert schema["properties"]["image_base64"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert "image_base64" not in schema.get("required", [])
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["MessageItem"]["additionalProperties"] is False


@pytest.mark.parametrize("version", [1, 2, 3, 4])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("client_privacy_review", [False, True])
def test_chat_rejects_disabled_images_independently_of_review_and_text_masking(
    monkeypatch, version, stream, client_privacy_review
):
    monkeypatch.setattr(
        chat_module,
        "get_runtime_config",
        lambda: fake_runtime_config(
            enable_image_upload=False,
            enable_client_privacy_review=client_privacy_review,
            enable_anonymization=False,
            pipeline={"mask_message": False, "mask_case_context": False},
        ),
    )
    monkeypatch.setattr(
        chat_module,
        "get_agent",
        lambda: (_ for _ in ()).throw(AssertionError("agent must not be initialized")),
    )

    response = app.test_client().post(
        "/api/v1/chat/",
        json={
            "message": "圖片內容",
            "history": [],
            "image_base64": VALID_PNG_DATA_URL,
            "contract_version": version,
            "stream": stream,
        },
    )

    assert response.status_code == 422
    assert response.is_json
    assert response.get_json()["code"] == "image_upload_disabled"
    assert response.get_json()["retryable"] is False


@pytest.mark.parametrize(
    ("mime", "signature"),
    [
        ("image/jpeg", b"\xff\xd8\xff"),
        ("image/png", b"\x89PNG\r\n\x1a\n"),
        ("image/gif", b"GIF87a"),
        ("image/gif", b"GIF89a"),
        ("image/webp", b"RIFF\x00\x00\x00\x00WEBP"),
    ],
)
def test_image_validator_checks_supported_mime_signatures(mime, signature):
    value = f"data:{mime};base64," + base64.b64encode(signature + b"synthetic").decode("ascii")
    request = chat_module.ChatRequest(image_base64=value)
    assert request.image_base64 == value
    mismatched = f"data:{mime};base64," + base64.b64encode(b"not-an-image").decode("ascii")
    with pytest.raises(ValidationError, match="does not match its MIME type"):
        chat_module.ChatRequest(image_base64=mismatched)


def test_image_validator_accepts_five_mib_and_rejects_larger_decoded_image():
    content = b"\x89PNG\r\n\x1a\n" + b"x" * (chat_module.MAX_IMAGE_BYTES - 8)
    value = "data:image/png;base64," + base64.b64encode(content).decode("ascii")
    assert chat_module.ChatRequest(image_base64=value).image_base64 == value
    oversized = "data:image/png;base64," + base64.b64encode(content + b"x").decode("ascii")
    with pytest.raises(ValidationError, match="decoded size must be at most"):
        chat_module.ChatRequest(image_base64=oversized)


def test_request_body_limit_accepts_five_mib_image_with_maximum_text_message(monkeypatch):
    content = b"\x89PNG\r\n\x1a\n" + b"x" * (chat_module.MAX_IMAGE_BYTES - 8)
    image = "data:image/png;base64," + base64.b64encode(content).decode("ascii")
    payload = {"message": "合" * chat_module.USER_MESSAGE_MAX_LENGTH, "image_base64": image}
    assert len(json.dumps(payload).encode("utf-8")) < app.config["MAX_CONTENT_LENGTH"]
    captured = []

    class FakeAgent:
        async def run(self, **kwargs):
            captured.append(kwargs)
            return AgentResult(
                reply='{"emotion":"未知","emotion_color":"gray","reply":"已收到測試圖片。","suggested_replies":["整理圖片內容","了解下一步"]}'
            )

    monkeypatch.setattr(chat_module, "get_runtime_config", fake_runtime_config)
    monkeypatch.setattr(chat_module, "get_agent", FakeAgent)
    response = app.test_client().post("/api/v1/chat/", json=payload)
    assert response.status_code == 200
    assert captured[0]["image_base64"] == image
    assert captured[0]["user_message"] == payload["message"]


@pytest.mark.parametrize("version", [1, 2, 3, 4])
@pytest.mark.parametrize("image_field", [{}, {"image_base64": None}])
def test_text_only_versions_keep_omitted_or_null_image_compatibility(version, image_field):
    request_model = chat_module.ChatRequest(
        message="合成文字需求", contract_version=version, **image_field
    )
    arguments, _ = chat_module._prepare_agent_input(
        request_model, fake_runtime_config(enable_image_upload=True)
    )
    assert arguments["user_message"] == "合成文字需求"
    assert arguments["image_base64"] is None


@pytest.mark.parametrize("path", ["/api/v1/chat/", "/v1/chat/"])
def test_chat_returns_explicit_maintenance_response_without_initializing_agent(
    monkeypatch, caplog, path
):
    caplog.set_level("ERROR")
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
        path,
        json={"message": "private-chat-do-not-log", "history": []},
    )

    assert response.status_code == 503
    assert response.get_json() == {
        "code": "maintenance",
        "detail": "系統維護中，請稍後再試。",
        "retryable": False,
    }
    summary = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_error_response"
    )
    assert summary.levelname == "ERROR"
    assert summary.http_status == 503
    assert summary.error_code == "maintenance"
    assert summary.error_detail == "系統維護中，請稍後再試。"
    assert summary.request_path == path
    assert summary.request_method == "POST"
    assert "系統維護中，請稍後再試。" in summary.getMessage()
    assert "private-chat-do-not-log" not in caplog.text


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
                {"role": "assistant", "content": "a" * 6000}
                for _ in range(chat_module.MAX_HISTORY_CHARACTERS // 6000 + 1)
            ],
        },
    )

    assert response.status_code == 422
    assert response.get_json()["code"] == "invalid_request"
    assert any(
        error["field"] == "" and "history content" in error["message"]
        for error in response.get_json()["errors"]
    )
