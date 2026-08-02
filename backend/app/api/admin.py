"""
Admin API for runtime configuration.

The first version uses an admin API key header. It is intentionally small and
server-side only; do not expose secrets through this route.
"""

from flask import Blueprint, jsonify, request

from backend.app.agents.openrouter_agent import get_default_prompt_sections
from backend.app.core.config import get_settings
from backend.app.core.runtime_config import (
    get_runtime_config,
    reset_runtime_config,
    seed_runtime_config_if_missing,
    update_runtime_config,
)

settings = get_settings()
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


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
        return jsonify({"detail": f"Failed to reset runtime config: {type(exc).__name__}"}), 500
    return jsonify(_public_config())
