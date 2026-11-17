"""Coordinate a parallel pre review and a post review of provisional output."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from rag_core.core.anonymizer import anonymize
from rag_core.core.gate_policy import FIXED_SHORT_REFUSALS
from rag_core.core.logger import get_logger
from rag_core.core.privacy_claims import (
    asks_for_third_party_identifiers,
    has_unsupported_privacy_claim,
    is_local_privacy_reply,
)

logger = get_logger(__name__)
GATE_BUFFER_EVENTS = 32
MAX_POST_REVIEW_CHARACTERS = 60_000
VISIBLE_FIELDS = (
    "reply",
    "suggested_replies",
    "clarifying_questions",
    "action_buttons",
    "answer_sections",
    "analysis",
    "reasoning",
    "summary_update",
    "fact_updates",
    "clarification",
)
_PRIVATE_METADATA_KEYS = {
    "validation_token",
    "question_id",
    "source_ids",
    "source_url",
    "url",
    "phone_number",
    "doc_id",
    "id",
    "signature",
    "token",
}


class GateUnavailableError(RuntimeError):
    """A review or refusal could not finish; the candidate must not be committed."""


def _redacted_gate_value(value: Any) -> Any:
    """Send reviewable text without metadata tokens or known PII patterns."""
    if isinstance(value, str):
        return anonymize(value).anonymized
    if isinstance(value, list):
        return [_redacted_gate_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redacted_gate_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _redacted_gate_value(item)
            for key, item in value.items()
            if key not in _PRIVATE_METADATA_KEYS
        }
    return value


def _case_context_for_gate(arguments: dict) -> dict:
    context = arguments.get("case_context") or {}
    facts = context.get("facts") or {}
    answer = arguments.get("clarification_answer") or {}
    return _redacted_gate_value(
        {
            "summary": context.get("summary", ""),
            "facts": {
                key: item.get("value")
                for key, item in facts.items()
                if isinstance(item, dict) and item.get("value") is not None
            },
            "clarification_answer": answer.get("value"),
        }
    )


async def run_gated_chat(
    agent,
    gate,
    refusal,
    *,
    run_kwargs: dict,
    runtime_config,
    build_payload: Callable[[Any], dict],
    build_refusal_payload: Callable[[str], dict],
    callbacks: dict[str, Callable[[Any], Awaitable[None]] | None] | None = None,
    on_retract: Callable[[str], Awaitable[None]] | None = None,
    review_trace: list[dict[str, Any]] | None = None,
) -> dict:
    """Return only an allowed payload; provisional callbacks may require retraction."""
    callbacks = callbacks or {}
    if not runtime_config.gate_enabled:
        result = await agent.run(**run_kwargs, **{k: v for k, v in callbacks.items() if v})
        if callbacks.get("on_progress") is not None:
            await callbacks["on_progress"]({"phase": "validating"})
        payload = build_payload(result)
        if is_local_privacy_reply(payload):
            if on_retract is not None and callbacks.get("on_reply_delta") is not None:
                await on_retract("gate_denied")
            if callbacks.get("on_reply_delta") is not None:
                await callbacks["on_reply_delta"](payload["reply"])
        return payload

    gate_input = {
        "user_message": _redacted_gate_value(run_kwargs["user_message"]),
        "history": _redacted_gate_value(run_kwargs["history"]),
        "case_context": _case_context_for_gate(run_kwargs),
        "runtime_config": runtime_config,
    }
    events: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=GATE_BUFFER_EVENTS)
    published: list[tuple[str, Any]] = []
    published_characters = 0
    retracted = False

    async def buffer(name: str, value: Any):
        # Bound an oversized provider chunk before it enters the queue.
        if name == "on_reply_delta" and isinstance(value, str):
            for offset in range(0, len(value), 1000):
                await events.put((name, value[offset : offset + 1000]))
        else:
            await events.put((name, value))

    buffered_callbacks = {
        name: (lambda value, name=name: buffer(name, value))
        for name, callback in callbacks.items()
        if callback is not None
    }

    async def produce():
        try:
            result = await agent.run(**run_kwargs, **buffered_callbacks)
        except Exception as exc:
            await events.put(("error", exc))
        else:
            await events.put(("result", result))

    async def judge(
        stage: str,
        candidate: dict | None = None,
        *,
        local_replacement_reason: str | None = None,
    ):
        started = monotonic()
        outcome = "error"
        failure = None
        reason_code = None
        try:
            async with asyncio.timeout(runtime_config.gate_timeout_seconds):
                decision = await gate.run(stage=stage, **gate_input, candidate=candidate)
            outcome = decision.decision
            reason_code = decision.reason_code
            return decision
        except Exception as exc:
            # Upstream errors can contain private request data. Log only its type.
            failure = type(exc).__name__
            raise GateUnavailableError(f"{stage} review unavailable") from None
        finally:
            summary = {
                "stage": stage,
                "decision": outcome,
                "reason_code": reason_code,
                "duration_ms": round((monotonic() - started) * 1000, 2),
                "policy_sha256": hashlib.sha256(
                    runtime_config.gate_prompt_for_stage(stage).encode()
                ).hexdigest(),
                "model": runtime_config.gate_model,
                "failure_type": failure,
            }
            if local_replacement_reason is not None:
                summary["local_replacement_reason"] = local_replacement_reason
            if review_trace is not None:
                review_trace.append(summary)
            logger.info(
                "Chat Gate review settled",
                extra={
                    "event": "chat_gate_review",
                    "gate_stage": stage,
                    "gate_decision": outcome,
                    "gate_failure_type": failure,
                    "gate_model": runtime_config.gate_model,
                    "gate_policy_hash": summary["policy_sha256"],
                    "gate_duration_ms": summary["duration_ms"],
                    **(
                        {"local_replacement_reason": local_replacement_reason}
                        if local_replacement_reason is not None
                        else {}
                    ),
                },
            )

    async def retract(code: str):
        nonlocal retracted
        if not retracted and published and on_retract is not None:
            retracted = True
            await on_retract(code)

    async def make_refusal(reason_code: str) -> dict:
        try:
            # Bound the main-model retry and its review together. A generated
            # refusal is still model output, so publish it only after Gate allows it.
            async with asyncio.timeout(runtime_config.gate_timeout_seconds):
                reply = await refusal.run(reason_code=reason_code, runtime_config=runtime_config)
                if (
                    not isinstance(reply, str)
                    or not reply.strip()
                    or len(reply) > 300
                    or "```" in reply
                    or "\n" in reply
                ):
                    raise ValueError("Invalid short refusal")
                reply = reply.strip()
                if has_unsupported_privacy_claim(
                    {"reply": reply},
                    third_party_request=asks_for_third_party_identifiers(
                        run_kwargs["user_message"]
                    ),
                ):
                    raise ValueError("Unsupported privacy claim in short refusal")
                refusal_decision = await judge(
                    "post",
                    {
                        "visible_payload": _redacted_gate_value({"reply": reply}),
                        "published_events": [],
                    },
                )
                if refusal_decision.decision != "allow":
                    raise ValueError("Generated refusal failed review")
        except Exception as exc:
            logger.warning(
                "Chat Gate short refusal regeneration failed",
                extra={
                    "event": "chat_gate_refusal_fallback",
                    "failure_type": type(exc).__name__,
                },
            )
            reply = FIXED_SHORT_REFUSALS[reason_code]
        payload = build_refusal_payload(reply)
        if callbacks.get("on_reply_delta") is not None:
            await callbacks["on_reply_delta"](reply)
        return payload

    business = asyncio.create_task(produce(), name="chat-business")
    pre = asyncio.create_task(judge("pre"), name="chat-gate-pre")
    try:
        decision = await pre
        if decision.decision == "deny":
            business.cancel()
            await asyncio.gather(business, return_exceptions=True)
            return await make_refusal(decision.reason_code)

        result = None
        while result is None:
            name, value = await events.get()
            if name == "error":
                raise value
            if name == "result":
                result = value
                break
            callback = callbacks.get(name)
            if callback is None:
                continue
            size = len(json.dumps(value, ensure_ascii=False, default=str))
            published_characters += size
            if published_characters > MAX_POST_REVIEW_CHARACTERS:
                raise GateUnavailableError("Provisional response exceeds review limit")
            await callback(value)
            published.append((name, value))

        if callbacks.get("on_progress") is not None:
            await callbacks["on_progress"]({"phase": "validating"})
        payload = build_payload(result)
        if is_local_privacy_reply(payload):
            # Review the trusted local correction as the post stage. A failed
            # review still closes the turn, as with any other candidate.
            if any(name != "on_progress" for name, _ in published):
                await retract("gate_denied")
            replacement_candidate = {
                "visible_payload": _redacted_gate_value(
                    {key: payload[key] for key in VISIBLE_FIELDS if key in payload}
                ),
                "published_events": [],
            }
            replacement_decision = await judge(
                "post",
                replacement_candidate,
                local_replacement_reason="unsupported_privacy_claim",
            )
            if replacement_decision.decision != "allow":
                raise GateUnavailableError("Local privacy correction denied")
            if callbacks.get("on_reply_delta") is not None:
                await callbacks["on_reply_delta"](payload["reply"])
            return payload
        candidate = {
            "visible_payload": _redacted_gate_value(
                {key: payload[key] for key in VISIBLE_FIELDS if key in payload}
            ),
            "published_events": _redacted_gate_value(
                [(name, value) for name, value in published if name != "on_progress"]
            ),
        }
        if len(json.dumps(candidate, ensure_ascii=False, default=str)) > MAX_POST_REVIEW_CHARACTERS:
            raise GateUnavailableError("Candidate response exceeds review limit")
        post_decision = await judge("post", candidate)
        if post_decision.decision == "deny":
            await retract("gate_denied")
            return await make_refusal(post_decision.reason_code)
        return payload
    except BaseException:
        await retract("review_unavailable")
        raise
    finally:
        for task in (business, pre):
            if not task.done():
                task.cancel()
        await asyncio.gather(business, pre, return_exceptions=True)
