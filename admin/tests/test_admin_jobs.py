from datetime import timedelta

import pytest

from admin_app import jobs


def register(monkeypatch, handler):
    monkeypatch.setitem(jobs.HANDLERS, "fixture", handler)


def test_job_idempotency_does_not_repeat_work(services, monkeypatch):
    calls = []
    register(monkeypatch, lambda job, context: calls.append(job["payload"]) or {"value": 7})
    job = jobs.submit_job("fixture", {"input": 1}, idempotency_key="one")
    same = jobs.submit_job("fixture", {"input": 1}, idempotency_key="one")
    assert same["job_id"] == job["job_id"]
    jobs.execute_job(job["job_id"], 0)
    jobs.execute_job(job["job_id"], 0)
    assert calls == [{"input": 1}]
    assert jobs.get_job(job["job_id"])["result"] == {"value": 7}
    with pytest.raises(jobs.JobFailure, match="different input"):
        jobs.submit_job("fixture", {"input": 2}, idempotency_key="one")


def test_queue_outage_is_durable_and_can_reconcile(services, monkeypatch):
    register(monkeypatch, lambda job, context: {})
    monkeypatch.setattr(
        jobs, "enqueue_job", lambda job: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    job = jobs.submit_job("fixture", {})
    assert jobs.get_job(job["job_id"])["dispatch_pending"] is True
    dispatched = []
    monkeypatch.setattr(jobs, "enqueue_job", lambda job: dispatched.append(job["job_id"]))
    jobs.get_job(job["job_id"])
    assert dispatched == [job["job_id"]]
    assert jobs.job_ref(job["job_id"]).get().to_dict()["dispatch_pending"] is False


def test_unknown_external_outcome_never_auto_replays(services, monkeypatch):
    calls = []

    def handler(job, context):
        context.external_call_started("paid-model")
        calls.append(1)
        raise TimeoutError("model connection vanished")

    register(monkeypatch, handler)
    job = jobs.submit_job("fixture", {})
    jobs.execute_job(job["job_id"], 0)
    assert jobs.get_job(job["job_id"])["status"] == "needs_review"
    jobs.execute_job(job["job_id"], 0)
    assert calls == [1]


def test_expired_external_lease_requires_review(services, monkeypatch):
    register(monkeypatch, lambda job, context: pytest.fail("must not replay"))
    job = jobs.submit_job("fixture", {})
    jobs.job_ref(job["job_id"]).update(
        {
            "status": "running",
            "lease_until": jobs.now() - timedelta(seconds=1),
            "external_call": {"label": "embedding"},
        }
    )
    jobs.execute_job(job["job_id"], 0)
    assert jobs.get_job(job["job_id"])["status"] == "needs_review"


def test_stale_lease_cannot_commit(services, monkeypatch):
    register(monkeypatch, lambda job, context: {})
    job = jobs.submit_job("fixture", {})
    claimed = jobs._claim(job["job_id"], 0)
    old_context = jobs.JobContext(job["job_id"], claimed["lease_token"])
    jobs.job_ref(job["job_id"]).update({"lease_until": jobs.now() - timedelta(seconds=1)})
    newer = jobs._claim(job["job_id"], 0)
    assert newer["lease_token"] != claimed["lease_token"]
    with pytest.raises(jobs.LeaseLost):
        old_context.finish("succeeded", result={"stale": True})


def test_checkpoint_continuation_uses_new_dispatch(services, monkeypatch):
    def handler(job, context):
        if not job["checkpoint"]:
            context.checkpoint({"processed": 1})
            raise jobs.ContinueJob()
        return {"processed": job["checkpoint"]["processed"] + 1}

    register(monkeypatch, handler)
    job = jobs.submit_job("fixture", {})
    jobs.execute_job(job["job_id"], 0)
    pending = jobs.get_job(job["job_id"])
    assert pending["status"] == "queued" and pending["dispatch_generation"] == 1
    jobs.execute_job(job["job_id"], 0)
    assert jobs.get_job(job["job_id"])["status"] == "queued"
    jobs.execute_job(job["job_id"], 1)
    assert jobs.get_job(job["job_id"])["result"] == {"processed": 2}


def test_large_job_input_and_result_use_immutable_objects(services, monkeypatch):
    register(monkeypatch, lambda job, context: {"reply": job["payload"]["text"]})
    job = jobs.submit_job("fixture", {"text": "x" * 220000})
    assert job["input"] is None and job["input_ref"]["generation"] == "1"
    jobs.execute_job(job["job_id"], 0)
    current = jobs.get_job(job["job_id"])
    assert current["result"] is None
    assert jobs.public_job(current)["result"]["reply"] == "x" * 220000


def test_retry_requires_explicit_acknowledgement_for_unknown(signed_in, services, monkeypatch):
    client, headers = signed_in
    register(monkeypatch, lambda job, context: {})
    job = jobs.submit_job("fixture", {})
    jobs.job_ref(job["job_id"]).update(
        {"status": "needs_review", "external_call": {"label": "model"}}
    )
    path = f"/api/v1/admin/jobs/{job['job_id']}/retry"
    assert client.post(path, json={}, headers=headers).status_code == 409
    response = client.post(path, json={"acknowledge_duplicate_cost": True}, headers=headers)
    assert response.status_code == 202
    assert jobs.get_job(job["job_id"])["external_call"] is None


def test_cancel_queued_job_prevents_model_work(signed_in, services, monkeypatch):
    client, headers = signed_in
    register(monkeypatch, lambda job, context: pytest.fail("cancelled job ran"))
    job = jobs.submit_job("fixture", {})
    response = client.post(f"/api/v1/admin/jobs/{job['job_id']}/cancel", headers=headers)
    assert response.status_code == 200 and response.json["status"] == "cancelled"
    jobs.execute_job(job["job_id"], 0)


def test_worker_route_checks_oidc_and_generation(app, services, monkeypatch):
    register(monkeypatch, lambda job, context: {"done": True})
    job = jobs.submit_job("fixture", {})
    path = f"/internal/jobs/{job['job_id']}/run"
    client = app.test_client()
    monkeypatch.setattr(jobs, "verify_worker_request", lambda: False)
    assert client.post(path, json={"dispatch_generation": 0}).status_code == 401
    monkeypatch.setattr(jobs, "verify_worker_request", lambda: True)
    assert client.post(path, json={"dispatch_generation": True}).status_code == 422
    assert client.post(path, json={"dispatch_generation": 0}).status_code == 204
    assert jobs.get_job(job["job_id"])["status"] == "succeeded"


def test_job_failure_is_sanitized_and_preserves_expected_contract_status(services, monkeypatch):
    def fail(job, context):
        raise jobs.JobFailure("chat_test_contract_mismatch", "Refresh test", http_status=409)

    register(monkeypatch, fail)
    job = jobs.submit_job("fixture", {})
    jobs.execute_job(job["job_id"], 0)
    assert jobs.get_job(job["job_id"])["error"] == {
        "code": "chat_test_contract_mismatch",
        "detail": "Refresh test",
        "retryable": False,
        "http_status": 409,
    }


def test_oidc_verifier_checks_audience_and_exact_verified_identity(app, services, monkeypatch):
    from google.oauth2 import id_token

    services[2].admin_tasks_audience = "https://test.run.app"
    services[2].admin_tasks_service_account = "worker@test.iam.gserviceaccount.com"
    seen = []
    claims = {"email": services[2].admin_tasks_service_account, "email_verified": True}

    def verify(token, transport, audience):
        seen.append((token, audience))
        return claims

    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)
    with app.test_request_context(
        "/internal/jobs/a/run", headers={"Authorization": "Bearer signed-token"}
    ):
        assert jobs.verify_worker_request()
        claims["email_verified"] = False
        assert not jobs.verify_worker_request()
        claims.update(email_verified=True, email="other@test.iam.gserviceaccount.com")
        assert not jobs.verify_worker_request()
    assert seen[0] == ("signed-token", "https://test.run.app")


def test_poll_recovers_abandoned_nonexternal_job(services, monkeypatch):
    register(monkeypatch, lambda job, context: {})
    job = jobs.submit_job("fixture", {})
    jobs.job_ref(job["job_id"]).update(
        {"status": "running", "lease_until": jobs.now() - timedelta(seconds=1)}
    )
    recovered = jobs.get_job(job["job_id"])
    assert recovered["status"] == "queued"
    assert recovered["dispatch_generation"] == 1


def test_external_result_checkpoint_and_flag_clear_are_atomic(services, monkeypatch):
    register(monkeypatch, lambda job, context: {})
    job = jobs.submit_job("fixture", {})
    claimed = jobs._claim(job["job_id"], 0)
    context = jobs.JobContext(job["job_id"], claimed["lease_token"])
    context.external_call_started()
    context.external_call_finished(checkpoint={"result_saved": True})
    current = jobs.get_job(job["job_id"])
    assert current["external_call"] is None
    assert current["checkpoint"] == {"result_saved": True}
