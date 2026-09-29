"""Tests for public Flask request-boundary protections."""

from io import BytesIO
from uuid import UUID

import pytest
from flask import Flask, jsonify
from pydantic import ValidationError
from werkzeug.test import EnvironBuilder

from backend.app.core.config import (
    DEFAULT_CORS_ORIGINS,
    DEFAULT_CORS_PREVIEW_ORIGIN_REGEXES,
    Settings,
)
from backend.app.core.security import (
    APP_CHECK_HEADER,
    ProcessLocalSlidingWindowRateLimiter,
    register_chat_request_security,
    register_request_size_limit,
)
from backend.app.main import app


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_protected_app(
    *,
    app_check_enabled: bool = False,
    rate_limit_enabled: bool = True,
    request_limit: int = 2,
    window_seconds: int = 60,
    verifier=lambda _token: {"sub": "test-app"},
    clock=None,
) -> Flask:
    test_app = Flask(__name__)
    register_chat_request_security(
        test_app,
        app_check_enabled=app_check_enabled,
        rate_limit_enabled=rate_limit_enabled,
        rate_limit_requests=request_limit,
        rate_limit_window_seconds=window_seconds,
        verify_app_check_token=verifier,
        clock=clock or FakeClock(),
    )

    @test_app.post("/api/v1/chat/")
    def chat():
        return jsonify({"status": "chat-ok"})

    @test_app.post("/v1/chat/")
    def chat_v1():
        return jsonify({"status": "chat-ok"})

    @test_app.get("/api/v1/health/")
    def health():
        return jsonify({"status": "ok"})

    @test_app.get("/api/v1/admin/config")
    def admin():
        return jsonify({"status": "admin-ok"})

    return test_app


def test_settings_replace_legacy_wildcard_with_fixed_cors_allowlist():
    configured = Settings(
        openrouter_api_key="test",
        cors_origins=["*"],
        _env_file=None,
    )

    assert configured.cors_origins == list(DEFAULT_CORS_ORIGINS)
    assert "*" not in configured.cors_origins
    assert Settings(openrouter_api_key="test", cors_origins=[], _env_file=None).cors_origins == []


def test_settings_normalize_and_validate_exact_cors_origins():
    configured = Settings(
        openrouter_api_key="test",
        cors_origins=["https://example.test/", "https://example.test"],
        _env_file=None,
    )

    assert configured.cors_origins == ["https://example.test"]
    with pytest.raises(ValidationError, match="exact HTTP\\(S\\) origin"):
        Settings(
            openrouter_api_key="test",
            cors_origins=["https://example.test/path"],
            _env_file=None,
        )
    with pytest.raises(ValidationError, match="exact HTTP\\(S\\) origin"):
        Settings(
            openrouter_api_key="test",
            cors_origins=["https://example.test$"],
            _env_file=None,
        )


def test_settings_accept_only_strictly_anchored_firebase_preview_origin_regexes():
    configured = Settings(
        openrouter_api_key="test",
        cors_preview_origin_regexes=list(DEFAULT_CORS_PREVIEW_ORIGIN_REGEXES),
        _env_file=None,
    )

    assert configured.cors_preview_origin_regexes == list(DEFAULT_CORS_PREVIEW_ORIGIN_REGEXES)
    unsafe_patterns = [
        r"https://anti-harassment-bot--dev-preview-[a-z0-9-]+\.web\.app",
        r"\Ahttps://.*\Z",
        r"^https://anti-harassment-bot--dev-preview-.*\.web\.app$",
        r"\Ahttps://(?:anti-harassment-bot|evil)--dev-preview-[a-z0-9-]+\.web\.app\Z",
    ]
    for unsafe_pattern in unsafe_patterns:
        with pytest.raises(ValidationError, match="strictly anchored Firebase Hosting"):
            Settings(
                openrouter_api_key="test",
                cors_preview_origin_regexes=[unsafe_pattern],
                _env_file=None,
            )


def test_app_check_protects_chat_but_not_health_or_admin():
    verified_tokens: list[str] = []

    def verifier(token: str):
        verified_tokens.append(token)
        if token != "valid-token":
            raise ValueError("invalid token")
        return {"sub": "web-app"}

    test_app = make_protected_app(
        app_check_enabled=True,
        rate_limit_enabled=False,
        verifier=verifier,
    )
    client = test_app.test_client()

    assert client.get("/api/v1/health/").status_code == 200
    assert client.get("/api/v1/admin/config").status_code == 200
    assert client.post("/api/v1/chat/").status_code == 401
    assert (
        client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "invalid-token"}).status_code == 401
    )
    assert (
        client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "valid-token"}).status_code == 200
    )
    assert client.post("/v1/chat/").status_code == 401
    assert client.post("/v1/chat/", headers={APP_CHECK_HEADER: "valid-token"}).status_code == 200
    assert verified_tokens == ["invalid-token", "valid-token", "valid-token"]


def test_invalid_app_check_attempts_do_not_consume_valid_client_rate_limit():
    def verifier(token: str):
        if token != "valid-token":
            raise ValueError("invalid token")
        return {"sub": "web-app"}

    test_app = make_protected_app(
        app_check_enabled=True,
        rate_limit_enabled=True,
        request_limit=1,
        verifier=verifier,
    )
    client = test_app.test_client()

    assert client.post("/api/v1/chat/").status_code == 401
    assert (
        client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "invalid-token"}).status_code == 401
    )
    assert (
        client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "valid-token"}).status_code == 200
    )
    limited = client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "valid-token"})
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"


def test_verified_app_identity_partitions_the_process_local_rate_limit():
    test_app = make_protected_app(
        app_check_enabled=True,
        rate_limit_enabled=True,
        request_limit=1,
        verifier=lambda token: {"sub": token.partition(":")[0]},
    )
    client = test_app.test_client()

    assert (
        client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "app-a:first"}).status_code == 200
    )
    assert (
        client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "app-b:first"}).status_code == 200
    )
    assert (
        client.post("/api/v1/chat/", headers={APP_CHECK_HEADER: "app-a:second"}).status_code == 429
    )


def test_rate_limit_is_process_local_sliding_window_and_ignores_spoofed_forwarded_for():
    clock = FakeClock()
    test_app = make_protected_app(request_limit=2, clock=clock)
    client = test_app.test_client()

    assert (
        client.post("/api/v1/chat/", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 200
    )
    assert (
        client.post("/api/v1/chat/", headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200
    )
    limited = client.post("/api/v1/chat/", headers={"X-Forwarded-For": "198.51.100.3"})
    assert limited.status_code == 429
    assert limited.get_json() == {"detail": "Too many chat requests", "retryable": True}

    clock.advance(60)
    assert client.post("/api/v1/chat/").status_code == 200


def test_sliding_window_limiter_bounds_tracked_client_state():
    limiter = ProcessLocalSlidingWindowRateLimiter(
        request_limit=1,
        window_seconds=60,
        max_tracked_clients=2,
        clock=FakeClock(),
    )

    assert limiter.check("client-a").allowed is True
    assert limiter.check("client-b").allowed is True
    assert limiter.check("client-c").allowed is True
    assert len(limiter._requests) == 2


def test_cors_uses_exact_allowlist_without_credentials():
    client = app.test_client()
    production_origins = (
        "https://anti-harassment-bot.web.app",
        "https://anti-harassment-bot.firebaseapp.com",
    )

    for allowed_origin in production_origins:
        allowed = client.get("/api/v1/health/", headers={"Origin": allowed_origin})
        assert allowed.headers["Access-Control-Allow-Origin"] == allowed_origin
        assert "Access-Control-Allow-Credentials" not in allowed.headers

    for denied_origin in (
        "https://evil.example",
        "https://anti-harassment-bot--dev-preview.web.app",
    ):
        denied = client.get("/api/v1/health/", headers={"Origin": denied_origin})
        assert "Access-Control-Allow-Origin" not in denied.headers


def test_cors_allows_only_this_projects_strict_preview_origin_pattern():
    client = app.test_client()
    allowed_origin = "https://anti-harassment-bot--dev-preview-a1b2-c3.web.app"
    denied_origins = [
        "https://other-project--dev-preview-a1b2-c3.web.app",
        "https://anti-harassment-bot--evil-a1b2-c3.web.app",
        "https://anti-harassment-bot--dev-preview-a1b2-c3.web.app.evil.example",
    ]

    allowed = client.get("/api/v1/health/", headers={"Origin": allowed_origin})

    assert allowed.headers["Access-Control-Allow-Origin"] == allowed_origin
    assert "Access-Control-Allow-Credentials" not in allowed.headers
    for denied_origin in denied_origins:
        denied = client.get("/api/v1/health/", headers={"Origin": denied_origin})
        assert "Access-Control-Allow-Origin" not in denied.headers


def test_declared_oversized_body_returns_stable_json(monkeypatch):
    monkeypatch.setitem(app.config, "MAX_CONTENT_LENGTH", 64)

    response = app.test_client().post(
        "/api/v1/chat/",
        data=b"x" * 65,
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json() == {"detail": "Request body too large"}


def test_streamed_oversized_body_without_content_length_returns_stable_json():
    test_app = Flask(__name__)
    register_request_size_limit(test_app, 64)

    @test_app.post("/api/v1/chat/")
    def chat():
        return jsonify({"status": "unexpected"})

    builder = EnvironBuilder(
        path="/api/v1/chat/",
        method="POST",
        input_stream=BytesIO(b"x" * 65),
        content_type="application/json",
    )
    environ = builder.get_environ()
    environ.pop("CONTENT_LENGTH", None)
    environ["wsgi.input_terminated"] = True

    response = test_app.test_client().open(environ)

    assert response.status_code == 413
    assert response.get_json() == {"detail": "Request body too large"}


def test_unexpected_flask_error_returns_opaque_logged_error_id(monkeypatch, caplog):
    def fail():
        raise RuntimeError("sensitive implementation detail")

    monkeypatch.setitem(app.view_functions, "root", fail)

    with caplog.at_level("ERROR"):
        response = app.test_client().get("/")

    payload = response.get_json()
    assert response.status_code == 500
    assert payload["detail"] == "Internal Server Error"
    assert UUID(hex=payload["error_id"]).hex == payload["error_id"]
    assert payload["error_id"] in caplog.text
    assert "sensitive implementation detail" not in response.get_data(as_text=True)
