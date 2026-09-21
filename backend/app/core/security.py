"""HTTP request-boundary protections for the public Flask application."""

from __future__ import annotations

import logging
import math
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any

from firebase_admin import app_check
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

logger = logging.getLogger(__name__)

APP_CHECK_HEADER = "X-Firebase-AppCheck"
CHAT_PATHS = frozenset({"/api/v1/chat", "/api/v1/chat/", "/v1/chat", "/v1/chat/"})
CHAT_ENDPOINTS = frozenset({"chat.chat", "chat_v1.chat"})

AppCheckVerifier = Callable[[str], Mapping[str, Any]]
Clock = Callable[[], float]


def new_error_id() -> str:
    """Create an opaque correlation ID safe to expose in a 500 response."""
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Result of one process-local sliding-window rate-limit check."""

    allowed: bool
    remaining: int
    retry_after_seconds: int | None = None


class ProcessLocalSlidingWindowRateLimiter:
    """Bounded in-memory limiter for one application instance.

    This protects an individual warm Functions/Cloud Run instance from bursts. It
    is deliberately not presented as a global quota: independently scaled
    instances each maintain their own window.
    """

    def __init__(
        self,
        request_limit: int,
        window_seconds: int,
        *,
        max_tracked_clients: int = 10_000,
        clock: Clock = time.monotonic,
    ) -> None:
        if request_limit < 1:
            raise ValueError("request_limit must be positive")
        if window_seconds < 1:
            raise ValueError("window_seconds must be positive")
        if max_tracked_clients < 1:
            raise ValueError("max_tracked_clients must be positive")

        self.request_limit = request_limit
        self.window_seconds = window_seconds
        self.max_tracked_clients = max_tracked_clients
        self._clock = clock
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def check(self, client_key: str) -> RateLimitDecision:
        """Consume one request when the caller is within its sliding window."""
        now = self._clock()
        cutoff = now - self.window_seconds

        with self._lock:
            self._drop_stale_clients(cutoff)
            timestamps = self._requests.setdefault(client_key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            self._requests.move_to_end(client_key)

            if len(timestamps) >= self.request_limit:
                retry_after = max(1, math.ceil(timestamps[0] + self.window_seconds - now))
                return RateLimitDecision(
                    allowed=False,
                    remaining=0,
                    retry_after_seconds=retry_after,
                )

            timestamps.append(now)
            while len(self._requests) > self.max_tracked_clients:
                self._requests.popitem(last=False)
            return RateLimitDecision(
                allowed=True,
                remaining=self.request_limit - len(timestamps),
            )

    def clear(self) -> None:
        """Clear process-local state, primarily for controlled tests."""
        with self._lock:
            self._requests.clear()

    def _drop_stale_clients(self, cutoff: float) -> None:
        while self._requests:
            _, timestamps = next(iter(self._requests.items()))
            if timestamps and timestamps[-1] > cutoff:
                break
            self._requests.popitem(last=False)


class ChatRequestSecurity:
    """Enforce App Check and burst limiting only on expensive chat POSTs."""

    def __init__(
        self,
        *,
        app_check_enabled: bool,
        rate_limit_enabled: bool,
        rate_limiter: ProcessLocalSlidingWindowRateLimiter,
        verify_app_check_token: AppCheckVerifier = app_check.verify_token,
    ) -> None:
        self.app_check_enabled = app_check_enabled
        self.rate_limit_enabled = rate_limit_enabled
        self.rate_limiter = rate_limiter
        self._verify_app_check_token = verify_app_check_token

    def enforce(self) -> Response | None:
        """Return an error response when a protected chat request is rejected."""
        if request.method != "POST" or (
            request.endpoint not in CHAT_ENDPOINTS and request.path not in CHAT_PATHS
        ):
            return None

        client_key = request.remote_addr or "unknown-client"
        if self.app_check_enabled:
            token = request.headers.get(APP_CHECK_HEADER, "").strip()
            if not token:
                return self._unauthorized_app_check_response()

            try:
                claims = self._verify_app_check_token(token)
                if not isinstance(claims, Mapping):
                    raise ValueError("App Check verifier returned invalid claims")
            except Exception as exc:
                logger.warning("App Check verification failed: %s", type(exc).__name__)
                return self._unauthorized_app_check_response()

            app_identity = str(claims.get("app_id") or claims.get("sub") or "unknown-app")
            client_key = f"{app_identity[:256]}:{client_key}"

        if self.rate_limit_enabled:
            decision = self.rate_limiter.check(client_key)
            if not decision.allowed:
                response = jsonify(
                    {
                        "detail": "Too many chat requests",
                        "retryable": True,
                    }
                )
                response.status_code = 429
                response.headers["Retry-After"] = str(decision.retry_after_seconds or 1)
                response.headers["X-RateLimit-Limit"] = str(self.rate_limiter.request_limit)
                response.headers["X-RateLimit-Remaining"] = "0"
                return response
        return None

    @staticmethod
    def _unauthorized_app_check_response() -> Response:
        response = jsonify({"detail": "Invalid or missing Firebase App Check token"})
        response.status_code = 401
        return response


def register_request_size_limit(app: Flask, max_content_length_bytes: int) -> None:
    """Configure Flask's body limit and reject an oversized declared body early."""
    app.config["MAX_CONTENT_LENGTH"] = max_content_length_bytes

    @app.before_request
    def reject_oversized_declared_body() -> Response | None:
        content_length = request.content_length
        configured_limit = app.config["MAX_CONTENT_LENGTH"]
        if content_length is not None and content_length > configured_limit:
            return _request_body_too_large_response()
        if content_length is None and request.method in {"POST", "PUT", "PATCH"}:
            try:
                request.get_data(cache=True)
            except RequestEntityTooLarge:
                return _request_body_too_large_response()
        return None


def _request_body_too_large_response() -> Response:
    response = jsonify({"detail": "Request body too large"})
    response.status_code = 413
    return response


def register_chat_request_security(
    app: Flask,
    *,
    app_check_enabled: bool,
    rate_limit_enabled: bool,
    rate_limit_requests: int,
    rate_limit_window_seconds: int,
    verify_app_check_token: AppCheckVerifier = app_check.verify_token,
    clock: Clock = time.monotonic,
) -> ChatRequestSecurity:
    """Install public chat protections and expose the instance for observability/tests."""
    limiter = ProcessLocalSlidingWindowRateLimiter(
        request_limit=rate_limit_requests,
        window_seconds=rate_limit_window_seconds,
        clock=clock,
    )
    security = ChatRequestSecurity(
        app_check_enabled=app_check_enabled,
        rate_limit_enabled=rate_limit_enabled,
        rate_limiter=limiter,
        verify_app_check_token=verify_app_check_token,
    )
    app.before_request(security.enforce)
    app.extensions["chat_request_security"] = security
    return security


def build_cors_origin_allowlist(
    exact_origins: Sequence[str],
    preview_origin_regexes: Sequence[str],
) -> list[str | re.Pattern[str]]:
    """Build the shared exact-plus-preview allowlist used by both HTTP layers."""
    return [*exact_origins, *(re.compile(pattern) for pattern in preview_origin_regexes)]


def is_cors_origin_allowed(
    origin: str | None,
    exact_origins: Sequence[str],
    preview_origin_regexes: Sequence[str] = (),
) -> bool:
    """Match an Origin against exact entries or a validated anchored preview regex."""
    if not origin:
        return False
    for allowed in build_cors_origin_allowlist(exact_origins, preview_origin_regexes):
        if isinstance(allowed, str):
            if origin == allowed:
                return True
        elif allowed.fullmatch(origin) is not None:
            return True
    return False


def cors_headers_for_origin(
    origin: str | None,
    allowed_origins: Sequence[str],
    preview_origin_regexes: Sequence[str] = (),
) -> dict[str, str]:
    """Return CORS headers only for a shared exact-or-preview allowlist match."""
    if not is_cors_origin_allowed(origin, allowed_origins, preview_origin_regexes):
        return {}
    assert origin is not None
    return {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
    }
