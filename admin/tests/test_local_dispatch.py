"""Local browser-to-worker integration without cloud or model calls."""

import threading
from types import SimpleNamespace

import pytest

from admin_app import jobs, local_dispatch, worker_cli


def test_new_local_job_runs_once_and_old_jobs_are_not_claimed(app, services, monkeypatch):
    executed = []
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def handler(job, context):
        executed.append(job["job_id"])
        started.set()
        assert release.wait(3)
        context.external_call_started("synthetic")
        context.external_call_finished()
        finished.set()
        return {"reply": "complete"}

    monkeypatch.setitem(jobs.HANDLERS, "fixture", handler)
    old = jobs.submit_job("fixture", {})
    dispatcher = local_dispatch.LocalDispatcher(app)
    app.extensions["admin_local_dispatcher"] = dispatcher
    monkeypatch.setattr(jobs, "enqueue_job", lambda job: dispatcher.enqueue(job))
    try:
        with app.app_context():
            job = jobs.submit_job("fixture", {}, idempotency_key="local-test")
            assert started.wait(3)
            dispatcher.enqueue(job)
            with pytest.raises(RuntimeError, match="another local run"):
                dispatcher.enqueue(old)
            release.set()
            assert finished.wait(3)
        dispatcher.pool.shutdown(wait=True)
        assert executed == [job["job_id"]]
        assert jobs.job_ref(job["job_id"]).get().to_dict()["status"] == "succeeded"
        assert jobs.job_ref(old["job_id"]).get().to_dict()["status"] == "queued"
    finally:
        release.set()
        dispatcher.pool.shutdown(wait=True)


def test_local_continuation_and_cancel_keep_existing_job_fences(app, monkeypatch):
    dispatcher = local_dispatch.LocalDispatcher(app)
    app.extensions["admin_local_dispatcher"] = dispatcher
    queued = []
    monkeypatch.setattr(jobs, "enqueue_job", lambda job: queued.append(dict(job)))
    calls = []

    def handler(job, context):
        calls.append(job["dispatch_generation"])
        if not job["checkpoint"]:
            context.checkpoint({"first": True})
            raise jobs.ContinueJob()
        return {"done": True}

    monkeypatch.setitem(jobs.HANDLERS, "fixture", handler)
    try:
        with app.app_context():
            job = jobs.submit_job("fixture", {})
            dispatcher._execute((job["job_id"], 0))
            assert queued[-1]["local_dispatch_id"] == dispatcher.id
            assert queued[-1]["dispatch_generation"] == 1
            dispatcher._execute((job["job_id"], 1))
            assert jobs.get_job(job["job_id"])["status"] == "succeeded"
            cancelled = jobs.submit_job("fixture", {})
            jobs.job_ref(cancelled["job_id"]).update({"cancel_requested": True})
            dispatcher._execute((cancelled["job_id"], 0))
            assert jobs.get_job(cancelled["job_id"])["status"] == "cancelled"
        assert calls == [0, 1]
    finally:
        dispatcher.pool.shutdown(wait=True)


@pytest.mark.parametrize("environment", ["production", "preview", "ci"])
def test_install_rejects_nonlocal_runtime(app, monkeypatch, environment):
    monkeypatch.setattr(
        worker_cli, "get_admin_settings", lambda: SimpleNamespace(environment=environment)
    )
    with pytest.raises(ValueError, match="development"):
        local_dispatch.install(app)
    assert "admin_local_dispatcher" not in app.extensions


def test_eight_local_jobs_can_run_while_ninth_waits(app, monkeypatch):
    """Capacity is exercised with blocked handlers, without cloud/model requests."""
    dispatcher = local_dispatch.LocalDispatcher(app)
    app.extensions["admin_local_dispatcher"] = dispatcher
    monkeypatch.setattr(jobs, "enqueue_job", lambda job: dispatcher.enqueue(job))
    lock, release, eight_started = threading.Lock(), threading.Event(), threading.Event()
    started = []

    def handler(job, context):
        with lock:
            started.append(job["job_id"])
            if len(started) == 8:
                eight_started.set()
        assert release.wait(5)
        return {"done": True}

    monkeypatch.setitem(jobs.HANDLERS, "fixture", handler)
    try:
        with app.app_context():
            submitted = [jobs.submit_job("fixture", {}) for _ in range(9)]
        assert eight_started.wait(5)
        with lock:
            assert len(started) == 8
        release.set()
        dispatcher.pool.shutdown(wait=True)
        assert len(started) == 9
        for job in submitted:
            assert jobs.job_ref(job["job_id"]).get().to_dict()["status"] == "succeeded"
    finally:
        release.set()
        dispatcher.pool.shutdown(wait=True)
