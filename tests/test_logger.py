"""Cloud logging format and request correlation regressions."""

import io
import json
import logging
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from flask import Flask, Request

from backend.app.core.logger import (
    ColoredFormatter,
    GCPJsonFormatter,
    get_request_log_context,
    setup_logging,
)

TRACE_ID = "105445aa7843bc8bf206b12000100000"
MANAGED_LOGGERS = ("", "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi")


@pytest.fixture(autouse=True)
def isolated_logging_environment(monkeypatch):
    for name in (
        "ENVIRONMENT",
        "K_SERVICE",
        "FUNCTION_TARGET",
        "FUNCTION_NAME",
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
        "GCP_PROJECT",
        "FIREBASE_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)
    original = {
        name: (logger.handlers[:], logger.level, logger.propagate)
        for name in MANAGED_LOGGERS
        for logger in [logging.getLogger(name)]
    }
    yield
    for name, (handlers, level, propagate) in original.items():
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def make_record(exc_info=None, **extra):
    record = logging.LogRecord(
        "backend.test",
        logging.ERROR,
        __file__,
        42,
        "Request failed: %s",
        ("upstream",),
        exc_info,
        "example_handler",
    )
    record.__dict__.update(extra)
    return record


@pytest.mark.parametrize("cloud_variable", ["K_SERVICE", "FUNCTION_TARGET", "FUNCTION_NAME"])
def test_cloud_development_emits_json_error_and_suppresses_debug(monkeypatch, cloud_variable):
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv(cloud_variable, "api-preview")

    setup_logging()
    logging.getLogger("backend.test").debug("private SDK payload must not be emitted")
    logging.getLogger("backend.test").error("preview failed", extra={"error_id": "error-123"})

    payload = json.loads(output.getvalue())
    assert payload["severity"] == "ERROR"
    assert payload["message"] == "preview failed"
    assert payload["logger"] == "backend.test"
    assert payload["error_id"] == "error-123"
    assert "private SDK payload" not in output.getvalue()
    assert "\033[" not in output.getvalue()
    assert logging.getLogger().level == logging.INFO
    assert isinstance(logging.getLogger().handlers[0].formatter, GCPJsonFormatter)


def test_local_development_keeps_colored_debug_logging(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setenv("ENVIRONMENT", "development")

    setup_logging()
    logging.getLogger("backend.test").error("local failure")

    assert "\033[31mERROR\033[0m" in output.getvalue()
    assert "local failure" in output.getvalue()
    assert logging.getLogger().level == logging.DEBUG
    assert isinstance(logging.getLogger().handlers[0].formatter, ColoredFormatter)


def test_setup_reads_environment_at_call_time_and_retains_single_root_handler(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    setup_logging()
    assert isinstance(logging.getLogger().handlers[0].formatter, ColoredFormatter)

    monkeypatch.setenv("ENVIRONMENT", "production")
    setup_logging()
    assert len(logging.getLogger().handlers) == 1
    assert isinstance(logging.getLogger().handlers[0].formatter, GCPJsonFormatter)
    assert logging.getLogger().level == logging.INFO
    for name in MANAGED_LOGGERS[1:]:
        assert logging.getLogger(name).handlers == []
        assert logging.getLogger(name).propagate


def test_explicit_request_context_contains_only_routing_and_validated_trace(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    req = Request.from_values(
        "/api/v1/chat/?secret=query-secret",
        method="POST",
        headers={
            "X-Cloud-Trace-Context": f"{TRACE_ID}/123;o=1",
            "Authorization": "Bearer header-secret",
            "Cookie": "session=cookie-secret",
        },
        data="private body contents",
    )

    assert get_request_log_context(req) == {
        "request_method": "POST",
        "request_path": "/api/v1/chat/",
        "logging.googleapis.com/trace": f"projects/test-project/traces/{TRACE_ID}",
        "logging.googleapis.com/spanId": "000000000000007b",
        "logging.googleapis.com/trace_sampled": True,
    }


def test_formatter_automatically_merges_flask_context_without_query_headers_or_body(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    app = Flask(__name__)
    with app.test_request_context(
        "/api/v1/chat/?token=query-secret",
        method="POST",
        headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/15;o=0", "Authorization": "header-secret"},
        data="body-secret",
    ):
        output = GCPJsonFormatter().format(make_record(error_id="error-123"))

    payload = json.loads(output)
    assert payload["request_method"] == "POST"
    assert payload["request_path"] == "/api/v1/chat/"
    assert payload["logging.googleapis.com/spanId"] == "000000000000000f"
    assert payload["logging.googleapis.com/trace_sampled"] is False
    assert payload["error_id"] == "error-123"
    for secret in ("query-secret", "header-secret", "body-secret"):
        assert secret not in output
    assert get_request_log_context() == {}


@pytest.mark.parametrize(
    "header",
    [
        "",
        "not-a-trace",
        f"{TRACE_ID}/abc;o=1",
        f"{TRACE_ID}/-1;o=1",
        f"{TRACE_ID}/18446744073709551616;o=1",
        f"{TRACE_ID}/1;o=2",
        f"{TRACE_ID}/1;o=1;secret=anything",
        f"{TRACE_ID}/1;o=1\n",
        f"{TRACE_ID}/0;o=1",
        f"{'0' * 32}/1;o=1",
        f"{TRACE_ID}/1;o=1,other-trace",
    ],
)
def test_invalid_trace_headers_are_ignored(monkeypatch, header):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    req = SimpleNamespace(method="GET", path="/health", headers={"X-Cloud-Trace-Context": header})
    assert get_request_log_context(req) == {"request_method": "GET", "request_path": "/health"}


def test_trace_project_environment_precedence_and_firebase_json_fallback(monkeypatch):
    req = Request.from_values("/", headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/1;o=1"})
    monkeypatch.setenv("FIREBASE_CONFIG", json.dumps({"projectId": "firebase-project"}))
    monkeypatch.setenv("GCP_PROJECT", "gcp-project")
    monkeypatch.setenv("GCLOUD_PROJECT", "gcloud-project")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "google-project")
    for variable, project in (
        ("GOOGLE_CLOUD_PROJECT", "google-project"),
        ("GCLOUD_PROJECT", "gcloud-project"),
        ("GCP_PROJECT", "gcp-project"),
        ("FIREBASE_CONFIG", "firebase-project"),
    ):
        assert get_request_log_context(req)["logging.googleapis.com/trace"] == (
            f"projects/{project}/traces/{TRACE_ID}"
        )
        monkeypatch.delenv(variable)
    assert get_request_log_context(req) == {"request_method": "GET", "request_path": "/"}


@pytest.mark.parametrize("firebase_config", ["/private/credentials.json", "not-json", "[]", "null"])
def test_firebase_config_fallback_never_reads_files(monkeypatch, firebase_config):
    monkeypatch.setenv("FIREBASE_CONFIG", firebase_config)
    req = Request.from_values("/", headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/1;o=1"})

    def forbidden_open(*_args, **_kwargs):
        raise AssertionError("Logger must not read credential/config files")

    with monkeypatch.context() as scoped:
        scoped.setattr("builtins.open", forbidden_open)
        context = get_request_log_context(req)
    assert context == {"request_method": "GET", "request_path": "/"}


def test_json_formatter_preserves_traceback_logger_location_and_extras():
    try:
        raise RuntimeError("upstream unavailable")
    except RuntimeError:
        payload = json.loads(GCPJsonFormatter().format(make_record(sys.exc_info(), error_id="abc")))

    assert payload["severity"] == "ERROR"
    assert payload["message"] == "Request failed: upstream"
    assert payload["logger"] == "backend.test"
    assert payload["error_id"] == "abc"
    assert payload["logging.googleapis.com/sourceLocation"] == {
        "file": __file__,
        "line": "42",
        "function": "example_handler",
    }
    assert "Traceback (most recent call last)" in payload["exception"]
    assert (
        "test_json_formatter_preserves_traceback_logger_location_and_extras" in payload["exception"]
    )
    assert "RuntimeError: upstream unavailable" in payload["exception"]


def test_non_json_extras_do_not_drop_error_records_or_override_severity():
    class Unprintable:
        def __str__(self):
            raise ValueError("cannot print this object")

    circular = []
    circular.append(circular)
    payload = json.loads(
        GCPJsonFormatter().format(
            make_record(
                severity="INFO",
                details={"status": 503, "nested": [True, None]},
                when=datetime(2026, 9, 22, tzinfo=UTC),
                object_value=Unprintable(),
                recursive=circular,
                invalid_keys={("tuple",): "value"},
            )
        )
    )

    assert payload["severity"] == "ERROR"
    assert payload["details"] == {"status": 503, "nested": [True, None]}
    assert payload["when"] == "2026-09-22 00:00:00+00:00"
    assert payload["object_value"] == "<Unprintable: unprintable>"
    assert payload["recursive"] == "[[...]]"
    assert "tuple" in payload["invalid_keys"]
