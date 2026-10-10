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
