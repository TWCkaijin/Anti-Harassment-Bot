"""測試：OpenRouter Agentic RAG flow。"""

import json

import pytest

from backend.app.agents.openrouter_agent import OpenRouterAgent
from backend.app.rag.base import RAGDocument


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message


class FakeResponse:
    def __init__(self, message):
        self.choices = [FakeChoice(message)]


class FakeFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, query: str, data_type: str = "law"):
        self.id = "tool-1"
        self.function = FakeFunction(
            "retrieve_harassment_knowledge",
            json.dumps({"query": query, "data_type": data_type}, ensure_ascii=False),
        )


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeChat:
    def __init__(self, completions):
        self.completions = completions


class FakeClient:
    def __init__(self, completions):
        self.chat = FakeChat(completions)


class FakeRAG:
    def __init__(self):
        self.calls = []

    async def retrieve(self, query: str, top_k: int = 5, data_type: str = "law"):
        self.calls.append({"query": query, "top_k": top_k, "data_type": data_type})
        return [
            RAGDocument(
                content="申訴期限為事件發生後一年內。",
                metadata={"source": "性騷擾防治法第13條"},
                doc_id="law-13",
            )
        ]


def make_agent(completions):
    agent = object.__new__(OpenRouterAgent)
    agent.model = "test/model"
    agent.client = FakeClient(completions)
    agent.rag = FakeRAG()
    return agent


@pytest.mark.asyncio
async def test_agent_returns_without_tool_call():
    completions = FakeCompletions(
        [
            FakeResponse(
                FakeMessage(
                    content='{"emotion":"冷靜","emotion_color":"green","reply":"我在。"}',
                    tool_calls=None,
                )
            )
        ]
    )
    agent = make_agent(completions)

    result = await agent.run("我有點害怕", use_rag=True)

    assert result.reply.endswith('"}')
    assert result.rag_used is False
    assert result.sources == []
    assert "tools" in completions.calls[0]


@pytest.mark.asyncio
async def test_agent_tool_call_returns_sources():
    completions = FakeCompletions(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("申訴期限")])),
            FakeResponse(
                FakeMessage(
                    content='{"emotion":"焦慮","emotion_color":"yellow","reply":"申訴期限通常是一年。"}',
                    tool_calls=None,
                )
            ),
        ]
    )
    agent = make_agent(completions)

    result = await agent.run("性騷擾申訴期限多久？", use_rag=True)

    assert result.rag_used is True
    assert result.sources == ["性騷擾防治法第13條"]
    assert len(completions.calls) == 2
    assert agent.rag.calls[0]["data_type"] == "law"
    tool_message = completions.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "[參考資料 - 性騷擾防治法第13條]" in tool_message["content"]


@pytest.mark.asyncio
async def test_agent_tool_call_passes_judgment_data_type():
    completions = FakeCompletions(
        [
            FakeResponse(FakeMessage(tool_calls=[FakeToolCall("類似判決", data_type="judgment")])),
            FakeResponse(
                FakeMessage(
                    content='{"emotion":"冷靜","emotion_color":"green","reply":"找到相近判決。"}',
                    tool_calls=None,
                )
            ),
        ]
    )
    agent = make_agent(completions)

    await agent.run("有沒有類似判決？", use_rag=True)

    assert agent.rag.calls[0]["data_type"] == "judgment"


@pytest.mark.asyncio
async def test_agent_use_rag_false_does_not_send_tools():
    completions = FakeCompletions(
        [
            FakeResponse(
                FakeMessage(
                    content='{"emotion":"未知","emotion_color":"gray","reply":"好的。"}',
                    tool_calls=None,
                )
            )
        ]
    )
    agent = make_agent(completions)

    result = await agent.run("不要查資料", use_rag=False)

    assert result.rag_used is False
    assert "tools" not in completions.calls[0]
    assert "tool_choice" not in completions.calls[0]


@pytest.mark.asyncio
async def test_agent_openrouter_error_returns_fallback():
    class FailingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("network down")

    agent = make_agent(FailingCompletions())

    result = await agent.run("你好", use_rag=True)

    assert result.rag_used is False
    assert result.sources == []
    assert "系統目前無法連線至 AI 引擎" in result.reply
