"""The explicit development runner preserves environment and paid-call fences."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from flask import has_app_context

from admin_app import jobs, worker_cli
from rag_core.core.config import Settings


@pytest.fixture
def local_worker(services, app, monkeypatch):
    settings = services[2]
    settings.environment = "development"
    monkeypatch.setattr(worker_cli, "get_admin_settings", lambda: settings)
    monkeypatch.setattr(
        worker_cli,
        "get_settings",
        lambda: SimpleNamespace(environment="development", firestore_database_id="app-dev"),
    )
    for name in ("K_SERVICE", "FUNCTION_TARGET", "FUNCTION_NAME", "FIRESTORE_EMULATOR_HOST"):
        monkeypatch.delenv(name, raising=False)

    def no_cloud_queue(job):
        raise RuntimeError("Admin queue is not configured")

    monkeypatch.setattr(jobs, "enqueue_job", no_cloud_queue)
    return app


@pytest.mark.parametrize("environment", ["production", "preview", "ci", "test"])
def test_cli_rejects_nondevelopment_even_with_emulator_before_creating_app(
    local_worker, services, monkeypatch, environment
):
    services[2].environment = environment
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    monkeypatch.setattr(worker_cli, "create_app", lambda: pytest.fail("app must not initialize"))
    with pytest.raises(SystemExit) as exc:
        worker_cli.main(["--once"])
    assert exc.value.code == 2


@pytest.mark.parametrize("name", ["K_SERVICE", "FUNCTION_TARGET", "FUNCTION_NAME"])
def test_cli_rejects_cloud_runtime_even_when_environment_says_development(
    local_worker, monkeypatch, name
):
    monkeypatch.setenv(name, "cloud-service")
    with pytest.raises(ValueError, match="deployed"):
        worker_cli.ensure_local_configuration()


def test_cli_rejects_cloud_queue_and_wrong_private_database(local_worker, services):
    services[2].admin_tasks_queue = "preview-queue"
    with pytest.raises(ValueError, match="Clear ADMIN_TASKS_QUEUE"):
        worker_cli.ensure_local_configuration()
    services[2].admin_tasks_queue = ""
    services[2].admin_private_database_id = "admin-private"
    with pytest.raises(ValueError, match="admin-private-dev"):
        worker_cli.ensure_local_configuration()


def test_pass_executes_only_development_queued_or_expired_running_jobs(
    local_worker, services, monkeypatch
):
    calls = []

    def handler(job, context):
        assert has_app_context()
        calls.append(job["job_id"])
        return {"done": True}

    monkeypatch.setitem(jobs.HANDLERS, "fixture", handler)
    foreign = jobs.submit_job("fixture", {})
    jobs.job_ref(foreign["job_id"]).update({"environment": "preview"})
    queued = jobs.submit_job("fixture", {})
    expired = jobs.submit_job("fixture", {})
    active = jobs.submit_job("fixture", {})
    jobs.job_ref(expired["job_id"]).update(
        {"status": "running", "lease_until": jobs.now() - timedelta(seconds=1)}
    )
    jobs.job_ref(active["job_id"]).update(
        {"status": "running", "lease_until": jobs.now() + timedelta(minutes=5)}
    )
    before = {
        item["job_id"]: jobs.job_ref(item["job_id"]).get().to_dict() for item in (foreign, active)
    }
    with local_worker.app_context():
        result = worker_cli.run_once(limit=2)
    assert set(calls) == {queued["job_id"], expired["job_id"]}
    assert result["processed"] == 2 and result["statuses"] == {"succeeded": 2}
    assert services[3] == []
    for job_id, original in before.items():
        assert jobs.job_ref(job_id).get().to_dict() == original


def test_unknown_paid_outcome_is_never_automatically_retried(local_worker, monkeypatch):
    calls = []

    def handler(job, context):
        context.external_call_started("synthetic-paid-provider")
        calls.append(job["job_id"])
        raise TimeoutError("unknown result")

    monkeypatch.setitem(jobs.HANDLERS, "fixture", handler)
    queued = jobs.submit_job("fixture", {})
    interrupted = jobs.submit_job("fixture", {})
    jobs.job_ref(interrupted["job_id"]).update(
        {
            "status": "running",
            "lease_until": jobs.now() - timedelta(seconds=1),
            "external_call": {"label": "already-sent"},
        }
    )
    with local_worker.app_context():
        first = worker_cli.run_once()
        second = worker_cli.run_once()
    assert calls == [queued["job_id"]]
    assert first["statuses"] == {"needs_review": 2} and second["processed"] == 0
    for job in (queued, interrupted):
        assert jobs.job_ref(job["job_id"]).get().to_dict()["error"]["code"] == (
            "external_outcome_unknown"
        )


def test_checkpoint_continuation_waits_for_next_bounded_pass(local_worker, monkeypatch):
    def handler(job, context):
        if not job["checkpoint"]:
            context.checkpoint({"completed": 1})
            raise jobs.ContinueJob()
        return {"completed": job["checkpoint"]["completed"] + 1}

    monkeypatch.setitem(jobs.HANDLERS, "fixture", handler)
    job_id = jobs.submit_job("fixture", {})["job_id"]
    with local_worker.app_context():
        assert worker_cli.run_once()["statuses"] == {"queued": 1}
        checkpointed = jobs.job_ref(job_id).get().to_dict()
        assert checkpointed["checkpoint"] == {"completed": 1}
        assert checkpointed["dispatch_generation"] == 1 and checkpointed["dispatch_pending"]
        assert worker_cli.run_once()["statuses"] == {"succeeded": 1}
    assert jobs.job_ref(job_id).get().to_dict()["result"] == {"completed": 2}


def test_cli_defaults_to_one_pass_and_poll_stops_on_interrupt(local_worker, monkeypatch, capsys):
    monkeypatch.setattr(worker_cli, "create_app", lambda: local_worker)
    assert worker_cli.main([]) == 0
    assert json.loads(capsys.readouterr().out)["processed"] == 0
    sleeps = []

    def stop_polling(seconds):
        sleeps.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(worker_cli.time, "sleep", stop_polling)
    assert worker_cli.main(["--poll-seconds", "0.1"]) == 0
    assert sleeps == [0.1]
    assert json.loads(capsys.readouterr().out)["environment"] == "development"
    # CLI use does not add an HTTP authentication exception.
    assert local_worker.test_client().post(f"/internal/jobs/{'a' * 32}/run").status_code == 401


@pytest.mark.parametrize("args", [["--limit", "0"], ["--poll-seconds", "nan"]])
def test_cli_rejects_unbounded_or_invalid_options(local_worker, args):
    with pytest.raises(SystemExit) as exc:
        worker_cli.main(args)
    assert exc.value.code == 2


def test_invalid_runtime_configuration_does_not_print_model_credentials(
    local_worker, monkeypatch, capsys
):
    monkeypatch.setattr(
        worker_cli,
        "get_settings",
        lambda: Settings(
            environment="development",
            firestore_database_id="(default)",
            openrouter_api_key="synthetic-do-not-print",
        ),
    )
    with pytest.raises(SystemExit) as exc:
        worker_cli.main(["--once"])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "Invalid local worker configuration" in error
    assert "synthetic-do-not-print" not in error
