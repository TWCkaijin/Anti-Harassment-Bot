"""測試：Flask API endpoints。"""

from backend.app.agents import AgentResult
from backend.app.api import chat as chat_module
from backend.app.core.runtime_config import RuntimeConfig
from backend.app.main import app


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


def test_chat_hides_retryable_error_diagnostics_outside_development_mode(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            raise RuntimeError("upstream model rejected the JSON schema")

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())
    monkeypatch.setattr(chat_module, "get_runtime_config", lambda: fake_runtime_config())

    response = app.test_client().post(
        "/api/v1/chat/",
        json={"message": "測試 schema", "history": [], "use_rag": False},
    )

    assert response.status_code == 502
    assert response.get_json() == {
        "detail": "伺服器回傳錯誤，正在重試中",
        "retryable": True,
    }
