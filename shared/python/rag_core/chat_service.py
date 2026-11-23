"""
性騷擾防治智能 AI — Chat API Blueprint
接收前端對話請求，執行匿名化後透過 OpenRouter 呼叫 AI 模型。
"""

import asyncio
import json
import uuid
from time import monotonic

from flask import Response, jsonify, request, stream_with_context
from pydantic import ValidationError

from rag_core.agents.gate_agent import GateAgent
from rag_core.agents.openrouter_agent import AgentContractError, OpenRouterAgent
from rag_core.agents.refusal_agent import RefusalAgent
from rag_core.chat.errors import (  # noqa: F401  (public API)
    agent_error,
    image_upload_disabled_error,
    request_validation_error,
    retryable_error,
    service_error,
)
from rag_core.chat.input import (  # noqa: F401  (public API)
    prepare_agent_input,
)
from rag_core.chat.models import (  # noqa: F401  (public API)
    MAX_HISTORY_CHARACTERS,
    MAX_IMAGE_BYTES,
    MAX_REASONING_CHARACTERS,
    MAX_REASONING_ENTRIES,
    USER_MESSAGE_MAX_LENGTH,
    AnalysisEntry,
    ChatRequest,
    MessageItem,
)
from rag_core.chat.payloads import (  # noqa: F401  (public API)
    gate_refusal_payload,
    parse_agent_json_response,
    public_reasoning_entry,
    validated_payload,
)
from rag_core.core.gated_chat import GateUnavailableError, run_gated_chat
from rag_core.core.logger import get_logger
from rag_core.core.privacy_claims import (
    is_local_privacy_reply,
    needs_private_completion,
)
from rag_core.core.runtime_config import (
    get_runtime_config,
    image_upload_enabled,
    resolve_runtime_config,
)

logger = get_logger(__name__)


UNAVAILABLE_CONFIG_SOURCES = {"safe_defaults", "last_known_good", "invalid_gate_config"}


STREAM_HEARTBEAT_SECONDS = 10


_STREAM_PROGRESS_PHASES = {
    "anonymizing",
    "preparing",
    "waiting_model",
    "retrieving",
    "generating",
    "guidance",
    "validating",
}


_agent_instance: OpenRouterAgent | None = None


_gate_instance: GateAgent | None = None


_refusal_instance: RefusalAgent | None = None


def get_agent() -> OpenRouterAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = OpenRouterAgent()
    return _agent_instance


def get_gate() -> GateAgent:
    global _gate_instance
    if _gate_instance is None:
        _gate_instance = GateAgent()
    return _gate_instance


def get_refusal_agent() -> RefusalAgent:
    global _refusal_instance
    if _refusal_instance is None:
        _refusal_instance = RefusalAgent()
    return _refusal_instance


async def run_chat_turn(
    req_obj: ChatRequest,
    runtime_config,
    *,
    session_id: str,
    was_anonymized: bool,
    run_kwargs: dict,
    **options,
) -> dict:
    """Run one Gate-reviewed agent turn with the standard payload builders.

    Callers may override build_payload/build_refusal_payload or pass Gate options
    such as callbacks, on_retract or review_trace.
    """
    options.setdefault(
        "build_payload",
        lambda result: validated_payload(
            result, session_id, was_anonymized, runtime_config, req_obj
        ),
    )
    options.setdefault(
        "build_refusal_payload",
        lambda reply: gate_refusal_payload(reply, session_id, was_anonymized, req_obj),
    )
    return await run_gated_chat(
        get_agent(),
        get_gate(),
        get_refusal_agent(),
        run_kwargs=run_kwargs,
        runtime_config=runtime_config,
        **options,
    )


def _sse_event(event: str, payload: dict) -> str:
    # JSON escaping keeps newlines in reply text inside one SSE data line.
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_response(
    req_obj: ChatRequest,
    session_id: str,
    runtime_config,
    *,
    request_started_at: float | None = None,
):
    started_at = request_started_at if request_started_at is not None else monotonic()

    @stream_with_context
    def generate():
        # Acknowledge each callback only after WSGI takes its bytes. Merely
        # queueing data doesn't yield: buffered SDK chunks or synchronous work
        # could otherwise run ahead and delay the first visible character.
        with asyncio.Runner() as runner:
            events: asyncio.Queue[tuple[str, dict, asyncio.Future | None]] = asyncio.Queue(
                maxsize=1
            )
            sent_output = False
            current_phase: str | None = None
            analysis_entries: list[dict] = []
            reasoning_entries: list[dict] = []
            reasoning_characters = 0
            timings: dict[str, float] = {}
            outcome = "cancelled"
            private_completion = needs_private_completion(
                req_obj.message, [item.model_dump() for item in req_obj.history]
            )

            def mark(name: str):
                timings.setdefault(name, round((monotonic() - started_at) * 1000, 2))

            async def emit(event: str, payload: dict):
                acknowledged = runner.get_loop().create_future()
                await events.put((event, payload, acknowledged))
                await acknowledged

            async def emit_progress(snapshot: dict):
                nonlocal current_phase
                phase = snapshot.get("phase")
                if phase not in _STREAM_PROGRESS_PHASES or phase == current_phase:
                    return
                current_phase = phase
                await emit(
                    "progress",
                    {
                        "phase": phase,
                        "elapsed_ms": round((monotonic() - started_at) * 1000, 2),
                        **({"turn_id": session_id} if runtime_config.gate_enabled else {}),
                    },
                )

            async def emit_retract(code: str):
                nonlocal sent_output, current_phase, analysis_entries, reasoning_entries
                nonlocal reasoning_characters
                await emit("retract", {"turn_id": session_id, "code": code})
                sent_output = False
                current_phase = None
                analysis_entries = []
                reasoning_entries = []
                reasoning_characters = 0

            async def emit_delta(text: str):
                nonlocal sent_output
                if text:
                    sent_output = True
                    mark("first_reply_ready_ms")
                    await emit("delta", {"text": text})
                    # Deliver the visible token first; status must not delay it.
                    await emit_progress({"phase": "generating"})

            async def emit_guidance(snapshot: dict):
                nonlocal sent_output
                if snapshot:
                    sent_output = True
                    mark("first_guidance_ready_ms")
                    await emit("guidance", snapshot)
                    await emit_progress({"phase": "guidance"})

            async def emit_analysis(snapshot: dict):
                nonlocal sent_output
                if not runtime_config.pipeline["enable_analysis"]:
                    return
                entry = AnalysisEntry.model_validate(snapshot).model_dump()
                if len(analysis_entries) >= 4 or any(
                    item["stage"] == entry["stage"] for item in analysis_entries
                ):
                    raise AgentContractError("Invalid analysis summary sequence")
                analysis_entries.append(entry)
                sent_output = True
                await emit("analysis", entry)

            async def emit_reasoning(snapshot: dict):
                nonlocal sent_output, reasoning_characters
                if (
                    not runtime_config.pipeline["enable_analysis"]
                    or not runtime_config.enable_reasoning_display
                    or len(reasoning_entries) >= MAX_REASONING_ENTRIES
                ):
                    return
                entry = public_reasoning_entry(
                    snapshot, MAX_REASONING_CHARACTERS - reasoning_characters
                )
                if entry is not None:
                    reasoning_entries.append(entry)
                    reasoning_characters += len(entry["text"])
                    sent_output = True
                    mark("first_reasoning_ready_ms")
                    await emit("reasoning", entry)

            async def produce():
                try:
                    if runtime_config.enable_anonymization and (
                        runtime_config.pipeline["mask_message"]
                        or runtime_config.pipeline["mask_case_context"]
                    ):
                        await emit_progress({"phase": "anonymizing"})
                    run_kwargs, was_anonymized = prepare_agent_input(req_obj, runtime_config)

                    def build_payload(result):
                        try:
                            payload = validated_payload(
                                result, session_id, was_anonymized, runtime_config, req_obj
                            )
                            if is_local_privacy_reply(payload):
                                return payload
                            if analysis_entries:
                                if (
                                    payload.get("analysis")
                                    and payload["analysis"] != analysis_entries
                                ):
                                    raise AgentContractError(
                                        "Analysis summary changed after publication"
                                    )
                                payload["analysis"] = analysis_entries
                            if reasoning_entries:
                                payload["reasoning"] = reasoning_entries
                            return payload
                        except Exception:
                            raise AgentContractError("Invalid full chat response") from None

                    callbacks = (
                        {"on_progress": emit_progress}
                        if private_completion
                        else {
                            "on_reply_delta": emit_delta,
                            "on_guidance": emit_guidance,
                            "on_progress": emit_progress,
                            "on_analysis": emit_analysis,
                            "on_reasoning": emit_reasoning,
                        }
                    )
                    payload = await run_chat_turn(
                        req_obj,
                        runtime_config,
                        session_id=session_id,
                        was_anonymized=was_anonymized,
                        run_kwargs=run_kwargs,
                        build_payload=build_payload,
                        callbacks=callbacks,
                        on_retract=emit_retract,
                    )
                    if private_completion:
                        await emit_delta(payload["reply"])
                except Exception as exc:
                    error_response, status = agent_error(runtime_config, exc)
                else:
                    await events.put(("done", payload, None))
                    return
                payload = error_response.get_json()
                payload["status"] = status
                if sent_output:
                    # Restarting an answer after showing it would mix attempts.
                    payload["retryable"] = False
                    payload["detail"] = "回覆途中發生錯誤，內容尚未完成，請重新提問"
                await events.put(("error", payload, None))

            task = runner.get_loop().create_task(produce())
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        event, payload, acknowledged = runner.run(
                            asyncio.wait_for(events.get(), timeout=STREAM_HEARTBEAT_SECONDS)
                        )
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event in {"delta", "guidance"}:
                        mark(
                            "first_reply_yield_ms"
                            if event == "delta"
                            else "first_guidance_yield_ms"
                        )
                    elif event == "reasoning":
                        mark("first_reasoning_yield_ms")
                    elif event in {"done", "error"}:
                        outcome = event
                    yield _sse_event(event, payload)
                    if acknowledged is not None and not acknowledged.done():
                        acknowledged.set_result(None)
                    if event in {"done", "error"}:
                        break
            finally:
                task.cancel()

                async def finish():
                    await asyncio.gather(task, return_exceptions=True)

                runner.run(finish())
                logger.info(
                    "Chat stream timing outcome=%s",
                    outcome,
                    extra={
                        "event": "chat_stream_timing",
                        "session_id": session_id,
                        "outcome": outcome,
                        "duration_ms": round((monotonic() - started_at) * 1000, 2),
                        **timings,
                    },
                )

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store, no-transform", "X-Accel-Buffering": "no"},
    )


def chat():
    """
    發送對話訊息
    接收使用者訊息與對話歷史，執行 PII 匿名化後透過 OpenRouter Agent 呼叫 AI，回傳回覆。
    """
    request_started_at = monotonic()
    try:
        req_data = request.get_json()
        if not req_data:
            return jsonify({"code": "invalid_json", "detail": "Invalid JSON"}), 400
        req_obj = ChatRequest(**req_data)
    except ValidationError as e:
        return request_validation_error(e)
    except Exception:
        # Never echo parser or framework exception text to the public client.
        return jsonify({"code": "invalid_json", "detail": "Invalid JSON"}), 400

    runtime_config = resolve_runtime_config(get_runtime_config())
    if runtime_config.source in UNAVAILABLE_CONFIG_SOURCES:
        return agent_error(runtime_config, GateUnavailableError("Runtime config unavailable"))
    if runtime_config.maintenance_message:
        return (
            jsonify(
                {
                    "detail": runtime_config.maintenance_message,
                    "retryable": False,
                    "code": "maintenance",
                }
            ),
            503,
        )
    if req_obj.image_base64 and not image_upload_enabled(runtime_config):
        return image_upload_disabled_error()
    session_id = str(uuid.uuid4())
    if req_obj.stream:
        return _stream_response(
            req_obj,
            session_id,
            runtime_config,
            request_started_at=request_started_at,
        )

    try:
        run_kwargs, was_anonymized = prepare_agent_input(req_obj, runtime_config)
        payload = asyncio.run(
            run_chat_turn(
                req_obj,
                runtime_config,
                session_id=session_id,
                was_anonymized=was_anonymized,
                run_kwargs=run_kwargs,
            )
        )
    except Exception as exc:
        return agent_error(runtime_config, exc)
    return jsonify(payload)
