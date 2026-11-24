"""
性騷擾防治智能 AI — Flask 主入口
"""

import logging

from flask import Flask, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from client_app.api.chat import chat_bp
from client_app.api.health import health_bp
from rag_core.core.config import get_settings
from rag_core.core.logger import setup_logging
from rag_core.core.request_logging import register_error_response_logging
from rag_core.core.security import (
    build_cors_origin_allowlist,
    new_error_id,
    register_chat_request_security,
    register_request_size_limit,
)
from rag_core.firestore_clients import initialize_firebase

settings = get_settings()
setup_logging()


def create_app() -> Flask:
    """Build the public application without registering administrator routes."""
    initialize_firebase()

    # ── Flask 應用程式 ─────────────────────────────────────────────────────────
    app = Flask(__name__)
    register_error_response_logging(app)

    # ── CORS 設定 ────────────────────────────────────────────────────────────────
    CORS(
        app,
        resources={
            r"/*": {
                "origins": build_cors_origin_allowlist(
                    settings.cors_origins,
                    settings.cors_preview_origin_regexes,
                )
            }
        },
        supports_credentials=False,
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Firebase-AppCheck",
            "X-Requested-With",
        ],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        always_send=False,
        vary_header=True,
    )

    # ── 公開請求邊界 ────────────────────────────────────────────────────────
    register_request_size_limit(app, settings.api_max_content_length_bytes)
    register_chat_request_security(
        app,
        app_check_enabled=settings.chat_app_check_enabled,
        rate_limit_enabled=settings.chat_rate_limit_enabled,
        rate_limit_requests=settings.chat_rate_limit_requests,
        rate_limit_window_seconds=settings.chat_rate_limit_window_seconds,
    )

    # ── 掛載 Blueprints ─────────────────────────────────────────────────────────────
    # 支援 /api/v1 (本地與 Firebase Hosting Rewrite)
    app.register_blueprint(health_bp, url_prefix="/api/v1/health")
    app.register_blueprint(chat_bp, url_prefix="/api/v1/chat")
    # 支援 /v1 (直接呼叫 Cloud Function 且無 rewrite 時備用)
    app.register_blueprint(health_bp, url_prefix="/v1/health", name="health_v1")
    app.register_blueprint(chat_bp, url_prefix="/v1/chat", name="chat_v1")

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        """Keep all API-level HTTP failures machine-readable."""
        detail = (
            "Request body too large"
            if isinstance(error, RequestEntityTooLarge)
            else error.description
        )
        return jsonify({"detail": detail}), error.code

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        """Keep unexpected implementation details in server logs, never in API responses."""
        error_id = new_error_id()
        logging.getLogger(__name__).exception(
            "Unhandled Flask request exception error_id=%s",
            error_id,
            extra={
                "event": "unhandled_request_error",
                "error_id": error_id,
                "error_type": type(error).__name__,
            },
        )
        return jsonify({"detail": "Internal Server Error", "error_id": error_id}), 500

    @app.route("/")
    def root():
        return jsonify(
            {
                "service": settings.api_title,
                "version": settings.api_version,
            }
        )

    return app


app = create_app()
