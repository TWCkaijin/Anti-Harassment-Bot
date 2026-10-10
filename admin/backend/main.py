"""Only two admin HTTP exports; Cloud Tasks uses internal routes on each."""

import logging
import os
import uuid
from functools import lru_cache

from firebase_functions import https_fn, options
from flask import Response

logger = logging.getLogger(__name__)
PROJECT = os.getenv("FIREBASE_PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", "anti-harassment-bot"))


@lru_cache
def get_app():
    from admin_app.main import create_app

    return create_app()


def handle_request(req):
    try:
        return Response.from_app(get_app(), req.environ, buffered=True)
    except Exception:
        error_id = uuid.uuid4().hex
        logger.exception("Admin wrapper failed error_id=%s", error_id)
        return Response(
            '{"detail":"Admin service unavailable","error_id":"' + error_id + '"}',
            status=503,
            mimetype="application/json",
            headers={"Cache-Control": "no-store"},
        )


@https_fn.on_request(
    region="asia-east1",
    invoker="public",
    timeout_sec=900,
    memory=options.MemoryOption.GB_1,
    concurrency=8,
    max_instances=5,
    service_account=os.getenv(
        "ADMIN_PROD_SERVICE_ACCOUNT", f"rag-admin-prod@{PROJECT}.iam.gserviceaccount.com"
    ),
    secrets=["RAG_OPENROUTER_PROD", "RAG_CLARIFICATION_PROD", "RAG_CLARIFICATION_LEGACY_PROD"],
)
def admin_api(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)


@https_fn.on_request(
    region="asia-east1",
    invoker="public",
    timeout_sec=900,
    memory=options.MemoryOption.GB_1,
    concurrency=8,
    max_instances=5,
    service_account=os.getenv(
        "ADMIN_PREVIEW_SERVICE_ACCOUNT", f"rag-admin-dev@{PROJECT}.iam.gserviceaccount.com"
    ),
    secrets=["RAG_OPENROUTER_DEV", "RAG_CLARIFICATION_DEV", "RAG_CLARIFICATION_LEGACY_DEV"],
)
def admin_api_preview(req: https_fn.Request) -> https_fn.Response:
    return handle_request(req)
