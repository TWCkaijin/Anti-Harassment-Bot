"""測試：OpenRouter Agentic RAG flow。"""

import json

import pytest

import backend.app.agents.openrouter_agent as agent_module
from backend.app.agents.openrouter_agent import OpenRouterAgent
from backend.app.core.runtime_config import RuntimeConfig
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

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        data_type: str = "law",
        collection_names_by_data_type=None,
    ):
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "data_type": data_type,
                "collection_names_by_data_type": collection_names_by_data_type,
            }
        )
        return [
            RAGDocument(
                content="申訴期限為事件發生後一年內。",
                metadata={"source": "性騷擾防治法第13條", "collection": "rag_documents"},
                doc_id="law-13",
            )
        ]


def make_agent(completions):
    agent = object.__new__(OpenRouterAgent)
    agent.model = "test/model"
    agent.client = FakeClient(completions)
    agent.rag = FakeRAG()
    return agent


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


@pytest.fixture(autouse=True)
def disable_scenario_script_firestore_reads(monkeypatch):
    monkeypatch.setattr(agent_module, "get_matching_scenario_scripts", lambda user_message: ())


@pytest.mark.asyncio
async def test_agent_returns_without_tool_call(monkeypatch):
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
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())

    result = await agent.run("我有點害怕", use_rag=True)

    assert result.reply.endswith('"}')
    assert result.rag_used is False
    assert result.sources == []
    assert "tools" in completions.calls[0]
    assert completions.calls[0]["temperature"] == 0.2
    assert completions.calls[0]["top_p"] == 1.0
    assert completions.calls[0]["max_tokens"] == 1200
    assert completions.calls[0]["response_format"]["type"] == "json_schema"


@pytest.mark.asyncio
async def test_agent_omits_max_tokens_when_runtime_config_is_unlimited(monkeypatch):
    completions = FakeCompletions(
        [FakeResponse(FakeMessage(content='{"emotion":"冷靜"}', tool_calls=None))]
    )
    agent = make_agent(completions)
    monkeypatch.setattr(
        agent_module, "get_runtime_config", lambda: fake_runtime_config(max_tokens=0)
    )

    await agent.run("測試", use_rag=False)

    assert "max_tokens" not in completions.calls[0]


@pytest.mark.asyncio
async def test_agent_passes_runtime_reasoning_effort_to_openrouter(monkeypatch):
    completions = FakeCompletions(
        [FakeResponse(FakeMessage(content='{"emotion":"冷靜"}', tool_calls=None))]
    )
    agent = make_agent(completions)
    monkeypatch.setattr(
        agent_module,
        "get_runtime_config",
        lambda: fake_runtime_config(reasoning_effort="high"),
    )

    await agent.run("測試", use_rag=False)

    assert completions.calls[0]["extra_body"] == {"reasoning": {"effort": "high", "exclude": True}}


@pytest.mark.asyncio
async def test_agent_uses_firestore_prompt_sections(monkeypatch):
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
    monkeypatch.setattr(
        agent_module,
        "get_runtime_config",
        lambda: fake_runtime_config(agent_prompt_sections={"language": "第一行\n第二行"}),
    )

    await agent.run("測試 prompt", use_rag=False)

    assert completions.calls[0]["messages"][0] == {
        "role": "system",
        "content": agent_module._assemble_system_instruction({"language": "第一行\n第二行"}),
    }
    assert "tools" not in completions.calls[0]


def test_firestore_sections_override_local_prompt_defaults():
    runtime_config = fake_runtime_config(
        agent_prompt_sections={"language": "Firestore language rules"},
    )

    instruction = agent_module._get_system_instruction(runtime_config)

    assert "## 語言\nFirestore language rules" in instruction
    assert "## 你的核心使命" in instruction


def test_missing_firestore_sections_use_built_in_prompt():
    instruction = agent_module._get_system_instruction(fake_runtime_config())

    assert instruction == agent_module._DEFAULT_SYSTEM_INSTRUCTION


@pytest.mark.asyncio
async def test_agent_tool_call_returns_sources(monkeypatch):
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
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())

    result = await agent.run("性騷擾申訴期限多久？", use_rag=True)

    assert result.rag_used is True
    assert result.sources == [
        {
            "label": "性騷擾防治法第13條",
            "type": "law",
            "collection": "rag_documents",
            "doc_id": "law-13",
        }
    ]
    assert len(completions.calls) == 2
    assert completions.calls[0]["temperature"] == completions.calls[1]["temperature"]
    assert completions.calls[0]["top_p"] == completions.calls[1]["top_p"]
    assert completions.calls[0]["max_tokens"] == completions.calls[1]["max_tokens"]
    assert "extra_body" not in completions.calls[0]
    assert "extra_body" not in completions.calls[1]
    assert agent.rag.calls[0]["data_type"] == "law"
    assert agent.rag.calls[0]["top_k"] == 3
    assert agent.rag.calls[0]["collection_names_by_data_type"] == {
        "law": "rag_documents",
        "judgment": "rag_judgments",
        "remedy": "rag_remedies",
    }
    assert result.tool_calls == [
        {
            "name": "retrieve_harassment_knowledge",
            "arguments": {"query": "申訴期限", "data_type": "law"},
            "result_count": 1,
        }
    ]
    tool_message = completions.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert "[參考資料 - 性騷擾防治法第13條]" in tool_message["content"]


@pytest.mark.asyncio
async def test_agent_tool_call_passes_judgment_data_type(monkeypatch):
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
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())

    await agent.run("請查詢這個案件的相關資料", use_rag=True)

    assert agent.rag.calls[0]["data_type"] == "judgment"


@pytest.mark.asyncio
async def test_agent_never_falls_back_to_user_message_for_missing_tool_query(monkeypatch):
    invalid_tool_call = FakeToolCall("placeholder", data_type="all")
    invalid_tool_call.function.arguments = json.dumps({"data_type": "all"})
    completions = FakeCompletions([FakeResponse(FakeMessage(tool_calls=[invalid_tool_call]))])
    agent = make_agent(completions)
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())

    with pytest.raises(ValueError, match="Tool call query must be a non-empty string"):
        await agent.run("使用者原始問題不得成為檢索 query", use_rag=True)

    assert agent.rag.calls == []


@pytest.mark.asyncio
async def test_agent_uses_model_tool_call_for_past_case_questions(monkeypatch):
    completions = FakeCompletions(
        [
            FakeResponse(
                FakeMessage(tool_calls=[FakeToolCall("性騷擾判決案例與準備資料", data_type="all")])
            ),
            FakeResponse(
                FakeMessage(
                    content='{"emotion":"冷靜","emotion_color":"green","reply":"先整理證據。"}',
                    tool_calls=None,
                )
            ),
        ]
    )
    agent = make_agent(completions)
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())

    result = await agent.run("我下一步該做什麼？根據過往歷史經驗，我要準備什麼？")

    assert result.rag_used is True
    assert agent.rag.calls[0]["data_type"] == "all"
    assert agent.rag.calls[0]["query"] == "性騷擾判決案例與準備資料"
    assert len(completions.calls) == 2
    assert "tools" in completions.calls[0]


@pytest.mark.asyncio
async def test_agent_use_rag_false_does_not_send_tools(monkeypatch):
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
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())

    result = await agent.run("不要查資料", use_rag=False)

    assert result.rag_used is False
    assert "tools" not in completions.calls[0]
    assert "tool_choice" not in completions.calls[0]


@pytest.mark.asyncio
async def test_agent_openrouter_error_returns_fallback(monkeypatch):
    class FailingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("network down")

    agent = make_agent(FailingCompletions())
    monkeypatch.setattr(agent_module, "get_runtime_config", lambda: fake_runtime_config())

    with pytest.raises(RuntimeError, match="network down"):
        await agent.run("你好", use_rag=True)
