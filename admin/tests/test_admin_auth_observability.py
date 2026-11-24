"""Authentication failures are traceable without logging protected contents."""

import logging
import re
from copy import deepcopy

import pytest
from google.api_core import exceptions

from admin_app import auth


@pytest.mark.parametrize("phase", ["authenticate_session", "consume_limits", "login"])
def test_auth_failure_logs_safe_phase_and_trace_without_running_protected_action(
    signed_in, services, monkeypatch, caplog, phase
):
    client, headers = signed_in
    database = services[0]
    credential = deepcopy(database.documents["admin_credentials/shared"])
    session_paths = {key for key in database.documents if key.startswith("admin_sessions/")}
    session_path = next(iter(session_paths))
    secret_parts = (
        "private-password-marker",
        "synthetic-personal-request-content",
        client.get_cookie("__session").value,
        headers["X-CSRF-Token"],
    )
    error = exceptions.Aborted("DO NOT LOG " + " ".join(secret_parts))
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise error

    trace_id = "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-rag-harass-bot")
    request_headers = {**headers, "X-Cloud-Trace-Context": f"{trace_id}/99;o=1"}
    with monkeypatch.context() as patch:
        patch.setattr(auth, "password_hasher" if phase == "login" else phase, fail)
        with caplog.at_level(logging.WARNING, logger=auth.logger.name):
            response = client.post(
                "/api/v1/admin/auth/login" if phase == "login" else "/api/v1/admin/auth/logout",
                headers=request_headers,
                json={"password": secret_parts[0], "content": secret_parts[1]},
            )

    assert response.status_code == 503
    assert response.json["detail"] == "Authentication service unavailable"
    assert re.fullmatch(r"[0-9a-f]{32}", response.json["error_id"])
    assert response.headers["Cache-Control"] == "no-store, private"
    assert attempts == [1], "Observability must not introduce automatic retries"
    records = [record for record in caplog.records if record.name == auth.logger.name]
    assert len(records) == 1
    record = records[0]
    assert record.phase == phase
    assert record.error_type == "Aborted"
    assert record.google_status == "ABORTED"
    assert record.error_id == response.json["error_id"]
    assert not record.exc_info and record.stack_info is None
    expected_trace = f"projects/demo-rag-harass-bot/traces/{trace_id}"
    assert record.__dict__["logging.googleapis.com/trace"] == expected_trace
    assert record.__dict__["logging.googleapis.com/spanId"] == "0000000000000063"
    assert record.__dict__["logging.googleapis.com/trace_sampled"] is True
    assert expected_trace in record.getMessage()
    assert response.json["error_id"] in record.getMessage()
    rendered = caplog.text + response.get_data(as_text=True)
    assert all(secret not in rendered for secret in secret_parts)
    assert "Traceback" not in rendered

    # A failed logout cannot revoke the session; a failed login cannot create one.
    # Keep the original successful authentication's last_seen refresh semantics.
    assert database.documents["admin_credentials/shared"] == credential
    assert {key for key in database.documents if key.startswith("admin_sessions/")} == session_paths
    assert database.documents[session_path]["revoked"] is False
    assert not any(key.startswith("admin_jobs/") for key in database.documents)
    assert client.get("/api/v1/admin/auth/session").status_code == 200


def test_unclassified_error_and_invalid_trace_never_render_exception_or_header(
    signed_in, monkeypatch, caplog
):
    class UnprintableError(RuntimeError):
        code = "not-a-safe-status-secret"

        def __str__(self):
            raise AssertionError("Must not stringify an authentication exception")

        def __repr__(self):
            raise AssertionError("Must not repr an authentication exception")

    client, headers = signed_in

    def fail():
        raise UnprintableError("exception-content-secret")

    monkeypatch.setattr(auth, "authenticate_session", fail)
    with caplog.at_level(logging.WARNING, logger=auth.logger.name):
        response = client.post(
            "/api/v1/admin/auth/logout",
            headers={**headers, "X-Cloud-Trace-Context": "invalid-trace-private-content"},
        )
    assert response.status_code == 503
    record = next(record for record in caplog.records if record.name == auth.logger.name)
    assert record.error_type == "UnprintableError"
    assert record.google_status is None
    assert record.error_id == response.json["error_id"]
    assert "logging.googleapis.com/trace" not in record.__dict__
    assert "trace=-" in record.getMessage()
    for secret in (
        "not-a-safe-status-secret",
        "exception-content-secret",
        "invalid-trace-private-content",
    ):
        assert secret not in caplog.text + response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (exceptions.DeadlineExceeded("private details"), "DEADLINE_EXCEEDED"),
        (exceptions.PermissionDenied("private details"), "PERMISSION_DENIED"),
        (exceptions.TooManyRequests("private details"), "HTTP_429"),
        (RuntimeError("private details"), None),
    ],
)
def test_google_status_accepts_only_known_sdk_status_constants(error, status):
    assert auth._safe_google_status(error) == status


def test_transaction_wrapper_retains_only_safe_google_cause_status():
    wrapped = ValueError("transaction message with sensitive contents")
    wrapped.__cause__ = exceptions.Aborted("private Firestore response")
    assert auth._safe_google_status(wrapped) == "ABORTED"
    cyclic = RuntimeError("private details")
    cyclic.__cause__ = cyclic
    assert auth._safe_google_status(cyclic) is None
