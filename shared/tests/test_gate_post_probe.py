"""Keep the post-only model comparison focused on two false denials and controls."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from rag_core.agents.gate_agent import GateAgent, GateDecision
from rag_core.core.gate_policy import DEFAULT_GATE_POST_PROMPT
from shared.tests.reliability.gate_eval import evaluate, validate_cases

PROBE = Path(__file__).with_name("reliability") / "gate_post_probe_cases.json"
PROMPT = Path(__file__).with_name("reliability") / "gate_post_prompt_candidate.txt"


def test_post_probe_keeps_both_false_denials_and_a_technical_counterexample():
    cases = json.loads(PROBE.read_text())
    validate_cases(cases)
    assert len(cases) == 9
    assert all(case["stage"] == "post" for case in cases)
    assert sum(case["expected"] == "allow" for case in cases) == 5
    assert sum(case["expected"] == "deny" for case in cases) == 4

    by_id = {case["id"]: case for case in cases}
    structured = by_id["post-law-source-uncertainty-structured"]
    reply_only = by_id["post-law-source-uncertainty-reply-only"]
    technical = deepcopy(by_id["post-law-source-technical-analysis"])
    assert (
        structured["candidate"]["visible_payload"]["reply"]
        == reply_only["candidate"]["visible_payload"]["reply"]
    )
    technical["id"] = structured["id"]
    technical["expected"] = structured["expected"]
    technical["candidate"]["visible_payload"]["analysis"][1]["summary"] = structured["candidate"][
        "visible_payload"
    ]["analysis"][1]["summary"]
    assert technical == structured


def test_live_probe_prompt_matches_the_runtime_post_default():
    assert PROMPT.read_text() == DEFAULT_GATE_POST_PROMPT


async def test_evaluator_uses_and_reports_distinct_stage_prompts(monkeypatch):
    received = []

    async def fake_run(self, **kwargs):
        stage = kwargs["stage"]
        received.append(kwargs["runtime_config"].gate_prompt_for_stage(stage))
        return GateDecision(decision="allow", reason_code="allowed")

    monkeypatch.setattr(GateAgent, "run", fake_run)
    cases = [
        {"id": "pre", "stage": "pre", "message": "合成查證", "history": [], "expected": "allow"},
        {
            "id": "post",
            "stage": "post",
            "message": "合成查證",
            "history": [],
            "candidate": {"visible_payload": {"reply": "請核對官方資料"}, "published_events": []},
            "expected": "allow",
        },
    ]

    report = await evaluate(cases, "offline/model", 1, "pre policy", "post policy")

    assert received == ["pre policy", "post policy"]
    assert report["policy_hashes"] == {
        "pre": hashlib.sha256(b"pre policy").hexdigest(),
        "post": hashlib.sha256(b"post policy").hexdigest(),
    }
    assert report["summary"]["false_denials"] == 0
    assert report["summary"]["missed_denials"] == 0
