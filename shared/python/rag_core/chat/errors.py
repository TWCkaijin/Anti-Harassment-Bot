"""Map agent, upstream and validation failures to safe HTTP error responses."""

import uuid

from flask import g, jsonify
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import ValidationError

from rag_core.agents.openrouter_agent import AgentContractError
from rag_core.core.agent_errors import ModelOutputLimitError
from rag_core.core.gated_chat import GateUnavailableError
from rag_core.core.logger import get_logger
from rag_core.rag.base import RAGUnavailableError

logger = get_logger(__name__)


_TRANSIENT_UPSTREAM_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    ConnectionError,
    TimeoutError,
)


_PERMANENT_UPSTREAM_ERRORS = (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)


_RETRYABLE_AGENT_ERRORS = (AgentContractError, *_TRANSIENT_UPSTREAM_ERRORS)


def image_upload_disabled_error():
    return (
        jsonify(
            {
                "code": "image_upload_disabled",
                "detail": "Image upload is disabled",
                "retryable": False,
            }
        ),
        422,
    )


def service_error(
    runtime_config,
    exc: Exception,
    *,
    code: str,
    detail: str,
    retryable: bool,
    status_code: int,
    error_id: str | None = None,
):
    """Log a category only: chat requests carry private case text, even in development."""
    error_message = type(exc).__name__
    logger.error(
        "Chat request failed [%s]: %s: %s",
        code,
        type(exc).__name__,
        error_message,
        extra={
            "event": "chat_request_failed",
            "error_code": code,
            "error_type": type(exc).__name__,
            "error_message": error_message,
            "http_status": status_code,
            "retryable": retryable,
            **(exc.safe_diagnostics if isinstance(exc, AgentContractError) else {}),
            **({"error_id": error_id} if error_id else {}),
        },
    )
    payload = {"code": code, "detail": detail, "retryable": retryable}
    if error_id:
        payload["error_id"] = error_id
    return jsonify(payload), status_code


def retryable_error(runtime_config, exc: Exception):
    return service_error(
        runtime_config,
        exc,
        code="upstream_invalid_response",
        detail="伺服器回傳錯誤，正在重試中",
        retryable=True,
        status_code=502,
    )


def request_validation_error(exc: ValidationError):
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return (
        jsonify(
            {
                "code": "invalid_request",
                "detail": "Invalid request payload",
                "errors": errors,
                "retryable": False,
            }
        ),
        422,
    )


def agent_error(runtime_config, exc: Exception):
    """Use the same diagnostic codes for JSON and streaming requests."""
    if isinstance(exc, ValidationError):
        return retryable_error(runtime_config, exc)
    if isinstance(exc, GateUnavailableError):
        return service_error(
            runtime_config,
            exc,
            code="review_unavailable",
            detail="內容審查暫時無法完成，請重試",
            retryable=True,
            status_code=503,
        )
    if isinstance(exc, ModelOutputLimitError):
        return service_error(
            runtime_config,
            exc,
            code="model_output_limit",
            detail="模型輸出達到長度上限，請提高輸出預算後再測試"
            if getattr(g, "admin_diagnostic", False)
            else "模型回覆因輸出長度限制而未完成，請縮小問題範圍後再試",
            retryable=False,
            status_code=502,
        )
    if isinstance(exc, RAGUnavailableError):
        return service_error(
            runtime_config,
            exc,
            code="rag_unavailable",
            detail="檢索服務暫時無法使用，請稍後再試",
            retryable=True,
            status_code=503,
        )
    if isinstance(exc, _RETRYABLE_AGENT_ERRORS):
        return retryable_error(runtime_config, exc)
    if isinstance(exc, _PERMANENT_UPSTREAM_ERRORS):
        return service_error(
            runtime_config,
            exc,
            code="upstream_request_rejected",
            detail="AI 服務目前無法處理此請求",
            retryable=False,
            status_code=502,
        )
    return service_error(
        runtime_config,
        exc,
        code="internal_error",
        detail="伺服器無法完成請求",
        retryable=False,
        status_code=500,
        error_id=uuid.uuid4().hex,
    )
