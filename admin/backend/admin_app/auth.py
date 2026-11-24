"""Opaque admin sessions: Firestore is authoritative on every request."""

import hashlib
import hmac
import re
import secrets
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from threading import Lock
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, request
from google.api_core.exceptions import GoogleAPICallError
from grpc import StatusCode

from admin_app import infrastructure
from admin_app.config import get_admin_settings
from rag_core.core.logger import get_logger, get_request_log_context

auth_bp = Blueprint("admin_auth", __name__)
COOKIE_NAME = "__session"
SESSION_COLLECTION = "admin_sessions"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,100}$")
logger = get_logger(__name__)
# Cache rate-budget reservations only; authorization always reads Firestore.
_read_budgets = OrderedDict()
_read_budget_lock = Lock()
_READ_RESERVATION = 8
_MAX_READ_BUDGETS = 1024
_SESSION_TOUCH_SECONDS = 30
_GOOGLE_HTTP_STATUSES = {400, 401, 403, 404, 409, 412, 429, 499, 500, 501, 503, 504}
_TRACE_FIELDS = {
    "logging.googleapis.com/trace",
    "logging.googleapis.com/spanId",
    "logging.googleapis.com/trace_sampled",
}


def _safe_google_status(error):
    # Transaction retries can wrap an API error. Inspect class constants only,
    # never exception messages, response bodies, instance data or traceback text.
    for _ in range(5):
        if isinstance(error, GoogleAPICallError):
            status = getattr(type(error), "grpc_status_code", None)
            if isinstance(status, StatusCode) and status is not StatusCode.OK:
                return status.name
            code = getattr(type(error), "code", None)
            if (
                isinstance(code, int)
                and not isinstance(code, bool)
                and code in _GOOGLE_HTTP_STATUSES
            ):
                return f"HTTP_{int(code)}"
        error = error.__cause__ or error.__context__
        if not isinstance(error, BaseException):
            break
    return None


def _authentication_unavailable(phase, error):
    error_id = uuid.uuid4().hex
    type_name = type(error).__name__
    error_type = (
        type_name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", type_name) else "Exception"
    )
    google_status = _safe_google_status(error)
    trace = {key: value for key, value in get_request_log_context().items() if key in _TRACE_FIELDS}
    # Include identifiers in the message as well as structured fields so the
    # association survives a platform handler that discards logging extras.
    logger.warning(
        "Admin authentication unavailable phase=%s error_type=%s google_status=%s error_id=%s trace=%s",
        phase,
        error_type,
        google_status or "UNCLASSIFIED",
        error_id,
        trace.get("logging.googleapis.com/trace", "-"),
        extra={
            "phase": phase,
            "error_type": error_type,
            "google_status": google_status,
            "error_id": error_id,
            **trace,
        },
        exc_info=False,
    )
    return jsonify({"detail": "Authentication service unavailable", "error_id": error_id}), 503


def now():
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hasher():
    from argon2 import PasswordHasher, Type

    return PasswordHasher(
        time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID
    )


def credential_ref():
    return infrastructure.private_db().collection("admin_credentials").document("shared")


def trusted_origin(origin: str | None) -> bool:
    if not origin:
        return False
    config = get_admin_settings()
    if origin in config.admin_origins and (
        config.environment not in {"production", "preview"} or urlsplit(origin).scheme == "https"
    ):
        return True
    if config.environment == "production":
        return origin in {
            f"https://{config.admin_hosting_site}.web.app",
            f"https://{config.admin_hosting_site}.firebaseapp.com",
        }
    if config.environment == "preview":
        return (
            re.fullmatch(
                rf"https://{re.escape(config.admin_hosting_site)}--dev-preview-[a-z0-9-]+\.web\.app",
                origin,
            )
            is not None
        )
    return False


def _client_bucket() -> str:
    # Never trust a caller-supplied X-Forwarded-For prefix. The global budget
    # remains effective even behind infrastructure with a shared remote address.
    return digest(request.remote_addr or "unknown")


def consume_limits(*, login=False, session_id=None) -> bool:
    config = get_admin_settings()
    current = now()
    window = config.admin_login_window_seconds if login else 60
    epoch = int(current.timestamp()) // window
    limits = (
        [
            (f"login-ip-{_client_bucket()}", config.admin_login_limit),
            ("login-global", config.admin_login_global_limit),
        ]
        if login
        else [
            (
                f"request-{'read' if request.method in {'GET', 'HEAD'} else 'write'}-{session_id}",
                config.admin_read_limit
                if request.method in {"GET", "HEAD"}
                else config.admin_request_limit,
            )
        ]
    )
    db = infrastructure.private_db()
    refs = [
        (db.collection("admin_rate_limits").document(f"{key}-{epoch}"), limit)
        for key, limit in limits
    ]

    def update(transaction, amount=1):
        rows = [
            (ref, limit, ref.get(transaction=transaction).to_dict() or {}) for ref, limit in refs
        ]
        if any(row.get("count", 0) >= limit for _, limit, row in rows):
            return 0
        granted = min(amount, *(limit - row.get("count", 0) for _, limit, row in rows))
        for ref, _, row in rows:
            transaction.set(
                ref,
                {
                    "count": row.get("count", 0) + granted,
                    "expires_at": current + timedelta(seconds=window * 2),
                },
            )
        return granted

    if not login and request.method in {"GET", "HEAD"}:
        # Reserve a small, globally counted budget, then serve it locally. Lost
        # reservations can reduce availability but can never exceed the limit.
        # Serialize refills within a process to avoid a burst of transactions.
        key = (db, config.environment, session_id, epoch, config.admin_read_limit)
        with _read_budget_lock:
            remaining = _read_budgets.pop(key, 0)
            if not remaining:
                remaining = infrastructure.atomic(lambda tx: update(tx, _READ_RESERVATION))
            if not remaining:
                return False
            _read_budgets[key] = remaining - 1
            while len(_read_budgets) > _MAX_READ_BUDGETS:
                _read_budgets.popitem(last=False)
            return True
    return bool(infrastructure.atomic(update))


def _public_session(session):
    return {
        "authenticated": True,
        "csrf_token": session["csrf_token"],
        "expires_at": session["expires_at"].isoformat(),
        "environment": get_admin_settings().environment,
    }


def authenticate_session():
    token = request.cookies.get(COOKIE_NAME, "")
    if not TOKEN_RE.fullmatch(token):
        return None
    session_id = digest(token)
    db = infrastructure.private_db()
    ref = db.collection(SESSION_COLLECTION).document(session_id)
    credentials = credential_ref()
    current = now()
    config = get_admin_settings()

    def valid(session, credential):
        if not session or not credential or not credential.get("enabled") or session.get("revoked"):
            return False
        if session.get("environment") != config.environment or session.get(
            "auth_version"
        ) != credential.get("auth_version"):
            return False
        return current < session["expires_at"] and current - session["last_seen_at"] < timedelta(
            seconds=config.admin_idle_seconds
        )

    # BatchGet does not guarantee response order. No read/write transaction is
    # needed for the common case, and no authorization state is cached locally.
    rows = {
        snapshot.reference.path: snapshot.to_dict() for snapshot in db.get_all([ref, credentials])
    }
    session, credential = rows.get(ref.path), rows.get(credentials.path)
    if not valid(session, credential):
        return None
    touch_interval = timedelta(seconds=min(_SESSION_TOUCH_SECONDS, config.admin_idle_seconds / 2))
    if current - session["last_seen_at"] >= touch_interval:

        def touch(transaction):
            latest = ref.get(transaction=transaction).to_dict()
            credential = credentials.get(transaction=transaction).to_dict()
            if not valid(latest, credential):
                return None
            if current - latest["last_seen_at"] >= touch_interval:
                transaction.update(ref, {"last_seen_at": current})
            return latest

        session = infrastructure.atomic(touch)
        if session is None:
            return None
    return {**session, "session_id": session_id}


def protect_admin_request():
    if request.path.startswith("/internal/"):
        # This namespace has its own OIDC-only verification, never session auth.
        return None
    if request.endpoint in {"admin_auth.login", "admin_auth_direct.login", "admin_health"}:
        return None
    if request.method == "OPTIONS":
        return jsonify({"detail": "Cross-origin admin API access is not supported"}), 403
    origin = request.headers.get("Origin")
    if (origin and not trusted_origin(origin)) or request.headers.get(
        "Sec-Fetch-Site"
    ) == "cross-site":
        return jsonify({"detail": "Untrusted origin"}), 403
    try:
        session = authenticate_session()
    except Exception as error:
        return _authentication_unavailable("authenticate_session", error)
    if session is None:
        return jsonify({"detail": "Authentication required", "authenticated": False}), 401
    g.admin_session = session
    g.admin_identity = "shared-admin"
    if request.method not in {"GET", "HEAD"}:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not trusted_origin(origin) or not hmac.compare_digest(csrf, session["csrf_token"]):
            return jsonify({"detail": "Invalid CSRF token or origin"}), 403
    try:
        if not consume_limits(session_id=session["session_id"]):
            return jsonify({"detail": "Too many requests"}), 429, {"Retry-After": "60"}
    except Exception as error:
        return _authentication_unavailable("consume_limits", error)
    return None


@auth_bp.post("/login")
def login():
    if not trusted_origin(request.headers.get("Origin")):
        return jsonify({"detail": "Untrusted origin"}), 403
    data = request.get_json(silent=True)
    password = data.get("password") if isinstance(data, dict) else None
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        return jsonify({"detail": "Invalid credentials"}), 401
    try:
        if not consume_limits(login=True):
            return (
                jsonify({"detail": "Too many login attempts"}),
                429,
                {"Retry-After": str(get_admin_settings().admin_login_window_seconds)},
            )
        credential = credential_ref().get().to_dict()
        if not credential or not credential.get("enabled"):
            return jsonify({"detail": "Admin login is not configured"}), 503
        from argon2.exceptions import VerificationError

        try:
            verified = password_hasher().verify(credential["password_hash"], password)
        except VerificationError:
            verified = False
        if not verified:
            return jsonify({"detail": "Invalid credentials"}), 401
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        config, current = get_admin_settings(), now()
        session = {
            "csrf_token": csrf,
            "auth_version": credential["auth_version"],
            "environment": config.environment,
            "created_at": current,
            "last_seen_at": current,
            "expires_at": current + timedelta(seconds=config.admin_session_seconds),
            "revoked": False,
        }
        ref = infrastructure.private_db().collection(SESSION_COLLECTION).document(digest(token))

        def persist(transaction):
            latest = credential_ref().get(transaction=transaction).to_dict()
            if (
                not latest
                or not latest.get("enabled")
                or latest.get("auth_version") != credential["auth_version"]
            ):
                return False
            transaction.set(ref, session)
            return True

        if not infrastructure.atomic(persist):
            return jsonify({"detail": "Credentials changed; sign in again"}), 401
        response = jsonify(_public_session(session))
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=config.admin_session_seconds,
            secure=config.admin_cookie_secure,
            httponly=True,
            samesite="Strict",
            path="/",
        )
        return response
    except Exception as error:
        return _authentication_unavailable("login", error)


@auth_bp.get("/session")
def session():
    return jsonify(_public_session(g.admin_session))


@auth_bp.post("/logout")
def logout():
    ref = (
        infrastructure.private_db()
        .collection(SESSION_COLLECTION)
        .document(g.admin_session["session_id"])
    )
    ref.update({"revoked": True, "revoked_at": now()})
    response = jsonify({"authenticated": False})
    response.delete_cookie(
        COOKIE_NAME,
        secure=get_admin_settings().admin_cookie_secure,
        httponly=True,
        samesite="Strict",
        path="/",
    )
    return response
