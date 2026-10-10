"""Queued diagnostics preserve snapshots, failure contracts, and paid outcomes."""

from dataclasses import asdict, replace
from datetime import timedelta

import pytest

from admin_app import api, jobs
from rag_core.core.runtime_config import RuntimeConfig


@pytest.fixture
def runtime(monkeypatch):
    config = RuntimeConfig(
        openrouter_model="fixture/model",
        rag_retrieval_top_k=3,
        enable_anonymization=True,
        temperature=0.2,
        top_p=1.0,
        max_tokens=1200,
    )
    monkeypatch.setattr(api, "get_runtime_config", lambda **kwargs: config)
    monkeypatch.setattr(api, "read_scenario_catalog", lambda: ((), 2))
    return config


def submit(signed_in):
    response = signed_in[0].post(
        "/api/v1/admin/test-jobs",
        headers=signed_in[1],
        json={
            "request": {"message": "synthetic fixture", "contract_version": 4},
            "overrides": {"temperature": 0.5},
        },
    )
    assert response.status_code == 202
    return response.json["job_id"]


def test_diagnostic_submit_worker_poll_preserves_result(signed_in, runtime, monkeypatch, app):
    calls = []
    expected = {"response": {"reply": "fixture"}, "diagnostics": {"trace": []}}
    monkeypatch.setattr(
        api, "run_chat_test", lambda payload, config: calls.append(config) or expected
    )
    job_id = submit(signed_in)
    assert calls == []
    with app.app_context():
        jobs.execute_job(job_id, 0)
        jobs.execute_job(job_id, 0)
    response = signed_in[0].get(f"/api/v1/admin/test-jobs/{job_id}")
    assert response.json["status"] == "succeeded"
    assert response.json["result"] == expected
    assert len(calls) == 1 and calls[0].temperature == 0.5
    assert runtime.temperature == 0.2


def test_diagnostic_drift_fails_before_any_paid_call(signed_in, runtime, monkeypatch, app):
    job_id = submit(signed_in)
    monkeypatch.setattr(
        api, "get_runtime_config", lambda **kwargs: replace(runtime, revision="new")
    )
    monkeypatch.setattr(api, "run_chat_test", lambda *args: pytest.fail("must not call model"))
    with app.app_context():
        jobs.execute_job(job_id, 0)
    job = jobs.get_job(job_id)
    assert job["status"] == "failed"
    assert job["error"]["code"] == "config_snapshot_changed"
    assert job["error"]["http_status"] == 409
    assert job["external_call"] is None


def test_diagnostic_checkpoint_recovers_without_repeating_paid_call(
    signed_in, runtime, monkeypatch, app
):
    job_id = submit(signed_in)
    claimed = jobs._claim(job_id, 0)
    context = jobs.JobContext(job_id, claimed["lease_token"])
    context.external_call_started()
    expected = {"response": {"reply": "already received"}}
    context.external_call_finished(
        checkpoint={"diagnostic_completed": {"result": expected, "http_status": 200}}
    )
    jobs.job_ref(job_id).update({"lease_until": jobs.now() - timedelta(seconds=1)})
    monkeypatch.setattr(api, "run_chat_test", lambda *args: pytest.fail("must not repay"))
    monkeypatch.setattr(api, "_snapshot", lambda: pytest.fail("completed snapshot already saved"))
    recovered = jobs.get_job(job_id)
    with app.app_context():
        jobs.execute_job(job_id, recovered["dispatch_generation"])
    assert jobs.get_job(job_id)["result"] == expected


def test_diagnostic_unknown_model_response_requires_review(signed_in, runtime, monkeypatch, app):
    job_id = submit(signed_in)
    monkeypatch.setattr(
        api, "run_chat_test", lambda *args: ({"detail": "timeout", "retryable": True}, 502)
    )
    with app.app_context():
        jobs.execute_job(job_id, 0)
    job = jobs.get_job(job_id)
    assert job["status"] == "needs_review"
    assert job["error"]["code"] == "external_outcome_unknown"


def test_snapshot_uses_same_config_for_payload_and_contract(runtime, monkeypatch):
    calls = []

    def get_config(**kwargs):
        calls.append(True)
        return runtime if len(calls) == 1 else replace(runtime, revision="torn")

    monkeypatch.setattr(api, "get_runtime_config", get_config)
    snapshot, _ = api._snapshot()
    assert len(calls) == 1
    assert snapshot["config"] == asdict(runtime)
    assert snapshot["prompt_contract"]["revision"] == runtime.revision
