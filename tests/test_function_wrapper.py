"""Tests for the Firebase HTTP-to-Flask wrapper."""

from uuid import UUID

from flask import Request

import main as function_main


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


def test_wrapper_500_returns_only_generic_detail_and_logged_error_id(monkeypatch, caplog):
    def exploding_app(_environ, _start_response):
        raise RuntimeError("sensitive wrapper detail")

    monkeypatch.setattr(function_main, "app", exploding_app)
    origin = function_main.settings.cors_origins[0]
    request = Request.from_values("/", headers={"Origin": origin})

    with caplog.at_level("ERROR"):
        response = function_main.handle_request(request)

    payload = response.get_json()
    assert response.status_code == 500
    assert payload["detail"] == "Internal Server Error"
    assert UUID(hex=payload["error_id"]).hex == payload["error_id"]
    assert payload["error_id"] in caplog.text
    assert "sensitive wrapper detail" not in response.get_data(as_text=True)
    assert response.headers["Access-Control-Allow-Origin"] == origin


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
