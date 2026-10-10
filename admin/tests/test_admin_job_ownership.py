"""Diagnostic IDs and idempotency keys never grant another session access."""

from datetime import timedelta

import pytest

from admin_app import api, auth, jobs, maintenance

from .test_admin_diagnostics import runtime as runtime


@pytest.fixture
def sessions(signed_in, app):
    other = app.test_client()
    login = other.post(
        "/api/v1/admin/auth/login",
        json={"password": "a-long-test-password"},
        headers={"Origin": "http://localhost:5174"},
    )
    assert login.status_code == 200
    return signed_in, (
        other,
        {"Origin": "http://localhost:5174", "X-CSRF-Token": login.json["csrf_token"]},
    )


def submit(session, *, key="session-test", changed=False):
    client, headers = session
    return client.post(
        "/api/v1/admin/test-jobs",
        headers={**headers, "Idempotency-Key": key},
        json={
            "request": {
                "message": "changed synthetic input" if changed else "synthetic input",
                "contract_version": 4,
            },
            # An untrusted JSON field cannot choose or replace the stored owner.
            "owner_session_id": "attacker-supplied-owner",
        },
    )


@pytest.mark.parametrize("prefix", ["/api/v1/admin/jobs", "/v1/admin/test-jobs"])
@pytest.mark.parametrize("operation", ["get", "cancel", "retry"])
def test_other_session_cannot_read_or_mutate_before_any_side_effect(
    sessions, runtime, services, monkeypatch, prefix, operation
):
    owner, other = sessions
    response = submit(owner)
    assert response.status_code == 202
    ref = jobs.job_ref(response.json["job_id"])
    updates = {
        "result_ref": {"bucket": "test-private", "name": "private-result", "generation": "1"}
    }
    if operation == "get":
        updates.update(status="running", lease_until=jobs.now() - timedelta(seconds=1))
    elif operation == "retry":
        updates.update(status="needs_review", external_call={"label": "model"})
    ref.update(updates)
    before = ref.get().to_dict()
    dispatched = list(services[3])
    monkeypatch.setattr(
        services[1], "read_bytes", lambda _: pytest.fail("unauthorized result object read")
    )
    path = f"{prefix}/{ref.id}"
    denied = (
        other[0].get(path)
        if operation == "get"
        else other[0].post(
            f"{path}/{operation}",
            json={"acknowledge_duplicate_cost": True},
            headers=other[1],
        )
    )
    assert denied.status_code == 404 and denied.json["code"] == "job_not_found"
    assert ref.get().to_dict() == before
    assert services[3] == dispatched


@pytest.mark.parametrize("changed", [False, True])
def test_other_session_idempotency_collision_is_denied_before_dispatch_or_fingerprint(
    sessions, runtime, services, changed
):
    owner, other = sessions
    created = submit(owner)
    ref = jobs.job_ref(created.json["job_id"])
    ref.update({"dispatch_pending": True})
    before = ref.get().to_dict()
    dispatched = list(services[3])
    denied = submit(other, changed=changed)
    assert denied.status_code == 404 and denied.json["code"] == "job_not_found"
    assert ref.get().to_dict() == before and services[3] == dispatched
    assert submit(owner).json["job_id"] == ref.id


def test_owner_can_poll_worker_result_and_owner_is_server_bound(
    sessions, runtime, app, monkeypatch
):
    owner, _ = sessions
    created = submit(owner)
    job_id = created.json["job_id"]
    job = jobs.job_ref(job_id).get().to_dict()
    assert job["owner_session_id"] == auth.digest(owner[0].get_cookie(auth.COOKIE_NAME).value)
    monkeypatch.setattr(api, "run_chat_test", lambda *args: {"reply": "synthetic result"})
    with app.app_context():
        jobs.execute_job(job_id, 0)
    response = owner[0].get(f"/api/v1/admin/test-jobs/{job_id}")
    assert response.status_code == 200 and response.json["status"] == "succeeded"
    assert response.json["result"] == {"reply": "synthetic result"}
    assert "owner_session_id" not in response.json


def test_owner_can_cancel_and_explicitly_retry_own_work(sessions, runtime):
    owner, _ = sessions
    cancelled = submit(owner, key="cancel-own").json["job_id"]
    response = owner[0].post(f"/api/v1/admin/jobs/{cancelled}/cancel", headers=owner[1])
    assert response.status_code == 200 and response.json["status"] == "cancelled"
    retry = submit(owner, key="retry-own").json["job_id"]
    jobs.job_ref(retry).update({"status": "needs_review", "external_call": {"label": "model"}})
    path = f"/api/v1/admin/jobs/{retry}/retry"
    assert owner[0].post(path, json={}, headers=owner[1]).status_code == 409
    response = owner[0].post(path, json={"acknowledge_duplicate_cost": True}, headers=owner[1])
    assert response.status_code == 202 and response.json["status"] == "queued"
    assert jobs.job_ref(retry).get().to_dict()["dispatch_generation"] == 1


def test_job_expiry_is_checked_after_ownership_without_mutating(sessions, runtime, services):
    owner, other = sessions
    job_id = submit(owner).json["job_id"]
    ref = jobs.job_ref(job_id)
    ref.update({"expires_at": jobs.now() - timedelta(seconds=1)})
    before = ref.get().to_dict()
    dispatched = list(services[3])
    for client, headers in (other, owner):
        expected = 404 if client is other[0] else 410
        assert client.get(f"/api/v1/admin/jobs/{job_id}").status_code == expected
        for operation in ("cancel", "retry"):
            assert (
                client.post(
                    f"/api/v1/admin/jobs/{job_id}/{operation}", json={}, headers=headers
                ).status_code
                == expected
            )
    assert ref.get().to_dict() == before and services[3] == dispatched


def test_expired_session_and_unbound_legacy_job_do_not_grant_diagnostic_access(
    sessions, runtime, services
):
    owner, other = sessions
    job_id = submit(owner).json["job_id"]
    owner_id = jobs.job_ref(job_id).get().to_dict()["owner_session_id"]
    services[0].collection(auth.SESSION_COLLECTION).document(owner_id).update(
        {"expires_at": jobs.now() - timedelta(seconds=1)}
    )
    assert owner[0].get(f"/api/v1/admin/jobs/{job_id}").status_code == 401
    jobs.job_ref(job_id).update({"owner_session_id": None})
    assert other[0].get(f"/api/v1/admin/jobs/{job_id}").status_code == 404


def test_internal_scheduler_can_reconcile_owned_job_without_browser_session(
    sessions, runtime, app, monkeypatch
):
    job_id = submit(sessions[0]).json["job_id"]
    jobs.job_ref(job_id).update(
        {"status": "running", "lease_until": jobs.now() - timedelta(seconds=1)}
    )
    monkeypatch.setattr(jobs, "verify_worker_request", lambda: True)
    monkeypatch.setattr(maintenance, "cleanup_expired_previews", lambda: 0)
    response = app.test_client().post("/internal/maintenance/reconcile")
    assert response.status_code == 200
    assert jobs.job_ref(job_id).get().to_dict()["status"] == "queued"


def test_corpus_jobs_remain_shared_administrator_operations(sessions, services, monkeypatch):
    monkeypatch.setitem(jobs.HANDLERS, "corpus_import", lambda *args: {})
    job_id = jobs.submit_job("corpus_import", {})["job_id"]
    other, headers = sessions[1]
    assert other.get(f"/api/v1/admin/jobs/{job_id}").status_code == 200
    cancelled = other.post(f"/api/v1/admin/jobs/{job_id}/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json["status"] == "cancelled"


def test_http_corpus_job_access_cannot_cross_environment_before_side_effects(
    sessions, services, monkeypatch
):
    monkeypatch.setitem(jobs.HANDLERS, "corpus_import", lambda *args: {})
    job_id = jobs.submit_job("corpus_import", {})["job_id"]
    ref = jobs.job_ref(job_id)
    ref.update({"environment": "development", "dispatch_pending": True})
    before, dispatched = ref.get().to_dict(), list(services[3])
    client, headers = sessions[0]
    assert client.get(f"/api/v1/admin/jobs/{job_id}").status_code == 404
    for operation in ("cancel", "retry"):
        assert (
            client.post(f"/api/v1/admin/jobs/{job_id}/{operation}", headers=headers).status_code
            == 404
        )
    assert ref.get().to_dict() == before and services[3] == dispatched
