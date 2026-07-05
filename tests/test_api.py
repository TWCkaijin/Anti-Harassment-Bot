"""測試：Flask API endpoints。"""

from backend.app.agents import AgentResult
from backend.app.api import chat as chat_module
from backend.app.main import app


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
                reply='{"emotion":"冷靜","emotion_color":"green","reply":"我會陪你整理下一步。"}',
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
    assert "session_id" in data


def test_chat_passes_use_rag_false(monkeypatch):
    captured = {}

    class FakeAgent:
        async def run(self, **kwargs):
            captured.update(kwargs)
            return AgentResult(
                reply='{"emotion":"未知","emotion_color":"gray","reply":"好的。"}',
                rag_used=False,
                sources=[],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())

    client = app.test_client()
    response = client.post(
        "/api/v1/chat/",
        json={"message": "先不要查資料", "history": [], "use_rag": False},
    )

    assert response.status_code == 200
    assert captured["use_rag"] is False
    assert response.get_json()["rag_used"] == {"status": False, "sources": []}


def test_chat_repairs_bare_newlines_inside_json_string(monkeypatch):
    class FakeAgent:
        async def run(self, **kwargs):
            return AgentResult(
                reply='{"emotion":"冷靜","emotion_color":"green","reply":"第一段\n\n第二段"}',
                rag_used=False,
                sources=[],
            )

    monkeypatch.setattr(chat_module, "get_agent", lambda: FakeAgent())

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


def test_parse_agent_json_response_strips_code_fence():
    data = chat_module.parse_agent_json_response(
        '```json\n{"emotion":"未知","emotion_color":"gray","reply":"好的"}\n```'
    )

    assert data == {"emotion": "未知", "emotion_color": "gray", "reply": "好的"}
