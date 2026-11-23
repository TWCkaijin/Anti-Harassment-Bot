"""Request-local understanding, evidence retrieval and validated natural answers.

Only complete answer units cross the display boundary. Model/provider metadata,
summary proposals and actions remain provisional until the full response passes.
"""

import asyncio
import json
import re
import secrets
from contextlib import suppress
from copy import deepcopy
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from rag_core.agents.provider_schema import provider_response_format
from rag_core.agents.v4_contract import (  # noqa: F401  (public API)
    _HAN_CHARACTER,
    V4_ANSWER_UNIT_INSTRUCTION,
    V4_FINAL_FORMAT,
    V4_FINAL_STAGE_INSTRUCTION,
    V4_INSTRUCTION,
    V4_PLAN_FORMAT,
    V4_PREDICTION_FINAL_FORMAT,
    V4_PREDICTION_FINAL_INSTRUCTION,
    V4_PREDICTION_INSTRUCTION,
    V4_PREDICTION_PLAN_FORMAT,
    AnswerUnit,
    ApplicableLawPrediction,
    ClarificationPlan,
    ClarificationUnderstanding,
    DirectPlan,
    DirectUnderstanding,
    FinalEnvelope,
    PlanEnvelope,
    PredictionPlan,
    ProviderPlanEnvelope,
    QuestionV4,
    RetrievalRequest,
    ShortEvidence,
    SourceID,
    SummaryProposal,
    SynthesisPlan,
    UncitedAnswerUnit,
    UnderstandingV4,
    _final_response_format,
    _visible_prompt_overlap,
    applicable_laws_contract_sha256,
    applicable_laws_scenario_catalog_sha256,
    chat_test_contract_sha256,
)
from rag_core.core.agent_errors import (
    ApplicableLawsContractMismatchError,
    ChatTestContractMismatchError,
    ModelOutputLimitError,
)
from rag_core.core.agent_prompts import assemble_service_instruction
from rag_core.core.answer_validation import (
    build_source,
    check_answer_units,
    contextless_prior_legal_source_request,
    verified_law_url,
)
from rag_core.core.case_context import CaseContext, is_urgent
from rag_core.core.chat_response import (
    ASSISTANT_REPLY_MAX_LENGTH,
    AssistantChatResponse,
)
from rag_core.core.clarification_tokens import sign_clarification, validate_clarification_answer
from rag_core.core.official_contacts import contact_catalog, official_contact_reply
from rag_core.core.runtime_config import get_runtime_config
from rag_core.core.scenario_scripts import (
    available_actions,
    format_scenario_instruction,
    get_matching_scenario_scripts,
    get_scenario_scripts,
)
from rag_core.core.streaming_reply import MAX_STREAM_RESPONSE_LENGTH, StreamReplyError
from rag_core.core.streaming_units import AnswerUnitJSONDecoder
from rag_core.rag.base import RAGUnavailableError
from rag_core.rag.rerank import apply_provider_sort, retrieve_with_rerank

# A local validation error must not immediately turn an upstream generation into
# "cancelled". Stop publishing and receive its ending, with bounded time/bytes.
VALIDATION_DRAIN_SECONDS = 10.0


_SCOPE_LABELS = {
    "personal": "個人陳述",
    "third_person": "第三人稱情境",
    "hypothetical": "假設情境",
    "general": "一般資訊",
}


_INTENT_LABELS = {
    "legal_direction": "法律適用方向",
    "procedure": "處理程序",
    "deadline": "期限或版本",
    "support": "支持協助",
    "other": "目前需求",
}


_SUMMARY_LABEL = re.compile(r"^\[(?:個人陳述|第三人稱情境|假設情境|引述|本輪需求|一般資訊)\]\s*")


_SAFETY_TEXT = (
    "如果你現在有立即危險，請先移動到安全處，聯絡身邊可信任的人或當地緊急服務；不必等候這份回覆。"
)


def _supported_evidence(evidence, texts):
    return [item for item in evidence if item and any(item in text for text in texts)]


def _metadata_rejection(diagnostics, stage, field, reason):
    # Diagnostics contain categories only, never rejected private excerpts.
    if diagnostics is not None:
        diagnostics.setdefault("metadata_validation", []).append(
            {"stage": stage, "field": field, "reason": reason, "action": "discarded"}
        )


def _discard_direct_user_citation(raw, diagnostics=None):
    """Repair a non-document marker, never invent or accept a retrieved source.

    Only the first, retrieval-free direct answer may use this compatibility
    repair. Other IDs, mixed citations and all final-stage citations still fail
    normal validation. Return a copy so incremental decoder state stays intact.
    """
    if isinstance(raw, dict) and raw.get("source_ids") == ["user"]:
        _metadata_rejection(
            diagnostics, "understanding", "answer_units.source_ids", "non_document_user_reference"
        )
        return {**raw, "source_ids": []}
    return raw


def _validated_understanding(raw, evidence_texts, diagnostics):
    """Share evidence validation while retaining strict routing and safety fields."""
    rejected = False
    try:
        understanding = UnderstandingV4.model_validate(raw, strict=True)
    except ValidationError as exc:
        if not isinstance(raw, dict) or any(
            not error["loc"] or error["loc"][0] != "evidence" for error in exc.errors()
        ):
            raise
        understanding = UnderstandingV4.model_validate({**raw, "evidence": []}, strict=True)
        rejected = True
        _metadata_rejection(diagnostics, "understanding", "understanding.evidence", "invalid_shape")
    evidence = _supported_evidence(understanding.evidence, evidence_texts)
    if len(evidence) != len(understanding.evidence):
        rejected = True
        _metadata_rejection(
            diagnostics, "understanding", "understanding.evidence", "unsupported_evidence"
        )
        understanding = understanding.model_copy(update={"evidence": evidence})
    return understanding, rejected


async def _retrieve_v4(
    rag,
    retrieval,
    understanding,
    config,
    metrics,
    diagnostics,
    progress,
    *,
    fail_closed=False,
    require_law=False,
):
    """Use one retrieval/query-masking boundary for chat and prediction contracts."""
    pipeline = config.pipeline
    query = retrieval.query
    before = query
    masked_types = []
    masking_enabled = config.enable_anonymization and pipeline.get("mask_retrieval_query", True)
    if masking_enabled:
        from rag_core.core.anonymizer import anonymize

        masked = anonymize(query)
        query, masked_types = masked.anonymized, masked.detected_types
    types = _retrieval_types(retrieval, understanding, require_law=require_law)
    if diagnostics is not None:
        diagnostics.setdefault("pii", {})["retrieval_query"] = {
            "enabled": masking_enabled,
            "kinds": masked_types,
            "changed_values": int(before != query),
            "before": before,
            "after": query,
        }
    await progress("retrieving")
    sources, documents = [], []
    rerank_before = metrics.get("rerank_ms", 0)
    started = monotonic()
    try:
        docs = await retrieve_with_rerank(
            rag,
            query,
            config,
            top_k=config.rag_retrieval_top_k,
            data_type=types[0] if len(types) == 1 else "all",
            collection_names_by_data_type=config.rag_collections,
            distance_threshold=config.rag_distance_threshold,
            selected_data_types=types,
            preserve_data_types=True,
            timings=metrics,
            **(
                {"diagnostics": diagnostics.setdefault("retrieval", {})}
                if diagnostics is not None
                else {}
            ),
        )
        for doc in docs:
            kind = next(
                (
                    key
                    for key, name in config.rag_collections.items()
                    if name == doc.metadata.get("collection")
                ),
                types[0],
            )
            source = build_source(doc, kind)
            if kind == "law":
                source["source_url"] = verified_law_url(source)
            if source["doc_id"] not in {item["doc_id"] for item in sources}:
                sources.append(source)
                documents.append({"source": source, "content": doc.content})
        return sources, documents, "ok" if sources else "empty"
    except Exception as exc:
        if diagnostics is not None:
            diagnostics.setdefault("retrieval", {})["failure_type"] = type(exc).__name__
            diagnostics["retrieval_status"] = "failed"
        if fail_closed:
            raise RAGUnavailableError("Applicable-law retrieval did not complete") from None
        return [], [], "failed"
    finally:
        metrics["retrieval_ms"] += (monotonic() - started) * 1000 - (
            metrics.get("rerank_ms", 0) - rerank_before
        )


def _retrieval_types(retrieval, understanding, *, require_law=False):
    types = list(retrieval.data_types)
    if (
        require_law or understanding.intent in {"legal_direction", "procedure", "deadline"}
    ) and "law" not in types:
        types.insert(0, "law")
    return types


def validated_summary(proposal, context, user_texts, scope, *, diagnostics=None):
    """Accept an extractive proposal, never an unsupported model paraphrase."""
    if proposal is None:
        return None
    texts = [context.summary, *user_texts]
    evidence = _supported_evidence(proposal.evidence, texts)
    if len(evidence) != len(proposal.evidence) or sum(map(len, evidence)) > 8000:
        _metadata_rejection(diagnostics, "summary", "summary_update", "unsupported_evidence")
        return None
    lines = [line.strip() for line in proposal.summary.splitlines() if line.strip()]
    for line in lines:
        excerpt = _SUMMARY_LABEL.sub("", line)
        if not excerpt or not (
            excerpt in context.summary or any(excerpt in item for item in evidence)
        ):
            _metadata_rejection(diagnostics, "summary", "summary_update", "unsupported_summary")
            return None
    summary = "\n".join(lines)
    if scope in {"third_person", "hypothetical"} and not any(
        f"[{label}]" in summary for label in ("第三人稱情境", "假設情境", "引述")
    ):
        summary = f"[{_SCOPE_LABELS[scope]}] " + summary
    if context.summary_origin == "user" and context.summary and context.summary not in summary:
        _metadata_rejection(diagnostics, "summary", "summary_update", "user_summary_overwrite")
        return None
    if not summary or len(summary) > 4000:
        _metadata_rejection(diagnostics, "summary", "summary_update", "invalid_summary_length")
        return None
    if summary == context.summary:
        return None
    return {"base_revision": context.revision, "summary": summary, "evidence": evidence}


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def provider_reasoning_text(value):
    """Expose only provider-designated summaries, never raw reasoning or opaque data."""
    fragments = []
    details = _get(value, "reasoning_details")
    if isinstance(details, list):
        for item in details:
            if _get(item, "type") != "reasoning.summary":
                continue
            text = _get(item, "summary")
            if isinstance(text, str) and text and (text, "summary") not in fragments:
                fragments.append((text, "summary"))
    return fragments


def _prediction_json_object(text):
    """Accept one JSON object; never repair fences, prose, duplicate keys or NaN."""

    def distinct_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON key")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("Invalid JSON constant")

    if not isinstance(text, str) or not text.strip():
        raise ValueError("Missing prediction JSON")
    value = json.loads(text, object_pairs_hook=distinct_pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("Prediction JSON must be an object")
    return value


def model_request_options(
    config, messages: list, response_format: dict, *, hide_reasoning: bool
) -> dict:
    """Build one chat-completions request from the runtime model settings."""
    options = {
        "model": config.openrouter_model,
        "messages": messages,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "response_format": provider_response_format(config.openrouter_model, response_format),
    }
    if config.max_tokens > 0:
        options["max_tokens"] = config.max_tokens
    if config.reasoning_effort != "none":
        options["extra_body"] = {
            "reasoning": {"effort": config.reasoning_effort, "exclude": hide_reasoning}
        }
    apply_provider_sort(options, config)
    return options


async def _run_applicable_laws_v4(
    rag,
    client,
    *,
    config,
    context,
    user_message,
    history,
    image_base64,
    clarification_data,
    evidence_texts,
    regenerate_from_summary,
    rag_enabled,
    metrics,
    diagnostics,
    on_progress,
    expected_applicable_laws_contract_sha256,
):
    from rag_core.agents.openrouter_agent import (
        AgentContractError,
        ApplicableLawPredictionResult,
    )

    async def progress(phase):
        if on_progress:
            await on_progress({"phase": phase})

    metrics["route"] = "understand_retrieve_predict" if rag_enabled else "understand_predict"
    catalog = (
        tuple(get_scenario_scripts(force_refresh=True))
        if config.pipeline.get("enable_skills", True)
        else ()
    )
    fingerprint = applicable_laws_contract_sha256(config, catalog)
    if diagnostics is not None:
        diagnostics["execution"] = metrics
        diagnostics["applicable_laws_contract_sha256"] = fingerprint
        diagnostics["applicable_laws_scenario_catalog_sha256"] = (
            applicable_laws_scenario_catalog_sha256(config, catalog)
        )
    if (
        expected_applicable_laws_contract_sha256 is not None
        and expected_applicable_laws_contract_sha256 != fingerprint
    ):
        raise ApplicableLawsContractMismatchError
    scripts = (
        get_matching_scenario_scripts(
            context.summary if regenerate_from_summary else user_message,
            history=None if regenerate_from_summary else history,
            catalog=catalog,
        )
        if config.pipeline.get("enable_skills", True)
        else []
    )
    if diagnostics is not None:
        diagnostics["matched_scenario_scripts"] = {
            "count": len(scripts),
            "ids": [script.script_id for script in scripts],
        }
    messages = [
        {"role": "system", "content": assemble_service_instruction(config.agent_prompt_sections)},
        {
            "role": "system",
            "content": V4_PREDICTION_INSTRUCTION
            + "\n本次服務日期（Asia/Taipei）："
            + datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat(),
        },
        {
            "role": "system",
            "content": json.dumps(
                {"request_policy": {"rag_enabled": rag_enabled}}, ensure_ascii=False
            ),
        },
    ]
    if scripts:
        # Trusted service guidance remains available; the test-specific schema
        # and instructions follow it and take precedence over chat-only fields.
        messages.insert(1, {"role": "system", "content": format_scenario_instruction(scripts)})
    if not regenerate_from_summary:
        messages.extend(history or [])
    request_data = {
        "conversation_summary": context.summary,
        "summary_origin": context.summary_origin,
        "message": user_message,
        "clarification_answer": clarification_data,
    }
    if regenerate_from_summary:
        messages.append(
            {
                "role": "system",
                "content": "本輪由已編輯摘要重新預測。conversation_summary是唯一案件事實來源，original_request_for_intent_only只表示需求；不得從原始訊息或歷史恢復舊事實。understanding.evidence只引用目前摘要。",
            }
        )
        request_data["original_request_for_intent_only"] = user_message
        request_data["message"] = "請依目前摘要預測適用法規"
    text = json.dumps(request_data, ensure_ascii=False)
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_base64}},
            ]
            if image_base64
            else text,
        }
    )

    async def complete(request_messages, *, first):
        await progress("waiting_model")
        options = model_request_options(
            config,
            request_messages,
            V4_PREDICTION_PLAN_FORMAT if first else V4_PREDICTION_FINAL_FORMAT,
            hide_reasoning=True,
        )
        options["stream"] = False
        metrics["model_calls"] += 1
        started = monotonic()
        try:
            response = await client.chat.completions.create(**options)
            choices = _get(response, "choices")
            if _get(response, "error") or not choices or len(choices) != 1:
                raise AgentContractError("Invalid applicable-law provider response")
            choice = choices[0]
            if _get(choice, "index", 0) != 0:
                raise AgentContractError("Unexpected applicable-law provider choice")
            finish = _get(choice, "finish_reason")
            if finish == "length":
                raise ModelOutputLimitError
            if finish != "stop":
                raise AgentContractError("Incomplete applicable-law provider response")
            message = _get(choice, "message")
            if _get(message, "refusal") or _get(message, "tool_calls"):
                raise AgentContractError("Unexpected provider refusal or tool call")
            raw = _prediction_json_object(_get(message, "content"))
        except ValueError:
            raise AgentContractError("Invalid applicable-law structured response") from None
        finally:
            metrics["model_ms"] += (monotonic() - started) * 1000
        started = monotonic()
        try:
            if first:
                understanding, _ = _validated_understanding(
                    raw.get("understanding"), evidence_texts, diagnostics
                )
                raw["understanding"] = understanding.model_dump()
                plan = PredictionPlan.model_validate(raw, strict=True)
                if (plan.retrieval is not None) != rag_enabled:
                    raise AgentContractError("Applicable-law retrieval must match request policy")
                return plan
            return ApplicableLawPrediction.model_validate(raw, strict=True)
        except (ValidationError, ValueError):
            raise AgentContractError("Invalid applicable-law structured response") from None
        finally:
            metrics["validation_ms"] += (monotonic() - started) * 1000

    await progress("preparing")
    plan = await complete(messages, first=True)
    sources, documents, retrieval_status = [], [], "disabled"
    if rag_enabled:
        sources, documents, retrieval_status = await _retrieve_v4(
            rag,
            plan.retrieval,
            plan.understanding,
            config,
            metrics,
            diagnostics,
            progress,
            fail_closed=True,
            require_law=True,
        )
    if diagnostics is not None:
        diagnostics.update(
            retrieval_status=retrieval_status,
            retrieved_count=len(sources),
            retrieved_sources=sources,
        )
    final_messages = [
        *messages,
        {"role": "system", "content": V4_PREDICTION_FINAL_INSTRUCTION},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "validated_understanding": plan.understanding.model_dump(),
                    "retrieval_status": retrieval_status,
                    "untrusted_retrieved_documents": documents,
                    "handoff_note": "理解摘要不是法源；只生成有序的適用法規預測。資訊不足仍做最終判斷，不追問。",
                },
                ensure_ascii=False,
            ),
        },
    ]
    prediction = await complete(final_messages, first=False)
    await progress("validating")
    return ApplicableLawPredictionResult(prediction=prediction)


async def run_guided_v4(
    rag,
    client,
    *,
    user_message,
    history=None,
    image_base64=None,
    case_context=None,
    clarification_answer=None,
    clarification_constraints=None,
    on_reply_delta=None,
    on_guidance=None,
    on_progress=None,
    on_analysis=None,
    on_reasoning=None,
    use_rag=True,
    contract_version=4,
    runtime_config=None,
    diagnostics=None,
    regenerate_from_summary=False,
    output_contract="chat",
    expected_applicable_laws_contract_sha256=None,
    expected_chat_test_contract_sha256=None,
):
    from rag_core.agents.openrouter_agent import AgentContractError, AgentResult

    context = CaseContext.model_validate(case_context or {"schema_version": 3})
    if context.schema_version != 3 or contract_version != 4:
        raise AgentContractError("v4 requires conversation summary schema 3")
    config = runtime_config or get_runtime_config()
    pipeline = config.pipeline
    rag_enabled = use_rag and pipeline.get("enable_rag", True)
    analysis_enabled = pipeline.get("enable_analysis", True)
    reasoning_enabled = (
        analysis_enabled and config.enable_reasoning_display and config.reasoning_effort != "none"
    )
    analyses, reasoning, sources, documents, checked_units = [], [], [], [], []
    seen_reasons = set()
    reasoning_length = 0
    metrics = {
        "route": "understand",
        "model_calls": 0,
        "embedding_ms": 0.0,
        "retrieval_ms": 0.0,
        "model_ms": 0.0,
        "validation_ms": 0.0,
    }
    user_texts = [] if regenerate_from_summary else [user_message]
    clarification_data = None
    if clarification_answer:
        if regenerate_from_summary:
            raise AgentContractError("Summary regeneration cannot submit a new clarification")
        issued = clarification_constraints or validate_clarification_answer(
            clarification_answer, contract_version=4
        )
        if not issued or issued.get("context_revision") != context.revision:
            raise AgentContractError("Clarification no longer matches the current summary")
        if clarification_answer.get("status") not in {"provided", "unknown", "declined"}:
            raise AgentContractError("Invalid clarification answer status")
        value = clarification_answer.get("value")
        # The signed token may encode original, unmasked option values. Only
        # projected data crosses the model boundary after the API's validation
        # and masking; credentials and internal constraints stay server-side.
        clarification_data = {
            "status": clarification_answer["status"],
            "value": value,
            "context_scope": issued["context_scope"],
        }
        if clarification_answer.get("status") == "provided":
            user_texts.extend(value if isinstance(value, list) else [value])
    evidence_texts = [context.summary, *user_texts]

    if output_contract == "applicable_laws_v1":
        return await _run_applicable_laws_v4(
            rag,
            client,
            config=config,
            context=context,
            user_message=user_message,
            history=history,
            image_base64=image_base64,
            clarification_data=clarification_data,
            evidence_texts=evidence_texts,
            regenerate_from_summary=regenerate_from_summary,
            rag_enabled=rag_enabled,
            metrics=metrics,
            diagnostics=diagnostics,
            on_progress=on_progress,
            expected_applicable_laws_contract_sha256=expected_applicable_laws_contract_sha256,
        )
    if output_contract != "chat":
        raise AgentContractError("Unsupported v4 output contract")

    test_catalog = None
    if expected_chat_test_contract_sha256 is not None:
        test_catalog = (
            tuple(get_scenario_scripts(force_refresh=True))
            if pipeline.get("enable_skills", True)
            else ()
        )
        fingerprint = chat_test_contract_sha256(config, test_catalog)
        if expected_chat_test_contract_sha256 != fingerprint:
            raise ChatTestContractMismatchError
        if diagnostics is not None:
            diagnostics["chat_test_contract_sha256"] = fingerprint
            diagnostics["service_date"] = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
            diagnostics["corpus_version"] = "unknown"

    async def progress(phase):
        if on_progress:
            await on_progress({"phase": phase})

    async def analyze(stage, summary, facts=None, source_labels=None, limitations=None):
        if not analysis_enabled or any(item["stage"] == stage for item in analyses):
            return
        entry = {
            "stage": stage,
            "summary": summary[:1000],
            "facts": (facts or [])[:12],
            "source_labels": (source_labels or [])[:24],
            "limitations": (limitations or [])[:12],
        }
        analyses.append(entry)
        if on_analysis:
            await on_analysis(entry)

    async def emit_reasoning(value, stage):
        nonlocal reasoning_length
        if not reasoning_enabled:
            return
        for text, kind in provider_reasoning_text(value):
            text = "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF)
            text = text[: 4000 - reasoning_length]
            for offset in range(0, len(text), 2000):
                part = {"text": text[offset : offset + 2000], "kind": kind, "stage": stage}
                reasoning_length += len(part["text"])
                reasoning.append(part)
                if on_reasoning:
                    await on_reasoning(part)

    async def publish_unit(raw):
        started = monotonic()
        try:
            unit = AnswerUnit.model_validate(raw, strict=True)
            result = check_answer_units(
                [unit],
                sources,
                user_message,
                documents=documents,
                content_policy=pipeline.get("content_policy", "repair"),
                diagnostics=diagnostics,
                seen_reasons=seen_reasons,
                trusted_action_urls=trusted_action_urls,
                approved_phones=approved_phones,
                allow_canonical_public_law=(
                    not context.summary.strip()
                    and not history
                    and not clarification_answer
                    and not image_base64
                    and not regenerate_from_summary
                ),
            )
        except (ValidationError, ValueError):
            raise AgentContractError("Invalid answer unit or citation") from None
        metrics["validation_ms"] += (monotonic() - started) * 1000
        for item in result:
            delta = ("\n\n" if checked_units else "") + item["text"]
            if (
                sum(len(part["text"]) for part in checked_units)
                + 2 * len(checked_units)
                + len(item["text"])
                > ASSISTANT_REPLY_MAX_LENGTH
            ):
                raise AgentContractError("Natural answer exceeds the reply limit")
            checked_units.append(item)
            if on_reply_delta:
                await on_reply_delta(delta)

    understood = None
    safety_sent = False
    summary_updates_blocked = False

    async def publish_understanding(raw):
        nonlocal understood, safety_sent, summary_updates_blocked
        understanding, rejected = _validated_understanding(raw, evidence_texts, diagnostics)
        summary_updates_blocked = summary_updates_blocked or rejected
        evidence = understanding.evidence
        understood = understanding
        await analyze(
            "understanding",
            f"本輪需求：{_INTENT_LABELS[understanding.intent]}；資料範圍：{_SCOPE_LABELS[understanding.scope]}。",
            evidence,
            limitations=[understanding.limitation] if understanding.limitation else [],
        )
        if understanding.immediate_safety and not safety_sent:
            safety_sent = True
            await publish_unit({"text": _SAFETY_TEXT, "source_ids": []})
        return understanding

    async def complete(messages, *, first):
        nonlocal summary_updates_blocked
        await progress("waiting_model")
        request_messages = list(messages)
        if not first:
            # Keep this stage instruction above the untrusted evidence handoff,
            # without changing the original request or inventing another turn.
            request_messages.insert(-1, {"role": "system", "content": V4_FINAL_STAGE_INSTRUCTION})
        response_format = V4_PLAN_FORMAT if first else _final_response_format(sources)
        if first and not pipeline.get("auto_clarify", True):
            response_format = deepcopy(V4_PLAN_FORMAT)
            branches = response_format["json_schema"]["schema"]["properties"]["plan"]["anyOf"]
            branches[:] = [
                branch for branch in branches if branch["$ref"] != "#/$defs/ClarificationPlan"
            ]
        options = model_request_options(
            config, request_messages, response_format, hide_reasoning=not reasoning_enabled
        )
        metrics["model_calls"] += 1
        started = monotonic()
        decoder = AnswerUnitJSONDecoder(wrapper_key="plan" if first else None)
        pending = []
        stage = "understanding" if first else "answer"

        async def consume(text):
            pending.extend(decoder.feed(text))
            if first:
                header = decoder.values
                if "understanding" in header and understood is None:
                    await publish_understanding(header["understanding"])
                if not {"understanding", "requires_synthesis", "retrieval"}.issubset(header):
                    return
                if header["requires_synthesis"] is not False or header["retrieval"] is not None:
                    return
                if understood.intent in {"legal_direction", "procedure", "deadline"}:
                    # A legal clarification may finish in stage one, but publish
                    # nothing until its required question and policy are validated.
                    return
            while pending:
                unit = pending.pop(0)
                await publish_unit(
                    _discard_direct_user_citation(unit, diagnostics) if first else unit
                )

        try:
            if on_reply_delta is not None or (reasoning_enabled and on_reasoning is not None):
                stream = await client.chat.completions.create(**options, stream=True)
                finish_reason = None
                validation_error = None
                content_length = 0
                try:
                    async with asyncio.timeout(None) as drain_timeout:
                        async for chunk in stream:
                            if _get(chunk, "error"):
                                raise AgentContractError("Provider reported a stream error")
                            for choice in chunk.choices:
                                if _get(choice, "index", 0) != 0:
                                    raise AgentContractError("Unexpected stream choice")
                                delta = choice.delta
                                text = _get(delta, "content")
                                if _get(delta, "refusal") or _get(delta, "tool_calls"):
                                    raise AgentContractError(
                                        "Unexpected provider refusal or tool call"
                                    )
                                if finish_reason is not None and (
                                    text or provider_reasoning_text(delta)
                                ):
                                    raise AgentContractError("Provider data after completion")
                                if text and not isinstance(text, str):
                                    raise AgentContractError("Invalid provider text")
                                content_length += len(text or "")
                                if validation_error is None:
                                    await emit_reasoning(delta, stage)
                                    try:
                                        if text:
                                            await consume(text)
                                    except (
                                        StreamReplyError,
                                        ValidationError,
                                        AgentContractError,
                                    ) as exc:
                                        validation_error = exc
                                        drain_timeout.reschedule(
                                            asyncio.get_running_loop().time()
                                            + VALIDATION_DRAIN_SECONDS
                                        )
                                # Drain discarded content without accumulating or publishing it.
                                if (
                                    validation_error is not None
                                    and content_length > MAX_STREAM_RESPONSE_LENGTH
                                ):
                                    raise validation_error
                                finish = _get(choice, "finish_reason")
                                if finish:
                                    finish_reason = finish
                                    if validation_error is None:
                                        if finish == "length":
                                            raise ModelOutputLimitError
                                        if finish != "stop":
                                            raise AgentContractError("Incomplete provider response")
                    if validation_error is not None:
                        raise validation_error
                    if finish_reason != "stop":
                        raise AgentContractError("Provider stream did not complete")
                except Exception:
                    if validation_error is not None:
                        raise validation_error from None
                    raise
                finally:
                    with suppress(Exception):
                        await stream.close()
            else:
                response = await client.chat.completions.create(**options)
                choices = _get(response, "choices")
                if _get(response, "error") or not choices or len(choices) != 1:
                    raise AgentContractError("Invalid v4 provider response")
                choice = choices[0]
                if _get(choice, "index", 0) != 0:
                    raise AgentContractError("Unexpected v4 provider choice")
                finish = _get(choice, "finish_reason")
                if finish == "length":
                    raise ModelOutputLimitError
                if finish != "stop":
                    raise AgentContractError("Incomplete v4 provider response")
                message = choice.message
                if _get(message, "refusal") or _get(message, "tool_calls"):
                    raise AgentContractError("Unexpected provider refusal or tool call")
                await emit_reasoning(message, stage)
                await consume(_get(message, "content") or "")
            raw = decoder.finish()
            if first and raw.get("requires_synthesis") is False and raw.get("retrieval") is None:
                # The decoder must verify the unmodified wire payload first.
                # Use the same canonical units as the already validated deltas.
                raw["answer_units"] = [
                    _discard_direct_user_citation(unit) for unit in raw["answer_units"]
                ]
            if first and understood is not None:
                raw["understanding"] = understood.model_dump()
            if raw.get("summary_update") is not None:
                try:
                    SummaryProposal.model_validate(raw["summary_update"], strict=True)
                except ValidationError:
                    summary_updates_blocked = True
                    raw["summary_update"] = None
                    _metadata_rejection(diagnostics, stage, "summary_update", "invalid_shape")
            answer = (
                ProviderPlanEnvelope.model_validate({"plan": raw}, strict=True).plan
                if first
                else FinalEnvelope.model_validate(raw, strict=True)
            )
            if first and isinstance(answer, ClarificationPlan):
                if not pipeline.get("auto_clarify", True):
                    raise AgentContractError("Clarification route is disabled")
                while pending:
                    await publish_unit(_discard_direct_user_citation(pending.pop(0), diagnostics))
            elif first and answer.requires_synthesis:
                pending.clear()
            elif pending:
                raise AgentContractError("Unpublished answer units")
            return answer
        except AgentContractError as exc:
            if exc.reason_code is None:
                exc.reason_code = (
                    "answer_validation"
                    if "answer" in str(exc).lower()
                    else "route_validation"
                    if "synthesis" in str(exc)
                    else "provider_protocol"
                )
            exc.stage = stage
            raise
        except (StreamReplyError, ValidationError, ValueError) as exc:
            reason = (
                "plan_wrapper"
                if isinstance(exc, StreamReplyError) and "wrapper" in str(exc)
                else "json_structure"
                if isinstance(exc, StreamReplyError)
                else "schema_validation"
                if isinstance(exc, ValidationError)
                else "value_validation"
            )
            raise AgentContractError(
                "Invalid v4 structured response", reason_code=reason, stage=stage
            ) from None
        finally:
            metrics["model_ms"] += (monotonic() - started) * 1000

    async def direct_answer(
        reply, route, analysis_summary, *, suggestions, actions=(), limitations=()
    ):
        metrics["route"] = route
        await progress("validating")
        unit = {"text": reply, "source_ids": []}
        payload = AssistantChatResponse.model_validate(
            {
                "reply": reply,
                "emotion": "未知",
                "emotion_color": "gray",
                "suggested_replies": suggestions,
                "action_buttons": [
                    {key: value for key, value in action.items() if key != "label"}
                    for action in actions
                ],
                "interaction_mode": "answer",
                "clarifying_questions": [],
            }
        ).model_dump()
        if on_reply_delta:
            await on_reply_delta(reply)
        await analyze(
            "answer",
            analysis_summary,
            limitations=limitations,
        )
        if diagnostics is not None:
            diagnostics.update(
                retrieved_sources=[],
                retrieved_count=0,
                cited_count=0,
                retrieval_status="not_requested",
                final_answer=[unit],
                execution=metrics,
                summary_update=None,
                clarification_decision={"accepted": False},
            )
            if expected_chat_test_contract_sha256 is not None:
                diagnostics["prompt_overlap"] = []
        return AgentResult(
            reply=json.dumps(payload, ensure_ascii=False),
            available_actions=list(actions),
            guidance={
                "contract_version": 4,
                "context_revision": context.revision,
                "summary_update": None,
                "clarification": None,
                "analysis": analyses,
                "execution": metrics,
                "retrieval": {"status": "not_requested", "data_types": [], "sources": []},
            },
        )

    await progress("preparing")
    if (
        pipeline.get("enable_skills", True)
        and not regenerate_from_summary
        and not image_base64
        and not clarification_answer
        and not is_urgent(user_message)
        and (contact := official_contact_reply(user_message, history))
    ):
        reply, actions = contact
        return await direct_answer(
            reply,
            "official_contact",
            "本輪聯絡電話採用已核對的官方資料。",
            suggestions=["我想了解申訴管道", "我想先整理情況"],
            actions=actions,
        )
    if (
        not history
        and not context.summary.strip()
        and not image_base64
        and not clarification_answer
        and not regenerate_from_summary
        and contextless_prior_legal_source_request(user_message)
    ):
        return await direct_answer(
            "目前這段對話沒有我先前的法律判斷，也沒有指定要核對的法規或條文，"
            "因此無法提供其官方法規網址、條文原文或資料日期。"
            "請貼上要核對的判斷，或提供法規名稱與條號；我才能依可核對的官方來源逐項確認。",
            "clarify_missing_prior_judgment",
            "本輪沒有先前法律判斷或指定條文可供核對，已請使用者提供目標。",
            suggestions=["我要貼上先前的判斷", "我要提供法規名稱與條號"],
            limitations=["未取得可核對的目標或官方法源。"],
        )
    scripts = (
        get_matching_scenario_scripts(
            context.summary if regenerate_from_summary else user_message,
            history=None if regenerate_from_summary else history,
            **({"catalog": test_catalog} if test_catalog is not None else {}),
        )
        if pipeline.get("enable_skills", True)
        else []
    )
    approved_phones = frozenset(
        re.sub(r"\D", "", contact.phone) for contact in contact_catalog()[1]
    )
    actions = [
        action
        for action in available_actions(scripts)
        if action["action"] != "tel" or re.sub(r"\D", "", action["phone_number"]) in approved_phones
    ]
    trusted_action_urls = tuple(action["url"] for action in actions if action["action"] == "url")
    messages = [
        {
            "role": "system",
            "content": assemble_service_instruction(config.agent_prompt_sections),
        },
        {
            "role": "system",
            "content": V4_INSTRUCTION
            + "\n本次服務日期（Asia/Taipei）："
            + datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat(),
        },
    ]
    if scripts:
        messages.append({"role": "system", "content": format_scenario_instruction(scripts)})
    messages.append(
        {
            "role": "system",
            "content": json.dumps(
                {
                    "request_policy": {
                        "rag_enabled": rag_enabled,
                        "auto_clarify": pipeline.get("auto_clarify", True),
                        "update_summary": pipeline.get("extract_facts", True),
                        "model_selection_mode": pipeline.get("model_selection_mode", True),
                    }
                },
                ensure_ascii=False,
            ),
        }
    )
    if not regenerate_from_summary:
        messages.extend(history or [])
    request_data = {
        "conversation_summary": context.summary,
        "summary_origin": context.summary_origin,
        "message": user_message,
        "clarification_answer": clarification_data,
    }
    if regenerate_from_summary:
        messages.append(
            {
                "role": "system",
                "content": "本輪是使用者編輯摘要後重新回答。conversation_summary是唯一案件事實來源，"
                "original_request_for_intent_only僅表示原本想獲得哪種協助，不能採用其中的人物、身分、"
                "行為或其他舊事實，也不能從舊歷史恢復使用者刪除的內容。understanding.evidence只能"
                "逐字引用conversation_summary。summary_update必須為null，不更改已編輯摘要。",
            }
        )
        request_data["original_request_for_intent_only"] = user_message
        request_data["message"] = "請依目前摘要重新回答原始需求"
    text = json.dumps(request_data, ensure_ascii=False)
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_base64}},
            ]
            if image_base64
            else text,
        }
    )
    plan = await complete(messages, first=True)
    if understood is None:
        await publish_understanding(plan.understanding.model_dump())
    retrieval_status = "disabled" if not rag_enabled else "not_requested"
    searched_types = []
    answer = plan
    if plan.requires_synthesis:
        metrics["route"] = (
            "understand_retrieve_answer" if rag_enabled and plan.retrieval else "understand_answer"
        )
        if rag_enabled and plan.retrieval:
            searched_types = _retrieval_types(plan.retrieval, plan.understanding)
            sources, documents, retrieval_status = await _retrieve_v4(
                rag, plan.retrieval, plan.understanding, config, metrics, diagnostics, progress
            )
        source_summary = {
            "ok": f"本輪資料庫檢索取得 {len(sources)} 筆資料，接著核對回覆引用。",
            "empty": "本輪資料庫檢索沒有找到可用資料。",
            "failed": "本輪資料庫檢索未能完成，無法據此確認法源。",
            "disabled": "本輪已關閉資料庫檢索。",
            "not_requested": "本輪沒有提出資料庫檢索需求。",
        }[retrieval_status]
        await analyze(
            "sources",
            source_summary,
            source_labels=[source["label"] for source in sources],
            limitations=[] if sources else ["未取得可供本輪引用的資料庫法源。"],
        )
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "validated_understanding": plan.understanding.model_dump(),
                        "retrieval_status": retrieval_status,
                        "untrusted_retrieved_documents": documents,
                        "handoff_note": "理解摘要不是法源；依據實際條文生成自然最終回答。保留使用者編輯摘要的優先性。",
                        "safety_preface_already_shown": _SAFETY_TEXT if safety_sent else None,
                    },
                    ensure_ascii=False,
                ),
            }
        )
        answer = await complete(messages, first=False)
    else:
        metrics["route"] = (
            "clarify_before_retrieval" if isinstance(plan, ClarificationPlan) else "direct_support"
        )
    await progress("validating")
    summary = (
        validated_summary(
            answer.summary_update or plan.summary_update,
            context,
            user_texts,
            plan.understanding.scope,
            diagnostics=diagnostics,
        )
        if pipeline.get("extract_facts", True)
        and not regenerate_from_summary
        and not summary_updates_blocked
        else None
    )
    question = None
    if (
        answer.question
        and pipeline.get("auto_clarify", True)
        and not plan.understanding.immediate_safety
    ):
        question = {
            "question_id": "conversation." + secrets.token_hex(12),
            **answer.question.model_dump(),
            "context_scope": "scenario"
            if plan.understanding.scope in {"third_person", "hypothetical"}
            else "personal",
            "context_revision": context.revision + int(summary is not None),
        }
        if not pipeline.get("model_selection_mode", True):
            question.update(selection_mode="single", max_selections=1)
        question["validation_token"] = sign_clarification(question, contract_version=4)
    payload = {
        "reply": "\n\n".join(item["text"] for item in checked_units),
        "emotion": answer.emotion,
        "emotion_color": answer.emotion_color,
        "suggested_replies": answer.suggested_replies,
        "action_buttons": [action.model_dump() for action in answer.action_buttons],
        "interaction_mode": "clarify" if question else "answer",
        "clarifying_questions": [question["question"]] if question else [],
    }
    try:
        payload = AssistantChatResponse.model_validate(payload).model_dump()
    except ValidationError:
        raise AgentContractError("Invalid v4 response projection") from None
    cited_ids = {identifier for item in checked_units for identifier in item["source_ids"]}
    cited_sources = [source for source in sources if source["doc_id"] in cited_ids]
    await analyze(
        "answer",
        "已完成回覆結構及引用對應檢查；仍不能取代個案法律專業判斷。",
        source_labels=[source["label"] for source in cited_sources],
        limitations=[plan.understanding.limitation] if plan.understanding.limitation else [],
    )
    if diagnostics is not None:
        if expected_chat_test_contract_sha256 is not None:
            diagnostics["prompt_overlap"] = _visible_prompt_overlap(
                payload["reply"],
                [
                    assemble_service_instruction(config.agent_prompt_sections),
                    V4_INSTRUCTION,
                    V4_FINAL_STAGE_INSTRUCTION,
                    format_scenario_instruction(scripts),
                ],
            )
        diagnostics.update(
            retrieved_sources=sources,
            retrieved_count=len(sources),
            cited_count=len(cited_sources),
            retrieval_status=retrieval_status,
            final_answer=checked_units,
            execution=metrics,
            summary_update=summary,
            clarification_decision={"accepted": bool(question)},
        )
    return AgentResult(
        reply=json.dumps(payload, ensure_ascii=False),
        rag_used=bool(sources),
        sources=cited_sources,
        available_actions=actions,
        guidance={
            "contract_version": 4,
            "context_revision": context.revision,
            "summary_update": summary,
            "clarification": question,
            "analysis": analyses,
            "execution": metrics,
            "retrieval": {
                "status": retrieval_status,
                "data_types": searched_types,
                "sources": sources,
            },
            **({"reasoning": reasoning} if reasoning else {}),
        },
    )
