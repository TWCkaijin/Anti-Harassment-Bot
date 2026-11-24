"""測試：OpenRouter Agentic RAG flow。"""

import json

import rag_core.agents.openrouter_agent as agent_module
from rag_core.agents.openrouter_agent import AgentResult, OpenRouterAgent
from rag_core.core.agent_prompts import assemble_service_instruction, get_default_prompt_sections
from rag_core.core.runtime_config import RuntimeConfig
from rag_core.rag.base import RAGDocument


def v4_result(*, guidance=None, **kwargs) -> AgentResult:
    """An agent result carrying the minimal chat contract v4 guidance."""
    return AgentResult(guidance={"contract_version": 4, **(guidance or {})}, **kwargs)


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeChoice:
    def __init__(self, message):
        self.message = message
        self.index = 0
        self.finish_reason = "stop"


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
        distance_threshold: float | None = None,
    ):
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "data_type": data_type,
                "collection_names_by_data_type": collection_names_by_data_type,
                "distance_threshold": distance_threshold,
            }
        )
        return [
            RAGDocument(
                content="申訴期限為事件發生後一年內。",
                metadata={
                    "source": "性騷擾防治法第13條",
                    "collection": "rag_documents",
                    "distance": 0.125,
                },
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


def test_service_defaults_and_admin_overrides_exclude_output_format_section():
    overrides = {
        "communication_principles": "先理解目前需求，再自然地回答。",
        "analysis_rules": "保留管理端領域參考規則。",
        "output_format": "LEGACY_ONLY_FORMAT",
    }
    original = dict(overrides)
    service = assemble_service_instruction(overrides)
    defaults = get_default_prompt_sections()
    assert overrides == original
    assert defaults["core_mission"] in service
    assert defaults["retrieval_instructions"] in service
    assert overrides["communication_principles"] in service
    assert overrides["analysis_rules"] in service
    assert "按情境參考" in service
    assert "LEGACY_ONLY_FORMAT" not in service


def test_admin_default_section_copy_does_not_mutate_future_service_prompts():
    defaults = agent_module.get_default_prompt_sections()
    defaults["communication_principles"] = "MUTATED"
    assert "MUTATED" not in assemble_service_instruction()
