"""Offline checks for the live smoke runner's scope and secret boundaries."""

import json

import httpx
import pytest

from scripts import smoke_preview_corpus as smoke


@pytest.mark.parametrize(
    "url",
    [
        "https://anti-harassment-bot-admin.web.app",
        "https://anti-harassment-bot-admin--prod-preview-abc.web.app",
        "http://anti-harassment-bot-admin--dev-preview-abc.web.app",
        smoke.BASE_URL + "/corpus",
        smoke.BASE_URL + "?redirect=production",
        "https://attacker.example@anti-harassment-bot-admin--dev-preview-abc.web.app",
    ],
)
def test_non_preview_hosts_are_rejected(url):
    with pytest.raises(smoke.SmokeError, match="preview_host_required"):
        smoke.validate_base_url(url)


def test_default_plan_does_not_authenticate_or_create_clients(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network or credential access in plan")

    monkeypatch.setattr(smoke, "access_token", forbidden)
    monkeypatch.setattr(smoke.httpx, "Client", forbidden)
    plan = smoke.smoke_plan(smoke.BASE_URL)
    assert plan["network_calls"] is False
    assert plan["business_database"] == "app-dev"
    assert plan["private_database"] == "admin-private-dev"


def test_fixture_is_generated_and_content_free_of_run_identifier():
    rows, raw, metadata = smoke.synthetic_fixture("fixture-run")
    assert json.loads(raw) == rows
    assert len(rows) == 2
    assert all(set(row) == {"body_text", "extra_json"} for row in rows)
    assert all(row["extra_json"]["synthetic"] is True for row in rows)
    assert metadata == {"synthetic": True, "smoke_run_id": "fixture-run"}
    assert b"fixture-run" not in raw


@pytest.fixture
def runner(tmp_path):
    instance = smoke.CorpusSmoke(smoke.BASE_URL, tmp_path / "not-read.password")
    yield instance
    instance.client.close()
    instance.observer.close()


def test_http_failure_does_not_expose_raw_response(runner):
    runner.client.close()
    runner.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, text="secret cookie and provider response")
        )
    )
    with pytest.raises(smoke.SmokeError) as failure:
        runner.request("GET", "/health")
    assert str(failure.value) == "unexpected_admin_status_500"
    assert runner.report["http_failure"]["status"] == 500
    assert "secret cookie" not in json.dumps(runner.report)


def test_successful_http_trace_excludes_credentials_and_body(runner):
    runner.client.close()
    runner.csrf = "synthetic-csrf-secret"
    runner.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"csrf_token": "synthetic-login-secret"},
                headers={
                    "x-cloud-trace-context": "a" * 32 + "/1;o=1",
                    "set-cookie": "__session=synthetic-session-secret",
                },
            )
        )
    )
    runner.request("GET", "/auth/session")
    trace = runner.report["http_requests"][0]
    assert trace["status"] == 200
    assert trace["trace"] == "a" * 32 + "/1;o=1"
    assert trace["method"] == "GET"
    assert trace["path"] == "/auth/session"
    assert "elapsed_ms" in trace
    assert "secret" not in json.dumps(runner.report)


def test_completed_replay_500_is_preserved_without_retry_or_abort(runner):
    runner.client.close()
    calls = []

    def response(request):
        calls.append(request.method)
        return httpx.Response(500, text="private upstream response")

    runner.client = httpx.Client(transport=httpx.MockTransport(response))
    runner.replay_completed_confirmation(
        "/corpus/imports/owned/confirm", {"plan_hash": "fixture"}, "same-job"
    )
    assert calls == ["POST"]
    assert runner.report["checks"] == [
        {"check": "completed_confirmation_replays_same_job", "passed": False, "status": 500}
    ]
    assert runner.report["failed_phase"] == "completed_confirmation_replay"
    assert not runner.report["passed"]
    assert "private upstream" not in json.dumps(runner.report)


def test_report_redacts_sdk_exception_details(runner, monkeypatch):
    def fail():
        runner.phase = "synthetic_probe"
        raise RuntimeError("private token and raw vector")

    monkeypatch.setattr(runner, "exercise", fail)
    report = runner.run()
    assert report["error"] == "unexpected_smoke_failure"
    assert report["error_type"] == "RuntimeError"
    assert report["failed_phase"] == "synthetic_probe"
    assert "private token" not in json.dumps(report)


def test_cleanup_refuses_unrelated_source(runner, monkeypatch):
    runner.source_id = "src_owned"

    def forbidden(*args, **kwargs):
        raise AssertionError("request must not run")

    monkeypatch.setattr(runner, "json_request", forbidden)
    with pytest.raises(smoke.SmokeError, match="cleanup_source_mismatch"):
        runner.delete_owned_source({"source_id": "src_unrelated", "revision": 1})


def test_cleanup_requires_run_markers_even_when_source_id_matches(runner, monkeypatch):
    runner.import_id = "a" * 32
    runner.source_id = f"src_{runner.import_id}"

    def forbidden(*args, **kwargs):
        raise AssertionError("request must not run")

    monkeypatch.setattr(runner, "json_request", forbidden)
    with pytest.raises(smoke.SmokeError, match="cleanup_source_mismatch"):
        runner.delete_owned_source(
            {
                "source_id": runner.source_id,
                "filename": runner.filename,
                "title": runner.title,
                "kind": "law",
                "status": "active",
                "revision": 1,
                "chunks": [{"metadata": {"smoke_run_id": "different-run"}}],
            }
        )


def test_storage_observer_checks_exact_generation_without_exposing_token(runner):
    runner.token = "fixture-observer-token"
    runner.observer.close()
    observed = []

    def respond(request):
        observed.append(request)
        return httpx.Response(404)

    runner.observer = httpx.Client(transport=httpx.MockTransport(respond))
    assert runner.storage_metadata("corpus/imports/owned/original", "12345") is None
    request = observed[0]
    assert request.url.host == "storage.googleapis.com"
    assert request.url.params["generation"] == "12345"
    assert request.url.params["fields"] == "name,generation,size"
    assert "anti-harassment-bot-admin-dev" in request.url.path
    assert runner.token not in json.dumps(runner.report)


def test_failure_cleanup_waits_for_known_import_before_deleting(runner, monkeypatch):
    events = []

    def fail():
        runner.source_id = "src_owned"
        runner.import_job_id = "import_owned"
        raise smoke.SmokeError("synthetic_failure")

    def wait_job(job_id, kind, *, require_success=True):
        assert require_success is False
        events.append(("wait", job_id, kind))
        return {"status": "succeeded"}

    def request(method, path, **kwargs):
        events.append((method, path))
        return httpx.Response(
            200, json={"source_id": "src_owned", "status": "active", "revision": 1}
        )

    monkeypatch.setattr(runner, "exercise", fail)
    monkeypatch.setattr(runner, "wait_job", wait_job)
    monkeypatch.setattr(runner, "request", request)
    monkeypatch.setattr(
        runner, "delete_owned_source", lambda source: events.append(("delete", source["source_id"]))
    )
    report = runner.run()
    assert report["passed"] is False
    assert report["recovery_source_deleted"] is True
    assert events == [
        ("wait", "import_owned", "corpus_import"),
        ("GET", "/corpus/sources/src_owned"),
        ("delete", "src_owned"),
    ]


def test_uncertain_import_timeout_never_reports_cleanup_success(runner, monkeypatch):
    def fail():
        runner.source_id = "src_owned"
        runner.import_job_id = "import_owned"
        raise smoke.SmokeError("synthetic_failure")

    def timeout(*args, **kwargs):
        raise smoke.SmokeError("job_wait_timeout")

    def forbidden(*args, **kwargs):
        raise AssertionError("source absence is not proof while import is pending")

    monkeypatch.setattr(runner, "exercise", fail)
    monkeypatch.setattr(runner, "wait_job", timeout)
    monkeypatch.setattr(runner, "request", forbidden)
    report = runner.run()
    assert report["passed"] is False
    assert report["cleanup_error"] == "owned_source_cleanup_incomplete"
    assert not report.get("recovery_source_absent")
    assert not report.get("cleanup_complete")
