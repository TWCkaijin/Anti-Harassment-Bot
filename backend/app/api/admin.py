"""
Admin API for runtime configuration.

The first version uses an admin API key header. It is intentionally small and
server-side only; do not expose secrets through this route.
"""

import asyncio
import uuid

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.agents.openrouter_agent import get_default_prompt_sections
from backend.app.core.config import get_settings
from backend.app.core.logger import get_logger
from backend.app.core.runtime_config import (
    get_runtime_config,
    reset_runtime_config,
    resolve_runtime_config,
    seed_runtime_config_if_missing,
    update_runtime_config,
)
from backend.app.core.scenario_scripts import (
    delete_scenario_script,
    list_scenario_scripts,
    seed_example_scenario_scripts,
    upsert_scenario_script,
)

settings = get_settings()
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


def _public_config():
    config = get_runtime_config(force_refresh=True).public_dict()
    prompt_sections = get_default_prompt_sections()
    prompt_sections.update(config.get("agent_prompt_sections") or {})
    config["agent_prompt_sections"] = prompt_sections
    return config


def _admin_identity() -> str | None:
    configured_token = settings.admin_api_key
    if not configured_token:
        return None

    bearer = request.headers.get("Authorization", "")
    header_token = request.headers.get("X-Admin-Token", "")
    token = header_token
    if bearer.lower().startswith("bearer "):
        token = bearer[7:].strip()

    if token and token == configured_token:
        return request.headers.get("X-Admin-User", "admin")
    return None


def _require_admin() -> tuple[str | None, tuple[object, int] | None]:
    identity = _admin_identity()
    if identity:
        return identity, None
    if not settings.admin_api_key:
        return None, (jsonify({"detail": "Admin API is disabled"}), 503)
    return None, (jsonify({"detail": "Unauthorized"}), 401)


@admin_bp.route("/config", methods=["GET"])
def get_config():
    _, error = _require_admin()
    if error:
        return error
    return jsonify(_public_config())


@admin_bp.route("/chat-test", methods=["POST"])
def chat_test():
    """Run an authenticated, ephemeral comparison using an isolated settings snapshot."""
    _, error = _require_admin()
    if error:
        return error
    # Keep imports local: ordinary chat never imports or accepts admin overrides.
    from backend.app.api import chat as chat_api

    class ChatTestRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        chat_request: chat_api.ChatRequest = Field(alias="request")
        overrides: dict = Field(default_factory=dict)

    try:
        data = ChatTestRequest.model_validate(request.get_json(silent=True))
        if data.chat_request.stream:
            return jsonify({"detail": "Admin chat tests return JSON; stream must be false"}), 422
        config = resolve_runtime_config(get_runtime_config(), data.overrides)
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
        result = asyncio.run(chat_api.get_agent().run(**arguments))
    except Exception as exc:
        return chat_api._agent_error(config, exc)
    try:
        response = chat_api._response_payload(
            result, str(uuid.uuid4()), was_anonymized, config, data.chat_request
        )
    except Exception as exc:
        return chat_api._retryable_error(config, exc)
    payload = jsonify({"response": response, "diagnostics": diagnostics})
    payload.headers["Cache-Control"] = "no-store"
    return payload


@admin_bp.route("/config", methods=["PUT"])
def put_config():
    identity, error = _require_admin()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"detail": "JSON body must be an object"}), 400
    try:
        update_runtime_config(payload, updated_by=identity or "admin")
    except ValueError as exc:
        return jsonify({"detail": str(exc)}), 422
    except Exception as exc:
        _log_operation_failure("update_runtime_config", exc)
        return jsonify({"detail": f"Failed to update runtime config: {type(exc).__name__}"}), 500
    return jsonify(_public_config())


@admin_bp.route("/config/seed", methods=["POST"])
def seed_config():
    identity, error = _require_admin()
    if error:
        return error
    try:
        seed_runtime_config_if_missing(updated_by=identity or "admin")
    except Exception as exc:
        _log_operation_failure("seed_runtime_config", exc)
        return jsonify({"detail": f"Failed to seed runtime config: {type(exc).__name__}"}), 500
    return jsonify(_public_config())


@admin_bp.route("/config/reset", methods=["POST"])
def reset_config():
    identity, error = _require_admin()
    if error:
        return error
    try:
        reset_runtime_config(updated_by=identity or "admin")
    except Exception as exc:
        _log_operation_failure("reset_runtime_config", exc)
        return jsonify({"detail": f"Failed to reset runtime config: {type(exc).__name__}"}), 500
    return jsonify(_public_config())


@admin_bp.route("/scenario-scripts/seed", methods=["POST"])
def seed_scenario_scripts():
    identity, error = _require_admin()
    if error:
        return error
    try:
        scripts = seed_example_scenario_scripts(updated_by=identity or "admin")
    except Exception as exc:
        _log_operation_failure("seed_scenario_scripts", exc)
        return jsonify({"detail": f"Failed to seed scenario scripts: {type(exc).__name__}"}), 500
    return jsonify({"script_ids": [script.script_id for script in scripts]})


@admin_bp.route("/scenario-scripts", methods=["GET"])
def get_scenario_scripts():
    _, error = _require_admin()
    if error:
        return error
    try:
        return jsonify({"skills": [script.public_dict() for script in list_scenario_scripts(True)]})
    except Exception as exc:
        _log_operation_failure("list_scenario_scripts", exc)
        return jsonify({"detail": f"Failed to load scenario scripts: {type(exc).__name__}"}), 500


@admin_bp.route("/scenario-scripts/<script_id>", methods=["PUT"])
def put_scenario_script(script_id: str):
    identity, error = _require_admin()
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"detail": "JSON body must be an object"}), 400
    try:
        return jsonify(
            upsert_scenario_script(script_id, payload, identity or "admin").public_dict()
        )
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
    try:
        delete_scenario_script(script_id)
    except Exception as exc:
        _log_operation_failure("delete_scenario_script", exc)
        return jsonify({"detail": f"Failed to delete scenario script: {type(exc).__name__}"}), 500
    return "", 204
