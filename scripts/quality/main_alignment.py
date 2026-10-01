"""Compare immutable engine snapshots with synthetic cases and frozen sources.

This intentionally bypasses HTTP, Firestore and embeddings. It measures agent
output and validated display deltas, not browser paint or production retrieval.
Provider reasoning and credentials are never persisted. Live calls are opt-in.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import fields
from pathlib import Path
from time import monotonic
from types import SimpleNamespace


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=lambda item: item.model_dump() if hasattr(item, "model_dump") else str(item),
        ).encode()
    ).hexdigest()


class RecordedStream:
    def __init__(self, stream, record):
        self.stream, self.record = stream, record

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        async for chunk in self.stream:
            for choice in chunk.choices:
                if choice.index == 0:
                    self.record["content"] += choice.delta.content or ""
                    if choice.finish_reason:
                        self.record["finish_reason"] = choice.finish_reason
            yield chunk

    async def close(self):
        await self.stream.close()


class RecordedClient:
    def __init__(self, client):
        self.client = client
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        record = {"content": "", "request_sha256": digest(kwargs), "finish_reason": None}
        self.calls.append(record)
        response = await self.client.chat.completions.create(**kwargs)
        if kwargs.get("stream"):
            return RecordedStream(response, record)
        record["content"] = response.choices[0].message.content or ""
        record["finish_reason"] = response.choices[0].finish_reason
        return response


async def execute(args):
    # Snapshot imports must precede the current checkout, even when called there.
    sys.path.insert(0, str(args.engine.resolve()))
    from dotenv import load_dotenv

    load_dotenv(args.env_file, override=False)
    from openai import AsyncOpenAI

    from backend.app.agents import openrouter_agent as module
    from backend.app.core.chat_response import AssistantChatResponse
    from backend.app.core.runtime_config import RuntimeConfig
    from backend.app.rag.base import RAGDocument

    if not Path(module.__file__).resolve().is_relative_to(args.engine.resolve()):
        raise RuntimeError("Engine imports escaped the requested snapshot")
    engine_files = {
        str(path.relative_to(args.engine)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(args.engine.rglob("*.py"))
    }
    engine_sha256 = hashlib.sha256(json.dumps(engine_files, sort_keys=True).encode()).hexdigest()
    data = args.data.resolve()
    common = json.loads((data / "common-config.json").read_text())
    prompt_file = {
        "main": "prompt-main.json",
        "before": "prompt-before.json",
        "candidate": "app-dev-prompt-draft.json",
    }[args.variant]
    prompts = json.loads((data / prompt_file).read_text())
    prompts = prompts.get("agent_prompt_sections", prompts)
    config_data = {**common, "agent_prompt_sections": prompts, "source": "request_override"}
    allowed = {f.name for f in fields(RuntimeConfig)}
    config = RuntimeConfig(**{k: v for k, v in config_data.items() if k in allowed})
    # Older main lacks request overrides and the skills switch. Equivalent
    # process-local injection keeps those differences out of the comparison.
    module.get_runtime_config = lambda: config
    module.get_matching_scenario_scripts = lambda *a, **kw: []
    fixtures = {r["doc_id"]: r for r in json.loads((data / "sources.json").read_text())}
    cases = json.loads((data / "cases.json").read_text())
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    output = data / "results" / args.variant
    output.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(case, repetition):
        destination = output / f"{case['id']}-{repetition}.json"
        if destination.exists():
            existing = json.loads(destination.read_text())
            expected = {
                "engine_sha256": engine_sha256,
                "config_sha256": digest(common),
                "prompt_sha256": digest(prompts),
                "case_sha256": digest(case),
                "sources_sha256": digest([fixtures[i] for i in case["source_ids"]]),
            }
            if any(existing.get(key) != value for key, value in expected.items()):
                raise ValueError(
                    f"Existing sample has different or missing provenance: {destination}"
                )
            print(f"SKIP existing {destination.name}", flush=True)
            return
        async with semaphore:
            started = monotonic()
            deltas, retrievals, analyses = [], [], []
            first_ms = None

            class FrozenRAG:
                async def retrieve(self, query, **kwargs):
                    retrievals.append({"query": query, "source_ids": case["source_ids"]})
                    if kwargs.get("diagnostics") is not None:
                        kwargs["diagnostics"].update(
                            fixture=True, returned_count=len(case["source_ids"])
                        )
                    return [
                        RAGDocument(
                            content=fixtures[i]["content"],
                            doc_id=i,
                            metadata={**fixtures[i]["metadata"], "collection": "frozen_law"},
                        )
                        for i in case["source_ids"]
                    ]

            async def on_delta(value):
                nonlocal first_ms
                if first_ms is None:
                    first_ms = round((monotonic() - started) * 1000, 2)
                deltas.append(value)

            async def on_analysis(value):
                analyses.append(value)

            diagnostics = {}
            result_data = {
                "variant": args.variant,
                "case_id": case["id"],
                "repetition": repetition,
                "engine_sha256": engine_sha256,
                "config_sha256": digest(common),
                "prompt_sha256": digest(prompts),
                "case_sha256": digest(case),
                "sources_sha256": digest([fixtures[i] for i in case["source_ids"]]),
                "status": "error",
                "case": case,
                "diagnostics": diagnostics,
                "retrievals": retrievals,
                "analyses": analyses,
            }
            async with AsyncOpenAI(
                base_url=module.settings.openrouter_base_url,
                api_key=module.settings.openrouter_api_key,
                timeout=module.settings.openrouter_request_timeout_seconds,
                max_retries=0,
            ) as transport:
                client = RecordedClient(transport)
                # Avoid FirestoreVectorRAG construction or any cloud settings read.
                agent = module.OpenRouterAgent.__new__(module.OpenRouterAgent)
                agent.client, agent.rag, agent.model = client, FrozenRAG(), config.openrouter_model
                kwargs = {"history": case["history"], "on_reply_delta": on_delta, "use_rag": True}
                message = case["message"]
                if args.variant != "main":
                    kwargs.update(
                        contract_version=4,
                        runtime_config=config,
                        diagnostics=diagnostics,
                        case_context={
                            "schema_version": 3,
                            "revision": 1 if case["regenerate"] else 0,
                            "facts": {},
                            "summary": case["summary"],
                            "summary_origin": "user" if case["regenerate"] else "model",
                        },
                        regenerate_from_summary=case["regenerate"],
                        on_analysis=on_analysis,
                    )
                elif case["regenerate"]:
                    # Main has no summary API. Supply the same corrected facts as
                    # explicit input; no deleted historical facts enter any arm.
                    message += "\n以下為唯一案件事實來源，請勿加入舊事實：\n" + case["summary"]
                result_data["effective_input_sha256"] = digest(
                    {"message": message, "history": case["history"]}
                )
                cancelled = False
                try:
                    result = await agent.run(message, **kwargs)
                    body = AssistantChatResponse.model_validate_json(result.reply)
                    result_data.update(
                        status="ok",
                        displayed_reply=body.reply,
                        response=body.model_dump(),
                        sources=result.sources,
                        guidance={
                            k: v
                            for k, v in (getattr(result, "guidance", None) or {}).items()
                            if k != "reasoning"
                        },
                    )
                except Exception as exc:
                    # Error text may contain provider payloads; type is sufficient.
                    result_data.update(error_type=type(exc).__name__, displayed_reply="")
                except asyncio.CancelledError:
                    cancelled = True
                    result_data.update(
                        status="interrupted", error_type="CancelledError", displayed_reply=""
                    )
                result_data.update(model_calls=len(client.calls), raw_model_calls=client.calls)
            result_data.update(
                first_display_delta_ms=first_ms,
                total_ms=round((monotonic() - started) * 1000, 2),
                streamed_reply="".join(deltas),
            )
            destination.write_text(json.dumps(result_data, ensure_ascii=False, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        k: result_data[k]
                        for k in (
                            "variant",
                            "case_id",
                            "repetition",
                            "status",
                            "model_calls",
                            "first_display_delta_ms",
                            "total_ms",
                        )
                    }
                ),
                flush=True,
            )
            if cancelled:
                raise asyncio.CancelledError

    await asyncio.gather(
        *(one(case, repetition) for repetition in range(1, args.repeats + 1) for case in cases)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--variant", choices=["main", "before", "candidate"], required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case")
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args()
    if not args.allow_live:
        parser.error("Live model calls require --allow-live; all inputs must be synthetic.")
    if not 1 <= args.concurrency <= 3 or not 1 <= args.repeats <= 3:
        parser.error("Use 1-3 concurrent calls and 1-3 repetitions.")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
