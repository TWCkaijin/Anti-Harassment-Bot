from datetime import timedelta

from admin_app import auth
from admin_app.password_cli import configure_password


def test_login_sets_opaque_cookie_and_session_response(app, services):
    configure_password("a-long-test-password")
    client = app.test_client()
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"password": "a-long-test-password"},
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 200
    assert response.json["authenticated"] is True
    assert response.json["csrf_token"]
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "a-long-test-password" not in str(services[0].documents)
    raw_token = client.get_cookie("__session").value
    assert raw_token not in str(services[0].documents)
    assert auth.digest(raw_token) in next(
        k for k in services[0].documents if k.startswith("admin_sessions/")
    )


def test_every_request_checks_password_version_and_revocation(signed_in, services):
    client, headers = signed_in
    assert client.get("/api/v1/admin/auth/session").status_code == 200
    configure_password("another-long-password", reset=True)
    assert client.get("/api/v1/admin/auth/session").status_code == 401
    response = client.post(
        "/api/v1/admin/auth/login", json={"password": "another-long-password"}, headers=headers
    )
    assert response.status_code == 200
    headers["X-CSRF-Token"] = response.json["csrf_token"]
    assert client.post("/api/v1/admin/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/auth/session").status_code == 401


def test_disabled_and_expired_sessions_are_rejected(signed_in, services, monkeypatch):
    client, _ = signed_in
    future = auth.now() + timedelta(seconds=services[2].admin_idle_seconds + 1)
    monkeypatch.setattr(auth, "now", lambda: future)
    assert client.get("/api/v1/admin/auth/session").status_code == 401
    configure_password(None, disable=True)
    assert client.get("/api/v1/admin/auth/session").status_code == 401


def test_cookie_secure_is_mandatory_in_deployed_settings():
    import pytest
    from pydantic import ValidationError

    from admin_app.config import AdminSettings

    with pytest.raises(ValidationError):
        AdminSettings(
            environment="production",
            admin_private_database_id="admin-private",
            admin_cookie_secure=False,
        )


def test_mutations_require_origin_and_csrf_before_action(signed_in):
    client, headers = signed_in
    assert client.post("/api/v1/admin/auth/logout").status_code == 403
    assert (
        client.post(
            "/api/v1/admin/auth/logout", headers={**headers, "X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/admin/auth/logout", headers={**headers, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/v1/admin/auth/session", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/admin/auth/session").status_code == 200


def test_static_bearer_headers_do_not_authenticate(app):
    client = app.test_client()
    response = client.get(
        "/api/v1/admin/config",
        headers={"Authorization": "Bearer obsolete-token", "X-Admin-Token": "obsolete-token"},
    )
    assert response.status_code == 401


def test_login_limit_is_shared_across_clients(app, services):
    configure_password("a-long-test-password")
    services[2].admin_login_limit = 2
    for _ in range(2):
        assert (
            app.test_client()
            .post(
                "/api/v1/admin/auth/login",
                json={"password": "wrong"},
                headers={"Origin": "http://localhost:5174"},
            )
            .status_code
            == 401
        )
    assert (
        app.test_client()
        .post(
            "/api/v1/admin/auth/login",
            json={"password": "a-long-test-password"},
            headers={"Origin": "http://localhost:5174"},
        )
        .status_code
        == 429
    )


def test_authentication_store_outage_fails_closed(signed_in, monkeypatch):
    from admin_app import infrastructure

    client, _ = signed_in
    monkeypatch.setattr(
        infrastructure, "private_db", lambda: (_ for _ in ()).throw(RuntimeError("private outage"))
    )
    response = client.get("/api/v1/admin/auth/session")
    assert response.status_code == 503
    assert "private outage" not in response.get_data(as_text=True)


def test_login_origin_and_direct_alias(app):
    configure_password("a-long-test-password")
    client = app.test_client()
    assert (
        client.post(
            "/api/v1/admin/auth/login", json={"password": "a-long-test-password"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/admin/auth/login",
            json={"password": "a-long-test-password"},
            headers={"Origin": "http://localhost:5174"},
        ).status_code
        == 200
    )


def test_worker_route_does_not_accept_browser_session(signed_in):
    client, headers = signed_in
    response = client.post(
        "/internal/jobs/" + "a" * 32 + "/run", json={"dispatch_generation": 0}, headers=headers
    )
    assert response.status_code == 401


def test_bootstrap_hash_is_argon2id_and_requires_explicit_reset(services):
    import pytest

    configure_password("a-long-test-password")
    document = auth.credential_ref().get().to_dict()
    assert document["password_hash"].startswith("$argon2id$")
    with pytest.raises(ValueError, match="--reset"):
        configure_password("another-long-password")


def test_read_burst_reuses_budget_but_checks_live_authorization(signed_in, services, monkeypatch):
    from admin_app import infrastructure

    client, _ = signed_in
    db = services[0]
    session_path = next(key for key in db.documents if key.startswith("admin_sessions/"))
    before = dict(db.documents[session_path])
    reads, transactions = [], []
    get_all, atomic = db.get_all, db.atomic

    def tracked_read(refs):
        reads.append(1)
        return get_all(refs)

    def tracked_atomic(callback):
        transactions.append(1)
        return atomic(callback)

    monkeypatch.setattr(db, "get_all", tracked_read)
    monkeypatch.setattr(infrastructure, "atomic", tracked_atomic)
    for _ in range(16):
        assert client.get("/api/v1/admin/auth/session").status_code == 200
    assert len(reads) == 16
    assert len(transactions) == 2  # Two eight-request reservations, no session writes.
    assert db.documents[session_path] == before
    db.documents[session_path]["revoked"] = True
    assert client.get("/api/v1/admin/auth/session").status_code == 401


def test_heartbeat_is_throttled_and_never_revives_a_revoked_session(
    signed_in, services, monkeypatch
):
    from admin_app import infrastructure

    client, _ = signed_in
    db = services[0]
    path = next(key for key in db.documents if key.startswith("admin_sessions/"))
    initial = db.documents[path]["last_seen_at"]
    monkeypatch.setattr(auth, "now", lambda: initial + timedelta(seconds=31))
    assert client.get("/api/v1/admin/auth/session").status_code == 200
    assert db.documents[path]["last_seen_at"] == initial + timedelta(seconds=31)
    monkeypatch.setattr(auth, "now", lambda: initial + timedelta(seconds=62))

    def revoke_before_touch(callback):
        db.documents[path]["revoked"] = True
        return db.atomic(callback)

    monkeypatch.setattr(infrastructure, "atomic", revoke_before_touch)
    assert client.get("/api/v1/admin/auth/session").status_code == 401
    assert db.documents[path]["last_seen_at"] == initial + timedelta(seconds=31)


def test_read_reservations_preserve_limit_after_cache_loss_and_new_window(
    signed_in, services, monkeypatch
):
    client, _ = signed_in
    services[2].admin_read_limit = 10
    current = auth.now()
    monkeypatch.setattr(auth, "now", lambda: current)
    assert client.get("/api/v1/admin/auth/session").status_code == 200  # reserves 8
    auth._read_budgets.clear()  # A different instance cannot spend that unused reservation.
    for _ in range(2):
        assert client.get("/api/v1/admin/auth/session").status_code == 200
    assert client.get("/api/v1/admin/auth/session").status_code == 429
    monkeypatch.setattr(auth, "now", lambda: current + timedelta(seconds=60))
    assert client.get("/api/v1/admin/auth/session").status_code == 200


def test_concurrent_read_budget_never_exceeds_global_limit(app, services, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    services[2].admin_read_limit = 19
    current = auth.now()
    monkeypatch.setattr(auth, "now", lambda: current)

    def read(_):
        with app.test_request_context("/api/v1/admin/config", method="GET"):
            return auth.consume_limits(session_id="parallel-fixture")

    with ThreadPoolExecutor(max_workers=12) as pool:
        assert sum(pool.map(read, range(40))) == 19
    rows = [
        row
        for path, row in services[0].documents.items()
        if "request-read-parallel-fixture" in path
    ]
    assert len(rows) == 1 and rows[0]["count"] == 19
