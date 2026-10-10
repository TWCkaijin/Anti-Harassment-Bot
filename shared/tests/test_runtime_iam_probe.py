"""IAM verifier tests use synthetic HTTP replies; no credentials or cloud calls."""

import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import verify_runtime_iam as probe


class SyntheticFirestore:
    def __init__(self):
        self.documents = {}
        self.requests = []
        self.extra_writer = None
        self.timeout_after_write = False
        self.runtime_delete_denied = False

    def request(self, request):
        identity = request.headers["Authorization"].removeprefix("Bearer ")
        database = request.url.path.split("/databases/")[1].split("/")[0]
        assert request.url.path.endswith("/documents/" + probe.PROBE_PATH)
        assert database in probe.DATABASES
        self.requests.append((identity, database, request.method))
        operator = identity == "operator"
        read_allowed = operator or database in probe.PRINCIPALS[identity]["read"]
        write_allowed = (
            operator
            or database in probe.PRINCIPALS[identity]["write"]
            or (identity, database) == self.extra_writer
        )
        if request.method == "GET":
            if not read_allowed:
                return httpx.Response(403)
            return (
                httpx.Response(200, json=self.documents[database])
                if database in self.documents
                else httpx.Response(404)
            )
        if request.method == "PATCH":
            assert request.url.params["currentDocument.exists"] == "false"
            if not write_allowed:
                return httpx.Response(403)
            if database in self.documents:
                return httpx.Response(409)
            data = json.loads(request.content)
            assert set(data["fields"]) == {"synthetic_permission_probe"}
            self.documents[database] = data | {"updateTime": "2026-10-10T00:00:00.000001Z"}
            if self.timeout_after_write:
                self.timeout_after_write = False
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            return httpx.Response(200, json=self.documents[database])
        assert request.method == "DELETE"
        if not write_allowed or (self.runtime_delete_denied and not operator):
            return httpx.Response(403)
        assert (
            request.url.params["currentDocument.updateTime"]
            == self.documents[database]["updateTime"]
        )
        del self.documents[database]
        return httpx.Response(200, json={})


@pytest.fixture
def runner():
    backend = SyntheticFirestore()
    client = httpx.Client(transport=httpx.MockTransport(backend.request))
    instance = probe.ProbeRunner(
        token_provider=lambda identity=None: identity or "operator", client=client
    )
    instance.backend = backend
    yield instance
    client.close()


def test_dry_plan_has_fixed_scope_and_no_authentication(monkeypatch):
    monkeypatch.setattr(probe, "access_token", lambda *args: pytest.fail("plan authenticated"))
    plan = probe.probe_plan()
    assert plan["network_calls"] is False
    assert plan["document"] == "migration_permission_probe/not-present"
    assert len(plan["probes"]) == 16
    assert sum(row["read"] == "allow" for row in plan["probes"]) == 6
    assert sum(row["write"] == "allow" for row in plan["probes"]) == 4


def test_expected_runtime_permissions_pass_and_all_created_probes_are_deleted(runner):
    result = runner.run()
    assert result["passed"] is True
    assert len(result["probes"]) == 16
    assert runner.backend.documents == {}
    assert sum(method == "DELETE" for _, _, method in runner.backend.requests) == 4
    assert runner.run_id not in json.dumps(result)
    assert "fields" not in json.dumps(result)


def test_read_only_mode_never_writes(runner):
    result = runner.run(read_only=True)
    assert result["passed"] is True
    assert {method for _, _, method in runner.backend.requests} == {"GET"}


def test_unexpected_client_write_is_failed_and_cleaned_with_operator(runner):
    runner.backend.extra_writer = ("rag-client-prod", "(default)")
    result = runner.run()
    unexpected = result["probes"][0]
    assert result["passed"] is False
    assert unexpected["write"] == {"status": 200, "expected": "deny", "passed": False}
    assert unexpected["cleanup"]["identity"] == "operator"
    assert unexpected["cleanup"]["complete"] is True
    assert ("operator", "(default)", "DELETE") in runner.backend.requests
    assert runner.backend.documents == {}


def test_existing_document_is_never_modified_or_reported(runner):
    existing = {
        "fields": {"private_fixture": {"stringValue": "do-not-report"}},
        "updateTime": "old",
    }
    runner.backend.documents["(default)"] = existing
    row = runner.probe("rag-admin-prod", "(default)", "rag-admin-prod")
    assert row["passed"] is False
    assert row["write"]["result"] == "preexisting_probe_not_modified"
    assert runner.backend.documents["(default)"] == existing
    assert "do-not-report" not in json.dumps(row)
    assert runner.backend.requests == [("rag-admin-prod", "(default)", "GET")]


def test_write_timeout_recovers_only_this_runs_marker(runner):
    runner.backend.timeout_after_write = True
    row = runner.probe("rag-admin-prod", "(default)", "rag-admin-prod")
    assert row["passed"] is False
    assert row["write"]["result"] == "probe_transport_failed"
    assert row["cleanup"]["complete"] is True
    assert runner.backend.documents == {}


def test_cleanup_falls_back_to_operator_without_changing_iam(runner):
    runner.backend.runtime_delete_denied = True
    row = runner.probe("rag-admin-prod", "(default)", "rag-admin-prod")
    assert row["cleanup"]["identity"] == "operator_fallback"
    assert row["cleanup"]["operator_status"] == 200
    assert row["passed"] is False
    assert runner.backend.documents == {}


def test_failed_impersonation_reports_code_without_running_probes(runner):
    def denied(identity=None):
        raise probe.ProbeError("impersonation_unavailable")

    runner.token_provider = denied
    report = runner.run()
    assert report["passed"] is False
    assert len(report["authentication"]) == 4
    assert report["probes"] == []
    assert runner.backend.requests == []


def test_auth_failure_never_exposes_captured_credentials(monkeypatch):
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="synthetic-sensitive-token",
            stderr="synthetic-sensitive-diagnostic",
        ),
    )
    with pytest.raises(probe.ProbeError, match="^impersonation_unavailable$") as error:
        probe.access_token("rag-client-prod")
    assert "sensitive" not in str(error.value)


def test_auth_permission_denial_is_reported_without_raw_stderr(monkeypatch):
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="PERMISSION_DENIED iam.serviceAccounts.getAccessToken synthetic-private-diagnostic",
        ),
    )
    with pytest.raises(probe.ProbeError, match="^impersonation_permission_denied$"):
        probe.access_token("rag-client-prod")


def test_arbitrary_principal_or_database_is_rejected():
    with pytest.raises(probe.ProbeError, match="unsupported_identity"):
        probe.principal_email("other-identity")
    with pytest.raises(probe.ProbeError, match="unsupported_database"):
        probe.document_url("other-database")


def test_stop_on_unexpected_write_cleans_probe_then_skips_other_scopes(runner):
    runner.backend.extra_writer = ("rag-client-prod", "(default)")
    report = runner.run(stop_on_failure=True)
    assert report["passed"] is False and report["stopped_early"] is True
    assert len(report["probes"]) == len(report["authentication"]) == 1
    assert report["probes"][0]["cleanup"]["complete"] is True
    assert runner.backend.documents == {}
    assert runner.backend.requests == [
        ("rag-client-prod", "(default)", "GET"),
        ("rag-client-prod", "(default)", "PATCH"),
        ("operator", "(default)", "DELETE"),
    ]


def test_unexpected_read_permission_stops_before_attempting_write(runner):
    runner.client.close()
    calls = []

    def permitted(request):
        calls.append(request.method)
        return httpx.Response(404)

    runner.client = httpx.Client(transport=httpx.MockTransport(permitted))
    try:
        row = runner.probe("rag-client-prod", "app-dev", "rag-client-prod", stop_on_failure=True)
    finally:
        runner.client.close()
    assert row["passed"] is False
    assert row["write"]["result"] == "unexpected_read_permission_not_modified"
    assert calls == ["GET"]


def test_cleanup_failure_stops_before_next_database_or_identity(runner, monkeypatch):
    monkeypatch.setattr(runner, "cleanup", lambda *a, **k: {"complete": False})
    report = runner.run(stop_on_failure=True)
    assert report["passed"] is False and report["stopped_early"] is True
    assert len(report["probes"]) == 9
    assert report["probes"][-1]["cleanup"]["complete"] is False
    assert {row["principal"] for row in report["authentication"]} == {
        probe.principal_email(identity)
        for identity in ("rag-client-prod", "rag-client-dev", "rag-admin-prod")
    }
    assert runner.backend.requests[-1] == ("rag-admin-prod", "(default)", "PATCH")


def test_authentication_failure_stops_before_probes_or_other_accounts(runner):
    calls = []

    def denied(identity=None):
        calls.append(identity)
        raise probe.ProbeError("temporary_impersonation_failed")

    runner.token_provider = denied
    report = runner.run(stop_on_failure=True)
    assert report["passed"] is False and report["stopped_early"] is True
    assert calls == ["rag-client-prod"]
    assert report["probes"] == []
    assert runner.backend.requests == []


@pytest.mark.parametrize(
    "path",
    [
        "__migration_permission_probe__/not-present",
        "migration_permission_probe/__reserved__",
        "migration_permission_probe/..",
        "migration_permission_probe/",
        "migration_permission_probe/extra/document",
        "x" * 1501 + "/not-present",
    ],
)
def test_invalid_probe_ids_are_rejected_before_any_authentication(runner, monkeypatch, path):
    monkeypatch.setattr(probe, "PROBE_PATH", path)
    runner.token_provider = lambda *a: pytest.fail("invalid path must fail before authentication")
    with pytest.raises(probe.ProbeError, match="invalid_probe_document_path"):
        runner.run(stop_on_failure=True)
    assert runner.backend.requests == []


def test_fixed_probe_path_uses_legal_nonreserved_ids():
    assert probe.validate_probe_path() == "migration_permission_probe/not-present"
    for database in probe.DATABASES:
        assert probe.document_url(database).endswith(
            "/documents/migration_permission_probe/not-present"
        )
