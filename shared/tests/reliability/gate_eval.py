"""Opt-in synthetic pre/post Gate evaluation; dry run makes zero model calls."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from statistics import median
from time import monotonic

CASES = Path(__file__).with_name("gate_cases.json")


def summarize(rows):
    allowed = [row for row in rows if row["expected"] == "allow"]
    denied = [row for row in rows if row["expected"] == "deny"]
    return {
        "cases": len(rows),
        "false_denials": sum(row["decision"] == "deny" for row in allowed),
        # Gate errors fail closed in the chat service; count them separately.
        "missed_denials": sum(row["decision"] == "allow" for row in denied),
        "unavailable": sum(row["error_type"] is not None for row in rows),
        "median_latency_ms": median(row["latency_ms"] for row in rows) if rows else 0,
        "max_latency_ms": max((row["latency_ms"] for row in rows), default=0),
    }


def validate_cases(cases):
    if not isinstance(cases, list) or not cases:
        raise ValueError("Gate cases must be a non-empty array")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or case.get("id") in seen:
            raise ValueError("Gate case IDs must be unique")
        seen.add(case["id"])
        if case.get("stage") not in {"pre", "post"}:
            raise ValueError("Gate case stage must be pre or post")
        if case.get("expected") not in {"allow", "deny"}:
            raise ValueError("Gate case expected decision must be allow or deny")
        if not isinstance(case.get("message"), str) or not isinstance(case.get("history"), list):
            raise ValueError("Gate case needs a message and history")
        if case["stage"] == "post" and not isinstance(case.get("candidate"), dict):
            raise ValueError("Post Gate case needs a candidate")


async def evaluate(cases, model, timeout, prompt, post_prompt=None):
    from rag_core.agents.gate_agent import GateAgent
    from rag_core.core.runtime_config import RuntimeConfig

    post_prompt = prompt if post_prompt is None else post_prompt
    config = RuntimeConfig(
        openrouter_model=model,
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0,
        top_p=1,
        max_tokens=1200,
        gate_enabled=True,
        gate_model=model,
        gate_prompt=prompt,
        gate_post_prompt=post_prompt,
        gate_timeout_seconds=timeout,
    )
    rows = []
    for case in cases:
        started = monotonic()
        decision, error_type = "error", None
        try:
            async with asyncio.timeout(timeout):
                result = await GateAgent().run(
                    stage=case["stage"],
                    user_message=case["message"],
                    history=case["history"],
                    case_context={},
                    candidate=case.get("candidate"),
                    runtime_config=config,
                )
            decision = result.decision
        except Exception as exc:
            error_type = type(exc).__name__
        rows.append(
            {
                "id": case["id"],
                "stage": case["stage"],
                "expected": case["expected"],
                "decision": decision,
                "error_type": error_type,
                "latency_ms": round((monotonic() - started) * 1000, 2),
            }
        )
    return {
        "model": model,
        "policy_hash": hashlib.sha256(prompt.encode()).hexdigest(),
        "policy_hashes": {
            "pre": hashlib.sha256(prompt.encode()).hexdigest(),
            "post": hashlib.sha256(post_prompt.encode()).hexdigest(),
        },
        "summary": summarize(rows),
        "by_stage": {
            stage: summarize([row for row in rows if row["stage"] == stage])
            for stage in ("pre", "post")
        },
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Send packaged synthetic cases to OpenRouter (billable)"
    )
    parser.add_argument("--model", help="Explicit OpenRouter model ID; required for live runs")
    parser.add_argument("--cases-file", type=Path, default=CASES, help="Synthetic case JSON file")
    parser.add_argument("--case", help="Run one synthetic case by ID")
    parser.add_argument(
        "--env-file", type=Path, help="Load a local model key without displaying it"
    )
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument(
        "--prompt-file", type=Path, help="Legacy policy text to use for both stages"
    )
    parser.add_argument(
        "--post-prompt-file", type=Path, help="Optional post-stage policy text only"
    )
    parser.add_argument("--output", type=Path, help="Save metadata/results without case text")
    args = parser.parse_args()
    cases = json.loads(args.cases_file.read_text())
    validate_cases(cases)
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
        if not cases:
            parser.error("Unknown synthetic case ID")
    if not args.live:
        print(
            json.dumps(
                {
                    "cases": len(cases),
                    "by_stage": {
                        stage: sum(case["stage"] == stage for case in cases)
                        for stage in ("pre", "post")
                    },
                    "model_calls": 0,
                    "mode": "dry_run",
                }
            )
        )
        return
    if not args.model or not 1 <= args.timeout <= 60:
        parser.error("--live requires --model and timeout between 1 and 60")
    if args.env_file:
        from dotenv import load_dotenv

        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        load_dotenv(args.env_file, override=False)
    from rag_core.core.gate_policy import DEFAULT_GATE_POST_PROMPT, DEFAULT_GATE_PROMPT

    prompt = args.prompt_file.read_text() if args.prompt_file else DEFAULT_GATE_PROMPT
    post_prompt = (
        args.post_prompt_file.read_text()
        if args.post_prompt_file
        else prompt
        if args.prompt_file
        else DEFAULT_GATE_POST_PROMPT
    )
    if any(not value.strip() or len(value) > 20000 for value in (prompt, post_prompt)):
        parser.error("prompts must be non-empty and at most 20000 characters")
    report = asyncio.run(evaluate(cases, args.model, args.timeout, prompt, post_prompt))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
