"""Handled responses must remain diagnosable when INFO logs are filtered out."""

import json

from flask import Flask, jsonify

from backend.app.core.logger import GCPJsonFormatter
from backend.app.core.request_logging import register_error_response_logging


def test_error_summary_excludes_private_payloads_and_success(caplog):
    app = Flask(__name__)
    register_error_response_logging(app)

    @app.get("/failure")
    def failure():
        return jsonify(
            {
                "detail": "upstream unavailable",
                "code": "upstream_error",
                "retryable": True,
                "error_id": "incident-123",
                "debug_message": "private-model-response",
                "errors": [{"input": "private-input"}],
            }
        ), 502

    @app.get("/ok")
    def ok():
        return jsonify({"reply": "private-reply"})

    caplog.set_level("WARNING")
    client = app.test_client()
    assert client.get("/ok").status_code == 200
    assert caplog.records == []
    assert (
        client.get(
            "/failure?token=private-query", headers={"Authorization": "Bearer private-token"}
        ).status_code
        == 502
    )
    record = caplog.records[-1]
    assert record.levelname == "ERROR"
    data = json.loads(GCPJsonFormatter().format(record))
    assert data["error_id"] == "incident-123"
    assert data["error_detail"] == "upstream unavailable"
    assert data["request_path"] == "/failure"
    assert data["retryable"] is True
    assert "private-" not in json.dumps(data)


def test_early_client_error_is_warning_and_non_json_error_still_logs(caplog):
    app = Flask(__name__)
    register_error_response_logging(app)

    @app.before_request
    def reject_request():
        return "Too many requests", 429

    caplog.set_level("WARNING")
    response = app.test_client().get("/anything")
    assert response.status_code == 429
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "WARNING"
    assert caplog.records[0].http_status == 429
    assert caplog.records[0].error_detail == "429 TOO MANY REQUESTS"
