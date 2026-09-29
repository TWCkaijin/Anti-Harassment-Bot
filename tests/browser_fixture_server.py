"""Local synthetic browser fixture using the real Flask/v2 pipeline.

Run with ``python -m tests.browser_fixture_server``. No model or Firestore request
is made: dependencies below are replaced before serving. Never deploy this file.
"""

import json
from types import SimpleNamespace

from backend.app.agents import guided_chat
from backend.app.agents.openrouter_agent import OpenRouterAgent
from backend.app.api import chat
from backend.app.core.runtime_config import RuntimeConfig
from backend.app.main import app
from backend.app.rag.base import RAGDocument


class FixtureCompletions:
    async def create(self, **kwargs):
        content = {
            "answer_sections": [
                {
                    "kind": "direction",
                    "text": "這是本機合成測試。我會依你更正後的情況整理資訊。",
                    "source_ids": [],
                },
                {
                    "kind": "basis",
                    "text": "測試資料尚未經法律專家覆核，不能據此確定個案法律方向。",
                    "source_ids": [],
                },
                {
                    "kind": "next_steps",
                    "text": "你可以修改摘要、暫不提供資訊，或繼續說明希望取得的協助。",
                    "source_ids": [],
                },
            ],
            "fact_updates": [],
            "question_fact": None,
            "suggested_replies": ["我想了解申訴管道", "我想先整理情況"],
            "action_buttons": [],
            "emotion": "未知",
            "emotion_color": "gray",
        }
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(content, ensure_ascii=False), tool_calls=None
                    )
                )
            ]
        )


class FixtureRAG:
    async def retrieve(self, query, **kwargs):
        return [
            RAGDocument(
                content="本機合成測試資料，非法律依據。",
                doc_id="synthetic",
                metadata={"source": "合成資料", "collection": "rag_documents"},
            )
        ]


def configure():
    config = RuntimeConfig(
        openrouter_model="fixture/no-network",
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0.2,
        top_p=1,
        max_tokens=1200,
        rag_collections={
            "law": "rag_documents",
            "judgment": "rag_judgments",
            "remedy": "rag_remedies",
        },
    )
    agent = object.__new__(OpenRouterAgent)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=FixtureCompletions()))
    agent.rag = FixtureRAG()
    chat.get_agent = lambda: agent
    chat.get_runtime_config = lambda: config
    guided_chat.get_runtime_config = lambda: config
    guided_chat.get_matching_scenario_scripts = lambda *args, **kwargs: ()


if __name__ == "__main__":
    configure()
    app.run(host="127.0.0.1", port=5055, debug=False)
