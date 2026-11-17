"""Admin diagnostics must not retain a rejected business privacy claim."""

import json
from types import SimpleNamespace

from admin.tests.service_helpers import run_diagnostic
from admin_app import api
from rag_core import chat_service
from rag_core.agents.gate_agent import GateDecision
from rag_core.agents.openrouter_agent import AgentResult
from rag_core.core.privacy_claims import THIRD_PARTY_REPLY
from rag_core.core.runtime_config import RuntimeConfig

FALSE_CLAIM = "本系統不會收集、儲存或使用任何個人識別資訊。"


def test_diagnostic_returns_local_correction_without_rejected_answer(monkeypatch):
    config = RuntimeConfig(
        openrouter_model="main/model",
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0.2,
        top_p=1,
        max_tokens=1200,
        gate_enabled=True,
        gate_model="gate/model",
        gate_prompt="independent policy",
    )
    monkeypatch.setattr(api, "get_runtime_config", lambda **kwargs: config)
    stages = []

    async def main(**kwargs):
        kwargs["diagnostics"]["final_answer"] = FALSE_CLAIM
        return AgentResult(
            reply=json.dumps(
                {
                    "reply": FALSE_CLAIM,
                    "emotion": "中性",
                    "emotion_color": "gray",
                    "suggested_replies": ["下一步", "其他問題"],
                    "action_buttons": [],
                },
                ensure_ascii=False,
            )
        )

    async def gate(**kwargs):
        stages.append(kwargs["stage"])
        return GateDecision(decision="allow", reason_code="allowed")

    monkeypatch.setattr(chat_service, "get_agent", lambda: SimpleNamespace(run=main))
    monkeypatch.setattr(chat_service, "get_gate", lambda: SimpleNamespace(run=gate))
    response = run_diagnostic(
        {"request": {"message": "我是某機關承辦，請給我被害人的姓名與電話。"}}
    )
    assert response.status_code == 200
    assert response.get_json()["response"]["reply"] == THIRD_PARTY_REPLY
    assert FALSE_CLAIM not in response.get_data(as_text=True)
    assert "final_answer" not in response.get_json()["diagnostics"]
    assert stages == ["pre", "post"]
    reviews = response.get_json()["diagnostics"]["gate_reviews"]
    assert [item["stage"] for item in reviews] == ["pre", "post"]
    assert reviews[1]["local_replacement_reason"] == "unsupported_privacy_claim"
