"""Build and validate the public chat payload from an agent result."""

import json

from pydantic import ValidationError

from rag_core.agents.openrouter_agent import AgentContractError, AgentResult
from rag_core.chat.models import (
    MAX_REASONING_CHARACTERS,
    MAX_REASONING_ENTRIES,
    AnalysisEntry,
    ChatRequest,
    ClarificationV4,
    ReasoningEntry,
)
from rag_core.core.case_context import (
    MAX_CONTEXT_REVISION,
    SummaryUpdate,
)
from rag_core.core.chat_response import (
    AssistantChatResponse,
    action_key,
)
from rag_core.core.privacy_claims import (
    asks_for_third_party_identifiers,
    has_unsupported_privacy_claim,
    local_privacy_reply,
)


def _strip_json_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _escape_newlines_inside_json_strings(text: str) -> str:
    """Escape bare line breaks only when they appear inside JSON strings."""
    result: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\":
            result.append(char)
            escaped = True
            continue
        if char == '"':
            result.append(char)
            in_string = not in_string
            continue
        if in_string and char == "\n":
            result.append("\\n")
            continue
        if in_string and char == "\r":
            result.append("\\r")
            continue
        result.append(char)
    return "".join(result)


def parse_agent_json_response(reply: str) -> dict | None:
    cleaned = _strip_json_code_fence(reply)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        repaired = _escape_newlines_inside_json_strings(cleaned)
        return json.loads(repaired)


def public_reasoning_entry(value, remaining: int) -> dict | None:
    if not isinstance(value, dict) or remaining <= 0 or not isinstance(value.get("text"), str):
        return None
    try:
        # Never serialize a provider object wholesale: encrypted blocks,
        # signatures and tool metadata are not part of this public channel.
        return ReasoningEntry.model_validate(
            {
                "text": value["text"][:remaining],
                "kind": value.get("kind"),
                "stage": value.get("stage"),
            }
        ).model_dump()
    except ValidationError:
        return None


def public_reasoning_entries(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    entries = []
    remaining = MAX_REASONING_CHARACTERS
    for value in values[:MAX_REASONING_ENTRIES]:
        entry = public_reasoning_entry(value, remaining)
        if entry is not None:
            entries.append(entry)
            remaining -= len(entry["text"])
    return entries


def response_payload(
    result: AgentResult,
    session_id: str,
    was_anonymized: bool,
    runtime_config,
    req_obj: ChatRequest | None = None,
):
    """Only publish metadata after the complete response passes the existing contract."""
    data = parse_agent_json_response(result.reply)
    if not isinstance(data, dict):
        raise ValueError("OpenRouter response must be a JSON object")
    structured_response = AssistantChatResponse.model_validate(data)
    approved_actions = {}
    for action in result.available_actions:
        key = action_key(action)
        if key is not None:
            approved_actions.setdefault(key, action)
    action_buttons = []
    selected_keys = set()
    for action in structured_response.action_buttons:
        key = action_key(action)
        if key in approved_actions and key not in selected_keys:
            # Labels, URLs and option values still come only from trusted Skills.
            action_buttons.append(approved_actions[key])
            selected_keys.add(key)
    payload = {
        "reply": structured_response.reply,
        "session_id": session_id,
        "turn_id": session_id,
        "anonymized": was_anonymized,
        "rag_used": {"status": result.rag_used, "sources": result.sources or []},
        "emotion": structured_response.emotion,
        "emotion_color": structured_response.emotion_color,
        "suggested_replies": structured_response.suggested_replies,
        "action_buttons": action_buttons,
        "interaction_mode": structured_response.interaction_mode,
        "clarifying_questions": structured_response.clarifying_questions,
    }
    if req_obj is not None:
        guidance = result.guidance or {}
        if guidance.get("contract_version") != 4:
            raise AgentContractError("Invalid v4 response contract")
        revision = req_obj.case_context.revision if req_obj.case_context else 0
        returned_revision = guidance.get("context_revision", revision)
        if (
            isinstance(returned_revision, bool)
            or not isinstance(returned_revision, int)
            or returned_revision != revision
        ):
            raise AgentContractError("Response context revision does not match the request")
        update = (
            SummaryUpdate.model_validate(guidance["summary_update"])
            if guidance.get("summary_update") is not None
            else None
        )
        if update is not None and update.base_revision != revision:
            raise AgentContractError("Summary update does not match the request revision")
        if update is not None and req_obj.regenerate_from_summary:
            raise AgentContractError("Regeneration must not replace the authoritative summary")
        current_summary = req_obj.case_context.summary if req_obj.case_context else ""
        if update is not None and update.summary == current_summary:
            update = None
        if update is not None and revision >= MAX_CONTEXT_REVISION:
            raise AgentContractError("Summary revision cannot be advanced")
        question = (
            ClarificationV4.model_validate(guidance["clarification"])
            if guidance.get("clarification") is not None
            else None
        )
        if question is not None and question.context_revision != revision + int(update is not None):
            raise AgentContractError("Clarification does not match the resulting context revision")
        # v4 has one authoritative text summary and natural reply. Do not expose
        # stale legacy fact/section fields or arbitrary agent-internal payloads.
        payload.update(
            contract_version=4,
            context_revision=revision,
            summary_update=update.model_dump() if update is not None else None,
            clarification=question.model_dump() if question is not None else None,
        )
        payload.update(
            {
                key: guidance[key]
                for key in ("execution", "analysis", "reasoning")
                if key in guidance
            }
        )
        if "retrieval" in guidance:
            payload["rag_used"]["retrieval"] = guidance["retrieval"]
    elif result.guidance:
        payload.update(result.guidance)
    if payload.get("contract_version") == 4:
        entries = payload.get("analysis", []) if runtime_config.pipeline["enable_analysis"] else []
        if not isinstance(entries, list) or len(entries) > 4:
            raise ValueError("Invalid analysis summary")
        payload["analysis"] = [AnalysisEntry.model_validate(item).model_dump() for item in entries]
        if len({entry["stage"] for entry in payload["analysis"]}) != len(entries):
            raise ValueError("Duplicate analysis summary stage")
    else:
        payload.pop("analysis", None)
    if (
        payload.get("contract_version") == 4
        and runtime_config.pipeline["enable_analysis"]
        and runtime_config.enable_reasoning_display
    ):
        payload["reasoning"] = public_reasoning_entries(payload.get("reasoning", []))
    else:
        payload.pop("reasoning", None)
    if runtime_config.development_mode:
        payload["debug_tool_calls"] = result.tool_calls
    return payload


def gate_refusal_payload(
    reply: str, session_id: str, was_anonymized: bool, req_obj: ChatRequest
) -> dict:
    """Keep refusal metadata inert under every supported chat contract."""
    payload = {
        "reply": reply,
        "session_id": session_id,
        "turn_id": session_id,
        "anonymized": was_anonymized,
        "rag_used": {"status": False, "sources": []},
        "emotion": "中性",
        "emotion_color": "gray",
        "suggested_replies": [],
        "action_buttons": [],
        "interaction_mode": "answer",
        "clarifying_questions": [],
    }
    revision = req_obj.case_context.revision if req_obj.case_context else 0
    payload.update(
        contract_version=4,
        context_revision=revision,
        summary_update=None,
        clarification=None,
        analysis=[],
        execution={"route": "gate_refusal", "model_calls": 1},
    )
    return payload


def privacy_replacement_payload(
    session_id: str, was_anonymized: bool, req_obj: ChatRequest, original_payload: dict
) -> dict:
    payload = gate_refusal_payload(
        local_privacy_reply(req_obj.message), session_id, was_anonymized, req_obj
    )
    if "execution" in payload:
        payload["execution"]["route"] = "privacy_correction"
        original_execution = original_payload.get("execution")
        original_calls = (
            original_execution.get("model_calls") if isinstance(original_execution, dict) else None
        )
        if (
            isinstance(original_calls, int)
            and not isinstance(original_calls, bool)
            and original_calls >= 0
        ):
            payload["execution"]["model_calls"] = original_calls
    return payload


def validated_payload(result, session_id, was_anonymized, runtime_config, req_obj):
    try:
        payload = response_payload(result, session_id, was_anonymized, runtime_config, req_obj)
    except Exception:
        if not runtime_config.gate_enabled:
            raise
        raise AgentContractError("Invalid full chat response") from None
    if has_unsupported_privacy_claim(
        payload, third_party_request=asks_for_third_party_identifiers(req_obj.message)
    ):
        return privacy_replacement_payload(session_id, was_anonymized, req_obj, payload)
    return payload
