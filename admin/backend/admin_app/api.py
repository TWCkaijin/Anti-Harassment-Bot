"""Admin configuration and diagnostic job APIs, behind session middleware."""

import asyncio
import hashlib
import uuid
from dataclasses import asdict
from typing import Literal

from flask import Blueprint, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from admin_app import infrastructure
from admin_app.jobs import JobFailure, canonical, register_handler, submit_job
from rag_core.agents.openrouter_agent import get_default_prompt_sections
from rag_core.core.agent_errors import (
    ApplicableLawsContractMismatchError,
    ChatTestContractMismatchError,
)
from rag_core.core.logger import get_logger
from rag_core.core.runtime_config import (
    ConfigConflictError,
    RuntimeConfig,
    get_runtime_config,
    reset_runtime_config,
    resolve_runtime_config,
    seed_runtime_config_if_missing,
    update_runtime_config,
)
from rag_core.core.scenario_scripts import (
    ScenarioRevisionConflict,
    _parse_script,
    delete_scenario_script,
    get_scenario_revision,
    read_scenario_catalog,
    seed_example_scenario_scripts,
    upsert_scenario_script,
    use_scenario_snapshot,
)
from rag_core.core.scenario_scripts import (
    get_scenario_scripts as get_enabled_scenario_catalog,
)

logger = get_logger(__name__)
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _log_operation_failure(operation: str, error: Exception) -> None:
    logger.exception(
        "Admin operation failed: %s",
        operation,
        extra={
            "event": "admin_operation_failed",
            "operation": operation,
            "error_type": type(error).__name__,
        },
    )


def _public_config(runtime_config=None, catalog_state=None):
    from rag_core.agents.guided_v4 import (
        applicable_laws_contract_sha256,
        applicable_laws_scenario_catalog_sha256,
        chat_test_contract_sha256,
    )

    runtime_config = runtime_config or get_runtime_config(force_refresh=True, strict=True)
    config = runtime_config.public_dict()
    prompt_sections = get_default_prompt_sections()
    prompt_sections.update(config.get("agent_prompt_sections") or {})
    config["agent_prompt_sections"] = prompt_sections
    catalog, skill_revision = catalog_state or read_scenario_catalog()
    with use_scenario_snapshot(catalog):
        catalog = (
            tuple(get_enabled_scenario_catalog())
            if runtime_config.pipeline.get("enable_skills", True)
            else ()
        )
    config["skills_revision"] = skill_revision
    config["applicable_laws_contract_sha256"] = applicable_laws_contract_sha256(
        runtime_config, catalog
    )
    config["applicable_laws_scenario_catalog_sha256"] = applicable_laws_scenario_catalog_sha256(
        runtime_config, catalog
    )
    config["chat_test_contract_sha256"] = chat_test_contract_sha256(runtime_config, catalog)
    return config


def _require_admin() -> tuple[str | None, tuple[object, int] | None]:
    identity = getattr(g, "admin_identity", None)
    if identity:
        return identity, None
    return None, (jsonify({"detail": "Unauthorized"}), 401)


def _expected_revision(*, skills=False):
    revision = request.headers.get("If-Match", "").strip().strip('"')
    if not revision:
        raise JobFailure("revision_required", "If-Match revision is required", http_status=428)
    if len(revision) > 128 or revision == "*" or revision.startswith("W/"):
        raise JobFailure("invalid_revision", "Invalid revision", http_status=422)
    if skills:
        if not revision.isdigit():
            raise JobFailure(
                "invalid_revision", "Skills revision must be an integer", http_status=422
            )
        return int(revision)
    return revision


def _config_response():
    data = _public_config()
    response = jsonify(data)
    response.set_etag(str(data["revision"]))
    return response


@admin_bp.route("/rerank-models", methods=["GET"])
def rerank_models():
    _, error = _require_admin()
    if error:
        return error
    from rag_core.core.rerank_models import list_rerank_models

    try:
        return jsonify({"data": list_rerank_models()})
    except Exception:
        return jsonify({"detail": "無法取得 rerank 模型清單；可保留設定或手填模型 ID。"}), 502


@admin_bp.route("/config", methods=["GET"])
def get_config():
    _, error = _require_admin()
    if error:
        return error
    return _config_response()


def run_chat_test(payload: dict, snapshot_config: RuntimeConfig | None = None):
    """Service-level diagnostic. Only the authenticated worker invokes it remotely."""
    previous = getattr(g, "admin_diagnostic", False)
    g.admin_diagnostic = True
    try:
        response = current_app.make_response(_run_chat_test(payload, snapshot_config))
    finally:
        g.admin_diagnostic = previous
    response.headers["Cache-Control"] = "no-store"
    return response


def _run_chat_test(payload: dict, snapshot_config: RuntimeConfig | None = None):
    # Keep imports local: ordinary chat never imports or accepts admin overrides.
    from rag_core import chat_service as chat_api

    class ChatTestRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        chat_request: chat_api.ChatRequest = Field(alias="request")
        overrides: dict = Field(default_factory=dict)
        output_contract: Literal["chat", "applicable_laws_v1"] = "chat"
        expected_applicable_laws_contract_sha256: str | None = Field(
            default=None, pattern="^[0-9a-f]{64}$"
        )
        expected_chat_test_contract_sha256: str | None = Field(
            default=None, pattern="^[0-9a-f]{64}$"
        )

    try:
        data = ChatTestRequest.model_validate(payload)
        if data.chat_request.stream:
            return jsonify({"detail": "Admin chat tests return JSON; stream must be false"}), 422
        if data.output_contract == "applicable_laws_v1" and data.chat_request.contract_version != 4:
            return jsonify(
                {"detail": "Applicable-law prediction requires chat contract version 4"}
            ), 422
        if (
            data.expected_applicable_laws_contract_sha256 is not None
            and data.output_contract != "applicable_laws_v1"
        ):
            return jsonify({"detail": "Applicable-law fingerprint requires prediction mode"}), 422
        if data.expected_chat_test_contract_sha256 is not None and (
            data.output_contract != "chat" or data.chat_request.contract_version != 4
        ):
            return jsonify({"detail": "Chat-test fingerprint requires v4 chat"}), 422
        config = snapshot_config or resolve_runtime_config(get_runtime_config(), data.overrides)
    except ValidationError as exc:
        return chat_api._request_validation_error(exc)
    except ValueError as exc:
        return jsonify({"detail": str(exc)}), 422
    if data.chat_request.image_base64 and not config.enable_image_upload:
        return chat_api._image_upload_disabled_error()
    diagnostics = {"effective_config": config.public_dict()}
    try:
        arguments, was_anonymized = chat_api._prepare_agent_input(
            data.chat_request, config, diagnostics, include_input_stages=True
        )
        if data.output_contract == "applicable_laws_v1":
            arguments["output_contract"] = data.output_contract
            arguments["expected_applicable_laws_contract_sha256"] = (
                data.expected_applicable_laws_contract_sha256
            )
        if data.expected_chat_test_contract_sha256 is not None:
            arguments["expected_chat_test_contract_sha256"] = (
                data.expected_chat_test_contract_sha256
            )
        result = asyncio.run(chat_api.get_agent().run(**arguments))
    except ChatTestContractMismatchError:
        return jsonify(
            {
                "code": "chat_test_contract_mismatch",
                "detail": "對話測試的提示或情境腳本已變更，請重新準備測試。",
                "retryable": False,
            }
        ), 409
    except ApplicableLawsContractMismatchError:
        return jsonify(
            {
                "code": "applicable_laws_contract_mismatch",
                "detail": "適用法規測試的提示或情境腳本已變更，請重新準備測試。",
                "retryable": False,
            }
        ), 409
    except Exception as exc:
        return chat_api._agent_error(config, exc)
    try:
        if data.output_contract == "applicable_laws_v1":
            from rag_core.agents.guided_v4 import ApplicableLawPrediction
            from rag_core.agents.openrouter_agent import (
                AgentContractError,
                ApplicableLawPredictionResult,
            )

            if not isinstance(result, ApplicableLawPredictionResult):
                raise AgentContractError("Invalid applicable-law result type")
            prediction = ApplicableLawPrediction.model_validate(result.prediction)
            return jsonify(
                {
                    "output_contract": data.output_contract,
                    "prediction": prediction.model_dump(),
                    "diagnostics": diagnostics,
                }
            )
        response = chat_api._response_payload(
            result, str(uuid.uuid4()), was_anonymized, config, data.chat_request
        )
    except Exception as exc:
        return chat_api._retryable_error(config, exc)
    payload = jsonify({"response": response, "diagnostics": diagnostics})
    payload.headers["Cache-Control"] = "no-store"
    return payload


def _snapshot():
    config = get_runtime_config(force_refresh=True, strict=True)
    catalog, revision = read_scenario_catalog()
    catalog = tuple(script for script in catalog if script.enabled)
    value = {
        "config": asdict(config),
        "skills": [script.public_dict() for script in catalog],
        "prompt_contract": _public_config(config, (catalog, revision)),
    }
    return value, hashlib.sha256(canonical(value)).hexdigest()


@admin_bp.post("/test-jobs")
def submit_test_job():
    from rag_core import chat_service

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("request"), dict):
        return jsonify({"detail": "A diagnostic request is required"}), 422
    try:
        chat = chat_service.ChatRequest.model_validate(payload["request"])
        if chat.stream:
            raise ValueError("Admin diagnostic jobs require stream=false")
        if payload.get("output_contract", "chat") not in {"chat", "applicable_laws_v1"}:
            raise ValueError("Unsupported diagnostic output contract")
        snapshot, fingerprint = _snapshot()
        effective = resolve_runtime_config(
            RuntimeConfig(**snapshot["config"]), payload.get("overrides", {})
        )
        if chat.image_base64 and not effective.enable_image_upload:
            raise ValueError("Image upload is disabled")
        snapshot.update(base_fingerprint=fingerprint, effective_config=asdict(effective))
        job = submit_job(
            "chat_test",
            payload,
            snapshot=snapshot,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    except (ValueError, ValidationError) as exc:
        return jsonify({"detail": str(exc)}), 422
    return jsonify({"job_id": job["job_id"], "status": job["status"]}), 202


def execute_chat_test(job, context):
    import json

    completed = job.get("checkpoint", {}).get("diagnostic_completed")
    completed_ref = job.get("checkpoint", {}).get("diagnostic_completed_ref")
    if completed_ref:
        completed = json.loads(infrastructure.object_store().read_bytes(completed_ref))
    if completed is not None:
        return _diagnostic_result(completed["result"], completed["http_status"])
    snapshot = job["snapshot"]
    _, fingerprint = _snapshot()
    if fingerprint != snapshot["base_fingerprint"]:
        raise JobFailure(
            "config_snapshot_changed", "設定或 Skills 已變更；請重新準備測試。", http_status=409
        )
    scripts = tuple(_parse_script(row["id"], row) for row in snapshot["skills"])
    if any(script is None for script in scripts):
        raise JobFailure(
            "invalid_skill_snapshot", "Stored Skill snapshot is invalid", http_status=409
        )
    context.external_call_started("diagnostic_model")
    with use_scenario_snapshot(scripts):
        response = current_app.make_response(
            run_chat_test(job["payload"], RuntimeConfig(**snapshot["effective_config"]))
        )
    result = response.get_json()
    # A connection failure after dispatch can have an unknown paid outcome.
    if response.status_code >= 500 and (result or {}).get("retryable"):
        raise JobFailure(
            "external_outcome_unknown",
            "模型回應未完成，需要人工確認。",
            http_status=response.status_code,
        )
    completed = {"result": result, "http_status": response.status_code}
    if len(canonical(completed)) > 150000:
        reference = infrastructure.object_store().put_bytes(
            f"jobs/chat_test/{job['job_id']}/diagnostic-{context.lease_token}.json",
            canonical(completed),
            "application/json",
        )
        checkpoint = {"diagnostic_completed_ref": reference}
    else:
        checkpoint = {"diagnostic_completed": completed}
    context.external_call_finished(checkpoint=checkpoint)
    return _diagnostic_result(result, response.status_code)


def _diagnostic_result(result, http_status):
    if http_status >= 400:
        raise JobFailure(
            (result or {}).get("code", "diagnostic_failed"),
            (result or {}).get("detail", "Diagnostic failed"),
            retryable=(result or {}).get("retryable", False),
            http_status=http_status,
        )
    return result


register_handler("chat_test", execute_chat_test)


@admin_bp.route("/config", methods=["PUT"])
def put_config():
    identity, error = _require_admin()
    if error:
        return error
    revision = _expected_revision()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"detail": "JSON body must be an object"}), 400
    try:
        update_runtime_config(payload, updated_by=identity or "admin", expected_revision=revision)
    except ConfigConflictError as exc:
        return jsonify({"code": "config_conflict", "detail": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"detail": str(exc)}), 422
    except Exception as exc:
        _log_operation_failure("update_runtime_config", exc)
        return jsonify({"detail": f"Failed to update runtime config: {type(exc).__name__}"}), 500
    return _config_response()


@admin_bp.route("/config/seed", methods=["POST"])
def seed_config():
    identity, error = _require_admin()
    if error:
        return error
    revision = _expected_revision()
    try:
        seed_runtime_config_if_missing(updated_by=identity or "admin", expected_revision=revision)
    except ConfigConflictError as exc:
        return jsonify({"code": "config_conflict", "detail": str(exc)}), 409
    except Exception as exc:
        _log_operation_failure("seed_runtime_config", exc)
        return jsonify({"detail": f"Failed to seed runtime config: {type(exc).__name__}"}), 500
    return _config_response()


@admin_bp.route("/config/reset", methods=["POST"])
def reset_config():
    identity, error = _require_admin()
    if error:
        return error
    revision = _expected_revision()
    try:
        reset_runtime_config(updated_by=identity or "admin", expected_revision=revision)
    except ConfigConflictError as exc:
        return jsonify({"code": "config_conflict", "detail": str(exc)}), 409
    except Exception as exc:
        _log_operation_failure("reset_runtime_config", exc)
        return jsonify({"detail": f"Failed to reset runtime config: {type(exc).__name__}"}), 500
    return _config_response()


@admin_bp.route("/scenario-scripts/seed", methods=["POST"])
def seed_scenario_scripts():
    identity, error = _require_admin()
    if error:
        return error
    revision = _expected_revision(skills=True)
    try:
        scripts = seed_example_scenario_scripts(
            updated_by=identity or "admin", expected_revision=revision
        )
    except ScenarioRevisionConflict as exc:
        return jsonify({"code": "skills_conflict", "detail": str(exc)}), 409
    except Exception as exc:
        _log_operation_failure("seed_scenario_scripts", exc)
        return jsonify({"detail": f"Failed to seed scenario scripts: {type(exc).__name__}"}), 500
    return jsonify(
        {
            "script_ids": [script.script_id for script in scripts],
            "revision": get_scenario_revision(),
        }
    )


@admin_bp.route("/scenario-scripts", methods=["GET"])
def get_scenario_scripts():
    _, error = _require_admin()
    if error:
        return error
    try:
        scripts, revision = read_scenario_catalog()
        response = jsonify(
            {"skills": [script.public_dict() for script in scripts], "revision": revision}
        )
        response.set_etag(str(revision))
        return response
    except ScenarioRevisionConflict as exc:
        return jsonify({"code": "skills_conflict", "detail": str(exc)}), 409
    except Exception as exc:
        _log_operation_failure("list_scenario_scripts", exc)
        return jsonify({"detail": f"Failed to load scenario scripts: {type(exc).__name__}"}), 500


@admin_bp.route("/scenario-scripts/<script_id>", methods=["PUT"])
def put_scenario_script(script_id: str):
    identity, error = _require_admin()
    if error:
        return error
    revision = _expected_revision(skills=True)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"detail": "JSON body must be an object"}), 400
    try:
        response = jsonify(
            upsert_scenario_script(
                script_id, payload, identity or "admin", expected_revision=revision
            ).public_dict()
        )
        response.headers["X-Skills-Revision"] = str(get_scenario_revision())
        return response
    except ScenarioRevisionConflict as exc:
        return jsonify({"code": "skills_conflict", "detail": str(exc)}), 409
    except ValueError as exc:
        return jsonify({"detail": str(exc)}), 422
    except Exception as exc:
        _log_operation_failure("upsert_scenario_script", exc)
        return jsonify({"detail": f"Failed to save scenario script: {type(exc).__name__}"}), 500


@admin_bp.route("/scenario-scripts/<script_id>", methods=["DELETE"])
def remove_scenario_script(script_id: str):
    _, error = _require_admin()
    if error:
        return error
    revision = _expected_revision(skills=True)
    try:
        delete_scenario_script(script_id, expected_revision=revision)
    except ScenarioRevisionConflict as exc:
        return jsonify({"code": "skills_conflict", "detail": str(exc)}), 409
    except Exception as exc:
        _log_operation_failure("delete_scenario_script", exc)
        return jsonify({"detail": f"Failed to delete scenario script: {type(exc).__name__}"}), 500
    return "", 204
