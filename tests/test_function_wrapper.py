"""Tests for the Firebase HTTP-to-Flask wrapper."""

import json
from uuid import UUID

import pytest
from flask import Flask, Request, Response, current_app, request, stream_with_context

import main as function_main
from backend.app.core.logger import GCPJsonFormatter


@pytest.mark.parametrize("disconnect_early", [False, True])
def test_wrapper_stream_context_isolated_from_functions_framework(monkeypatch, disconnect_early):
    outer = Flask("functions-framework")
    origin = "https://anti-harassment-bot--dev-preview-w28ypvc3.web.app"
    inner = Flask("backend-stream")
    closed = []

    @inner.route("/stream")
    def stream():
        @stream_with_context
        def chunks():
            try:
                yield ": connected\n\n"
                assert current_app._get_current_object() is inner
                assert request.path == "/stream"
                yield "event: done\ndata: {}\n\n"
            finally:
                closed.append(current_app.name)

        return Response(chunks(), headers={"Access-Control-Allow-Origin": origin})

    monkeypatch.setattr(function_main, "app", inner)

    @outer.route("/stream")
    def invoke():
        response = function_main.handle_request(request)
        assert current_app._get_current_object() is outer
        return response

    response = outer.test_client().get("/stream", buffered=False)
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    if not disconnect_early:
        assert b"event: done" in response.get_data()
    response.close()
    assert closed == ["backend-stream"]


def test_wrapper_delegates_preflight_to_allowlisted_flask_cors():
    origin = function_main.settings.cors_origins[0]
    request = Request.from_values(
        "/api/v1/chat/",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type, X-Firebase-AppCheck",
        },
    )

    response = function_main.handle_request(request)

    assert response.status_code in {200, 204}
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert "Access-Control-Allow-Credentials" not in response.headers
    assert "X-Firebase-AppCheck" in response.headers["Access-Control-Allow-Headers"]


def test_wrapper_does_not_reflect_disallowed_origin():
    request = Request.from_values(
        "/api/v1/health/",
        headers={"Origin": "https://evil.example"},
    )

    response = function_main.handle_request(request)

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


def test_wrapper_keeps_json_response_and_headers(monkeypatch):
    def json_app(_environ, start_response):
        start_response(
            "201 Created",
            [("Content-Type", "application/json"), ("X-Request-Id", "json-request")],
        )
        return [b'{"response":"ok"}']

    monkeypatch.setattr(function_main, "app", json_app)

    response = function_main.handle_request(Request.from_values("/v1/chat/"))

    assert response.status_code == 201
    assert response.get_json() == {"response": "ok"}
    assert response.headers["X-Request-Id"] == "json-request"
    response.close()


def test_wrapper_does_not_wait_for_later_stream_chunks(monkeypatch):
    state = {"next_chunk_ready": False, "closed": False}

    def stream_app(_environ, start_response):
        start_response(
            "200 OK",
            [
                ("Content-Type", "text/event-stream; charset=utf-8"),
                ("Cache-Control", "no-cache, no-transform"),
                ("Access-Control-Allow-Origin", "https://example.com"),
            ],
        )

        def chunks():
            try:
                yield b'event: delta\ndata: {"text":"first"}\n\n'
                # Simulate a producer whose next chunk is not available yet.
                assert state["next_chunk_ready"], "wrapper consumed the stream eagerly"
                yield b'event: done\ndata: {"response":"first"}\n\n'
            finally:
                state["closed"] = True

        return chunks()

    monkeypatch.setattr(function_main, "app", stream_app)

    response = function_main.handle_request(Request.from_values("/v1/chat/"))

    assert response.status_code == 200
    assert response.is_streamed
    assert response.mimetype == "text/event-stream"
    assert response.headers["Cache-Control"] == "no-cache, no-transform"
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.com"
    assert "Content-Length" not in response.headers
    assert not state["closed"]
    chunks = iter(response.response)
    assert next(chunks) == b'event: delta\ndata: {"text":"first"}\n\n'
    state["next_chunk_ready"] = True
    assert next(chunks) == b'event: done\ndata: {"response":"first"}\n\n'
    response.close()
    assert state["closed"]


def test_wrapper_closes_upstream_when_client_stops_reading(monkeypatch):
    events = []

    def stream_app(_environ, start_response):
        start_response("200 OK", [("Content-Type", "text/event-stream")])

        def chunks():
            try:
                yield b": connected\n\n"
                events.append("read later chunk")
                yield b"event: done\ndata: {}\n\n"
            finally:
                events.append("closed")

        return chunks()

    monkeypatch.setattr(function_main, "app", stream_app)
    response = function_main.handle_request(Request.from_values("/v1/chat/"))

    response.close()

    assert events == ["closed"]


def test_wrapper_500_returns_only_generic_detail_and_logged_error_id(monkeypatch, caplog):
    def exploding_app(_environ, _start_response):
        raise RuntimeError("sensitive wrapper detail")

    monkeypatch.setattr(function_main, "app", exploding_app)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    origin = function_main.settings.cors_origins[0]
    trace_id = "0123456789abcdef0123456789abcdef"
    request = Request.from_values(
        "/?token=not-for-logs",
        headers={
            "Origin": origin,
            "X-Cloud-Trace-Context": f"{trace_id}/74;o=1",
            "Authorization": "Bearer not-for-logs",
        },
    )

    with caplog.at_level("ERROR"):
        response = function_main.handle_request(request)

    payload = response.get_json()
    assert response.status_code == 500
    assert payload["detail"] == "Internal Server Error"
    assert UUID(hex=payload["error_id"]).hex == payload["error_id"]
    assert payload["error_id"] in caplog.text
    assert "sensitive wrapper detail" not in response.get_data(as_text=True)
    assert response.headers["Access-Control-Allow-Origin"] == origin
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "firebase_wrapper_error"
    )
    entry = json.loads(GCPJsonFormatter().format(record))
    assert entry["logging.googleapis.com/trace"] == f"projects/test-project/traces/{trace_id}"
    assert entry["logging.googleapis.com/spanId"] == "000000000000004a"
    assert entry["error_id"] == payload["error_id"]
    assert entry["request_path"] == "/"
    assert "sensitive wrapper detail" in entry["exception"]
    assert "not-for-logs" not in json.dumps(entry)


def test_wrapper_500_allows_strict_preview_origin_but_not_cross_project(monkeypatch):
    def exploding_app(_environ, _start_response):
        raise RuntimeError("failure")

    monkeypatch.setattr(function_main, "app", exploding_app)
    allowed_origin = "https://anti-harassment-bot--dev-preview-a1b2-c3.web.app"
    denied_origin = "https://other-project--dev-preview-a1b2-c3.web.app"

    allowed = function_main.handle_request(
        Request.from_values("/", headers={"Origin": allowed_origin})
    )
    denied = function_main.handle_request(
        Request.from_values("/", headers={"Origin": denied_origin})
    )

    assert allowed.status_code == 500
    assert allowed.headers["Access-Control-Allow-Origin"] == allowed_origin
    assert "Access-Control-Allow-Credentials" not in allowed.headers
    assert denied.status_code == 500
    assert "Access-Control-Allow-Origin" not in denied.headers


def test_wrapper_error_does_not_add_cors_for_disallowed_origin(monkeypatch):
    def exploding_app(_environ, _start_response):
        raise RuntimeError("failure")

    monkeypatch.setattr(function_main, "app", exploding_app)
    request = Request.from_values("/", headers={"Origin": "https://evil.example"})

    response = function_main.handle_request(request)

    assert response.status_code == 500
    assert "Access-Control-Allow-Origin" not in response.headers
