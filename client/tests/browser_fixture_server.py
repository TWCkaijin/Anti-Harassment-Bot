"""Local synthetic Flask/v4 fixture; no Firebase initialization or external model.

Run ``PYTHONPATH=shared/python:client/backend python client/tests/browser_fixture_server.py``.
The real chat/health blueprints and
v4 pipeline are used with in-memory configuration, model streams and retrieval.
"""

import asyncio
import json
import math
import os
from types import SimpleNamespace

# These precede app imports so local credentials cannot leak into the fixture.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["OPENROUTER_API_KEY"] = "fixture-not-a-real-key"
os.environ["ENVIRONMENT"] = "ci"

from flask import Flask  # noqa: E402
from flask_cors import CORS  # noqa: E402

from client_app.api import health  # noqa: E402
from client_app.api.chat import chat_bp  # noqa: E402
from rag_core import chat_service as chat  # noqa: E402
from rag_core.agents import guided_v4  # noqa: E402
from rag_core.agents.openrouter_agent import OpenRouterAgent  # noqa: E402
from rag_core.core.runtime_config import RuntimeConfig  # noqa: E402
from rag_core.rag.base import RAGDocument  # noqa: E402


class FixtureStream:
    def __init__(self, content):
        self.content = content

    def __aiter__(self):
        async def chunks():
            for offset in range(0, len(self.content), 80):
                yield SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            index=0,
                            delta=SimpleNamespace(content=self.content[offset : offset + 80]),
                            finish_reason=None,
                        )
                    ]
                )
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        index=0,
                        delta=SimpleNamespace(content=None),
                        finish_reason="stop",
                    )
                ]
            )

        return chunks()

    async def close(self):
        pass


class FixtureCompletions:
    async def create(self, **kwargs):
        try:
            delay = float(os.environ.get("SYNTHETIC_MODEL_DELAY_SECONDS", "0"))
        except ValueError:
            delay = 0.0
        delay = min(5.0, max(0.0, delay)) if math.isfinite(delay) else 0.0
        if delay:
            await asyncio.sleep(delay)
        schema = kwargs["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert kwargs["extra_body"]["provider"]["require_parameters"] is True
        request = {}
        policy = {}
        for message in kwargs["messages"]:
            try:
                data = json.loads(message["content"])
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict):
                if "message" in data:
                    request = data
                policy.update(data.get("request_policy", {}))
        source = request.get("conversation_summary") or request.get("message", "合成情境")
        excerpt = source[:400]
        content = {
            "answer_units": [
                {"text": "這是本機合成測試。我會依你更正後的情況整理資訊。", "source_ids": []}
            ],
            "summary_update": {"summary": excerpt, "evidence": [excerpt]},
            "question": None,
            "suggested_replies": ["我想了解申訴管道", "我想先整理情況"],
            "action_buttons": [],
            "emotion": "未知",
            "emotion_color": "gray",
        }
        if schema["name"] == "guided_chat_v4_understanding":
            content.update(
                understanding={
                    "intent": "legal_direction",
                    "scope": "personal",
                    "evidence": [excerpt],
                    "sufficient": True,
                    "limitation": "合成測試資料",
                    "immediate_safety": False,
                },
                requires_synthesis=True,
                retrieval={"query": "合成測試查詢", "data_types": ["law", "remedy"]}
                if policy.get("rag_enabled", True)
                else None,
                answer_units=[],
                summary_update=None,
            )
            content = {"plan": content}
        elif schema["name"] != "guided_chat_v4_answer":
            raise ValueError("Synthetic browser fixture only supports v4 chat schemas")
        text = json.dumps(content, ensure_ascii=False)
        if kwargs.get("stream"):
            return FixtureStream(text)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    index=0,
                    finish_reason="stop",
                    message=SimpleNamespace(content=text, tool_calls=None),
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
            ),
            RAGDocument(
                content="本機合成管道，非正式服務資訊。",
                doc_id="synthetic-remedy",
                metadata={"source": "合成救濟管道", "collection": "rag_remedies"},
            ),
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
    health.get_runtime_config = lambda: config
    guided_v4.get_matching_scenario_scripts = lambda *args, **kwargs: ()
    guided_v4.get_scenario_scripts = lambda *args, **kwargs: ()
    app = Flask(__name__)
    CORS(app)
    app.register_blueprint(health.health_bp, url_prefix="/api/v1/health")
    app.register_blueprint(chat_bp, url_prefix="/api/v1/chat")
    return app


if __name__ == "__main__":
    configure().run(
        host="127.0.0.1", port=int(os.environ.get("SYNTHETIC_PORT", "5055")), debug=False
    )
