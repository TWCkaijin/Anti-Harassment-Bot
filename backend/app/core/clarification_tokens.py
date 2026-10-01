"""Stateless signatures bind v3/v4 answers to the question actually issued.

The token authenticates the question's shape and allowed choices. Choices may
contain user text, so tokens must stay outside model prompts and logs. Answers
remain unverified user statements, never an authorization credential.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets

from backend.app.core.config import get_settings

_PROCESS_KEY = secrets.token_bytes(32)
_RESERVED = {"不確定", "我不確定", "暫不提供", "我暫不提供", "自行補充", "其他"}


def _key(contract_version: int = 3) -> bytes:
    settings = get_settings()
    configured = (
        os.environ.get("CLARIFICATION_SIGNING_KEY")
        or settings.admin_api_key
        or settings.openrouter_api_key
    )
    return (
        hashlib.sha256((f"clarification-v{contract_version}:" + configured).encode()).digest()
        if configured
        else hmac.new(_PROCESS_KEY, f"v{contract_version}".encode(), hashlib.sha256).digest()
    )


def sign_clarification(question: dict, *, contract_version: int = 3) -> str:
    if contract_version not in (3, 4):
        raise ValueError("Signed clarifications require contract version 3 or 4")
    if contract_version == 4 and "fact_key" in question:
        raise ValueError("V4 clarifications must not contain a fact key")
    payload = {key: question[key] for key in ("question_id", "selection_mode", "max_selections")}
    if contract_version == 3:
        payload["fact_key"] = question["fact_key"]
    else:
        payload["contract_version"] = 4
        payload["context_revision"] = question["context_revision"]
    payload["context_scope"] = question.get("context_scope", "personal")
    payload["allowed_values"] = [option["value"] for option in question["options"]]
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(_key(contract_version), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def validate_clarification_answer(
    answer: dict, *, require_token: bool = True, contract_version: int = 3
) -> dict | None:
    if contract_version not in (3, 4):
        raise ValueError("Signed clarifications require contract version 3 or 4")
    if contract_version == 4 and "fact_key" in answer:
        raise ValueError("V4 clarification answers must not contain a fact key")
    token = answer.get("validation_token")
    if not token:
        if require_token:
            raise ValueError("A clarification answer requires the issued question token")
        return
    if not isinstance(token, str):
        raise ValueError("Clarification token must be a string")
    try:
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(_key(contract_version), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        issued = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(issued, dict):
            raise ValueError
        if contract_version == 4 and (issued.get("contract_version") != 4 or "fact_key" in issued):
            raise ValueError
        if contract_version == 3 and issued.get("contract_version", 3) != 3:
            raise ValueError
        if contract_version == 4 and (
            isinstance(answer.get("context_revision"), bool)
            or not isinstance(answer.get("context_revision"), int)
        ):
            raise ValueError
        bound_keys = (
            ("question_id", "fact_key")
            if contract_version == 3
            else ("question_id", "context_revision")
        )
        if any(answer.get(key) != issued[key] for key in bound_keys):
            raise ValueError
        for key in ("selection_mode", "max_selections", "allowed_values", "context_scope"):
            if answer.get(key) is not None and answer[key] != issued[key]:
                raise ValueError
        if answer.get("status") not in {"provided", "unknown", "declined"}:
            raise ValueError
        value = answer.get("value")
        if answer.get("status") != "provided":
            if value is not None:
                raise ValueError
            return issued
        values = value if isinstance(value, list) else [value]
        if not 1 <= len(values) <= issued["max_selections"]:
            raise ValueError
        if issued["selection_mode"] == "single" and len(values) != 1:
            raise ValueError
        if any(
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 300
            or item.strip() in _RESERVED
            for item in values
        ):
            raise ValueError
        if len(set(values)) != len(values):
            raise ValueError
        # The one freeform entry is allowed to coexist with preset multiple choices.
        if sum(item not in issued["allowed_values"] for item in values) > 1:
            raise ValueError
        return issued
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("Clarification answer does not match the issued question") from exc
