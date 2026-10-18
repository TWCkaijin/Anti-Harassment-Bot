"""Independent admin Flask application; no public chat routes are mounted."""

import logging
import uuid

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from admin_app.auth import auth_bp, protect_admin_request
from admin_app.config import get_admin_settings
from admin_app.jobs import JobFailure, internal_bp, jobs_bp

logger = logging.getLogger(__name__)


def create_app(*, include_corpus=True):
    from admin_app.api import admin_bp
    from rag_core.firestore_clients import initialize_firebase

    initialize_firebase()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = get_admin_settings().admin_max_body_bytes
    app.before_request(protect_admin_request)
    app.register_blueprint(admin_bp, url_prefix="/api/v1/admin")
    app.register_blueprint(admin_bp, url_prefix="/v1/admin", name="admin_direct")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/admin/auth")
    # Explicit aliases use the same middleware and cookie/CSRF policy.
    app.register_blueprint(auth_bp, url_prefix="/v1/admin/auth", name="admin_auth_direct")
    app.register_blueprint(jobs_bp, url_prefix="/api/v1/admin")
    app.register_blueprint(jobs_bp, url_prefix="/v1/admin", name="admin_jobs_direct")
    app.register_blueprint(internal_bp, url_prefix="/internal")
    if include_corpus:
        from admin_app.corpus import corpus_bp, register_jobs

        register_jobs()
        app.register_blueprint(corpus_bp, url_prefix="/api/v1/admin/corpus")
        app.register_blueprint(corpus_bp, url_prefix="/v1/admin/corpus", name="admin_corpus_direct")

    @app.get("/api/v1/admin/health")
    @app.get("/v1/admin/health")
    def admin_health():
        return jsonify(
            {"status": "ok", "service": "admin", "environment": get_admin_settings().environment}
        )

    @app.after_request
    def secure_response(response):
        response.headers["Cache-Control"] = "no-store, private"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.errorhandler(JobFailure)
    def job_failure(error):
        return jsonify(error.error), error.error["http_status"]

    @app.errorhandler(HTTPException)
    def http_failure(error):
        return jsonify({"detail": error.description}), error.code

    @app.errorhandler(Exception)
    def unexpected_failure(error):
        error_id = uuid.uuid4().hex
        logger.exception(
            "Admin request failed error_id=%s method=%s path=%s",
            error_id,
            request.method,
            request.path,
        )
        return jsonify({"detail": "Admin service unavailable", "error_id": error_id}), 503

    if get_admin_settings().admin_local_jobs:
        from admin_app.local_dispatch import install

        install(app)
    return app


# Firebase imports this factory after all deployment source modules are present.
