"""Opaque admin sessions: Firestore is authoritative on every request."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from flask import Blueprint, g, jsonify, request

from admin_app import infrastructure
from admin_app.config import get_admin_settings

auth_bp = Blueprint("admin_auth", __name__)
COOKIE_NAME = "__session"
SESSION_COLLECTION = "admin_sessions"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,100}$")


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

    def update(transaction):
        rows = [
            (ref, limit, ref.get(transaction=transaction).to_dict() or {}) for ref, limit in refs
        ]
        if any(row.get("count", 0) >= limit for _, limit, row in rows):
            return False
        for ref, _, row in rows:
            transaction.set(
                ref,
                {
                    "count": row.get("count", 0) + 1,
                    "expires_at": current + timedelta(seconds=window * 2),
                },
            )
        return True

    return infrastructure.atomic(update)


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
    ref = infrastructure.private_db().collection(SESSION_COLLECTION).document(session_id)
    current = now()
    config = get_admin_settings()

    def verify(transaction):
        session = ref.get(transaction=transaction).to_dict()
        credential = credential_ref().get(transaction=transaction).to_dict()
        if not session or not credential or not credential.get("enabled") or session.get("revoked"):
            return None
        if session.get("environment") != config.environment or session.get(
            "auth_version"
        ) != credential.get("auth_version"):
            return None
        if current >= session["expires_at"] or current - session["last_seen_at"] >= timedelta(
            seconds=config.admin_idle_seconds
        ):
            return None
        transaction.update(ref, {"last_seen_at": current})
        return {**session, "session_id": session_id}

    return infrastructure.atomic(verify)


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
    except Exception:
        return jsonify({"detail": "Authentication service unavailable"}), 503
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
    except Exception:
        return jsonify({"detail": "Authentication service unavailable"}), 503
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
    except Exception:
        return jsonify({"detail": "Authentication service unavailable"}), 503


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
