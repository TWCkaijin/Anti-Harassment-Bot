import json
import logging
import os
from contextvars import copy_context

from firebase_functions import https_fn, options
from flask import Response as FlaskResponse

from client_app.main import app
from rag_core.core.config import get_settings
from rag_core.core.logger import get_request_log_context
from rag_core.core.security import cors_headers_for_origin, new_error_id

logger = logging.getLogger(__name__)
settings = get_settings()
PROJECT = os.getenv("FIREBASE_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "anti-harassment-bot"))


class _ContextBoundIterable:
    """Keep the nested Flask stream out of Functions Framework's context stack."""

    def __init__(self, iterable, context):
        self._iterable = iterable
        self._context = context
        self._iterator = context.run(iter, iterable)

    def __iter__(self):
        return self

    def __next__(self):
        return self._context.run(next, self._iterator)

    def close(self):
        close = getattr(self._iterable, "close", None)
        if close is not None:
            self._context.run(close)


def handle_request(req: https_fn.Request) -> https_fn.Response:
    try:
        # Flask-CORS owns both preflight and normal response headers.
        # Keep the WSGI iterable lazy so SSE reaches the client as it is yielded.
        # from_app also preserves close(), including client disconnect cleanup.
        # Werkzeug advances the iterable once here. stream_with_context can
        # leave the inner Flask context pushed until the stream is closed.
        # Isolate both that first advance and subsequent iteration/cleanup so
        # Functions Framework can pop its own outer request context safely.
        context = copy_context()
        response = context.run(FlaskResponse.from_app, app, req.environ, buffered=False)
        response.response = _ContextBoundIterable(response.response, context)
        return response
    except Exception:
        error_id = new_error_id()
        logger.exception(
            "Unhandled Firebase HTTP wrapper exception error_id=%s",
            error_id,
            extra={
                **get_request_log_context(req),
                "event": "firebase_wrapper_error",
                "error_id": error_id,
                "http_status": 500,
            },
        )
        return FlaskResponse(
            response=json.dumps(
                {"detail": "Internal Server Error", "error_id": error_id},
                ensure_ascii=False,
            ),
            status=500,
            mimetype="application/json",
            headers=cors_headers_for_origin(
                req.headers.get("Origin"),
                settings.cors_origins,
                settings.cors_preview_origin_regexes,
            ),
        )


# 註冊為 Firebase HTTP 函數，支援公開呼叫 (invoker="public")
# 增加記憶體至 512MB 以獲得更多 CPU 資源，並將超時時間延長至 180 秒
@https_fn.on_request(
    region="asia-east1",
    invoker="public",
    timeout_sec=180,
    memory=options.MemoryOption.MB_512,
    service_account=os.getenv(
        "CLIENT_PROD_SERVICE_ACCOUNT", f"rag-client-prod@{PROJECT}.iam.gserviceaccount.com"
    ),
    secrets=["RAG_OPENROUTER_PROD", "RAG_CLARIFICATION_PROD", "RAG_CLARIFICATION_LEGACY_PROD"],
)
def api(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)


@https_fn.on_request(
    region="asia-east1",
    invoker="public",
    timeout_sec=180,
    memory=options.MemoryOption.MB_512,
    service_account=os.getenv(
        "CLIENT_PREVIEW_SERVICE_ACCOUNT", f"rag-client-dev@{PROJECT}.iam.gserviceaccount.com"
    ),
    secrets=["RAG_OPENROUTER_DEV", "RAG_CLARIFICATION_DEV", "RAG_CLARIFICATION_LEGACY_DEV"],
)
def api_preview(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)
