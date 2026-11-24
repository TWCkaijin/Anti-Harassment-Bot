"""Prepare masked, budgeted agent input from a validated chat request."""

from rag_core.chat.models import ChatRequest
from rag_core.core.anonymizer import anonymize
from rag_core.core.case_context import (
    SUMMARY_MAX_LENGTH,
    V4_CASE_SCHEMA_VERSION,
    CaseContext,
)
from rag_core.core.clarification_tokens import validate_clarification_answer
from rag_core.core.pipeline_config import (
    trim_request_history,
)
from rag_core.core.runtime_config import (
    image_upload_enabled,
)


def prepare_agent_input(
    req_obj: ChatRequest,
    runtime_config,
    diagnostics: dict | None = None,
    *,
    include_input_stages: bool = False,
) -> tuple[dict, bool]:
    original_history = [{"role": msg.role, "content": msg.content} for msg in req_obj.history]
    history, history_counts = trim_request_history(original_history, runtime_config.pipeline)
    trimmed_history = history
    message = req_obj.message
    was_anonymized = False
    mask_message = runtime_config.enable_anonymization and runtime_config.pipeline["mask_message"]
    mask_case = runtime_config.enable_anonymization and runtime_config.pipeline["mask_case_context"]
    pii = {
        stage: {"enabled": enabled, "kinds": set(), "changed_values": 0}
        for stage, enabled in (("message", mask_message), ("case_context", mask_case))
    }

    def clean_text(value, stage, case_limit=300):
        nonlocal was_anonymized
        if isinstance(value, str) and pii[stage]["enabled"]:
            result = anonymize(value)
            was_anonymized = was_anonymized or result.was_modified
            pii[stage]["kinds"].update(result.detected_types)
            pii[stage]["changed_values"] += int(result.was_modified)
            return result.anonymized[:case_limit] if stage == "case_context" else result.anonymized
        if isinstance(value, dict):
            # Only case values carry user prose. Signed question metadata must
            # remain byte-for-byte intact for its downstream signature check.
            return {
                key: clean_text(item, stage, SUMMARY_MAX_LENGTH if key == "summary" else case_limit)
                if key not in {"validation_token", "question_id", "allowed_values"}
                else item
                for key, item in value.items()
            }
        if isinstance(value, list):
            cleaned = [clean_text(item, stage) for item in value]
            if stage == "case_context" and all(isinstance(item, str) for item in cleaned):
                # Distinct selections can contain different identifiers which
                # redact to the same placeholder. Preserve their count and order
                # without retaining any original identifying text.
                used = set()
                for index, item in enumerate(cleaned):
                    if item in used:
                        suffix_index = index + 1
                        while True:
                            suffix = f"（另一已遮罩值{suffix_index}）"
                            distinct = item[: 300 - len(suffix)] + suffix
                            if distinct not in used:
                                break
                            suffix_index += 1
                        cleaned[index] = distinct
                    used.add(cleaned[index])
            return cleaned
        return value

    message = clean_text(message, "message")
    history = [{**item, "content": clean_text(item["content"], "message")} for item in history]
    arguments = {
        "user_message": message,
        "history": history,
        "image_base64": req_obj.image_base64 if image_upload_enabled(runtime_config) else None,
        "use_rag": req_obj.use_rag and runtime_config.pipeline["enable_rag"],
        "runtime_config": runtime_config,
    }
    arguments.update(
        contract_version=4,
        case_context=clean_text(
            (req_obj.case_context or CaseContext(schema_version=V4_CASE_SCHEMA_VERSION)).model_dump(
                exclude_none=True
            ),
            "case_context",
        ),
        clarification_answer=clean_text(
            req_obj.clarification_answer.model_dump(exclude_none=True), "case_context"
        )
        if req_obj.clarification_answer
        else None,
        regenerate_from_summary=req_obj.regenerate_from_summary,
    )
    if req_obj.clarification_answer:
        issued = validate_clarification_answer(
            req_obj.clarification_answer.model_dump(exclude_none=True),
            contract_version=4,
        )
        # A typed answer is verified before masking. The internal bounds
        # prevent rechecking masked values against the original token's
        # choices; the public request schema never accepts this argument.
        arguments["clarification_constraints"] = {
            key: issued[key]
            for key in (
                "question_id",
                "fact_key",
                "selection_mode",
                "max_selections",
                "context_scope",
                "context_revision",
            )
            if key in issued
        }
    if diagnostics is not None:
        diagnostics["history"] = history_counts
        diagnostics["pii"] = {
            stage: {**counts, "kinds": sorted(counts["kinds"])} for stage, counts in pii.items()
        }
        if include_input_stages:
            original_summary = req_obj.case_context.summary if req_obj.case_context else ""
            summary = arguments.get("case_context", {}).get("summary", "")
            # Only the authenticated, non-persistent admin test opts into raw
            # before/after views; regular chats never create these snapshots.
            diagnostics["input_stages"] = {
                "message": {
                    "before": req_obj.message,
                    "after": message,
                    "changed": req_obj.message != message,
                },
                "history": {
                    "before": original_history,
                    "after_trim": trimmed_history,
                    "after": history,
                    "changed": original_history != history,
                },
                "summary": {
                    "before": original_summary,
                    "after": summary,
                    "changed": original_summary != summary,
                },
            }
        arguments["diagnostics"] = diagnostics
    return arguments, was_anonymized
