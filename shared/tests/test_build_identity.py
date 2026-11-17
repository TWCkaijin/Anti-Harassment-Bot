"""Regressions for CI build identity and secret-safe deployment diagnostics."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import deploy, provision_build, provision_delivery


def test_deployment_error_does_not_expose_credentials():
    result = SimpleNamespace(
        returncode=1,
        stdout=json.dumps(
            {"error": {"message": "secret-token-abc", "permission": "iam.serviceAccounts.actAs"}}
        ),
        stderr="password=secret-token-abc",
    )
    with (
        patch.object(deploy.subprocess, "run", return_value=result),
        pytest.raises(RuntimeError) as error,
    ):
        deploy.run(["firebase", "deploy"], capture=True)
    assert "iam.serviceAccounts.actAs" in str(error.value)
    assert "secret-token-abc" not in str(error.value)


@pytest.mark.parametrize(
    "actual", [None, {}, {"buildConfig": {"serviceAccount": "default-compute"}}]
)
def test_preflight_rejects_unconfigured_build_identity(actual):
    with (
        patch.object(deploy, "function_info", return_value=actual),
        pytest.raises(RuntimeError, match="provision_build.py"),
    ):
        deploy.verify_build_account("example", "preview", "api_preview")


def test_build_patch_is_narrow_and_verifies_runtime_preservation():
    class FakeProvisioner:
        project = "example"
        apply = True

        def sa(self, identity):
            return identity + "@example.iam.gserviceaccount.com"

        def run(self, args, **kwargs):
            if args[:2] == ["projects", "describe"]:
                return {"projectNumber": "123"}
            if args[:3] == ["iam", "service-accounts", "list"]:
                return []
            return {}

        def bind_project(self, *args):
            pass

    before = {
        "state": "ACTIVE",
        "buildConfig": {"source": {"storageSource": {"object": "source.zip"}}},
        "serviceConfig": {"environmentVariables": {"SECRET": "never-print"}},
    }
    after = json.loads(json.dumps(before))
    after["buildConfig"]["serviceAccount"] = (
        "projects/example/serviceAccounts/rag-build-dev@example.iam.gserviceaccount.com"
    )
    with patch.object(
        provision_build, "api", side_effect=[before, {"name": "op", "done": True}, after] * 2
    ) as api:
        provision_build.configure(FakeProvisioner(), ["preview"])
    for call in api.call_args_list:
        if call.args[0] == "PATCH":
            assert call.args[1].endswith("?updateMask=buildConfig.serviceAccount")
            assert set(call.args[2]) == {"name", "buildConfig"}
            assert set(call.args[2]["buildConfig"]) == {"serviceAccount"}


def test_source_fingerprint_uses_version_and_rejects_missing_checksums():
    provisioner = SimpleNamespace(
        run=lambda args, **kw: {"size": 42, "md5_hash": "abc", "crc32c_hash": "def"}
    )
    source = {"storageSource": {"bucket": "b", "object": "source.zip", "generation": "123"}}
    assert provision_build.source_fingerprint(provisioner, source) == (42, "abc", "def")
    with pytest.raises(RuntimeError, match="versioned"):
        provision_build.source_fingerprint(provisioner, {"storageSource": {"bucket": "b"}})
    provisioner.run = lambda args, **kw: {"size": 42}
    with pytest.raises(RuntimeError, match="checksum"):
        provision_build.source_fingerprint(provisioner, source)


@pytest.mark.parametrize(
    ("event", "branch", "workflow", "expected"),
    [
        ("workflow_dispatch", "main", "deploy-main.yml", True),
        ("push", "main", "deploy-main.yml", False),
        ("workflow_dispatch", "dev", "deploy-dev.yml", True),
        ("push", "dev", "deploy-dev.yml", True),
        ("pull_request", "dev", "deploy-dev.yml", False),
        ("workflow_dispatch", "main", "deploy-dev.yml", False),
        ("workflow_dispatch", "dev", "deploy-main.yml", False),
    ],
)
def test_federation_restricts_release_events_and_workflows(event, branch, workflow, expected):
    repository = "Example/anti-harassment"
    condition = provision_delivery.federation_condition("123", "456", repository)
    assertion = SimpleNamespace(
        repository_id="123",
        repository_owner_id="456",
        event_name=event,
        ref=f"refs/heads/{branch}",
        workflow_ref=f"{repository}/.github/workflows/{workflow}@refs/heads/{branch}",
    )
    python_condition = condition.replace("&&", " and ").replace("||", " or ")
    assert bool(eval(python_condition, {"__builtins__": {}}, {"assertion": assertion})) is expected
    assertion.repository_owner_id = "wrong-owner"
    assert not eval(python_condition, {"__builtins__": {}}, {"assertion": assertion})


@pytest.mark.parametrize(
    ("service", "expected_base"),
    [("admin", "/api"), ("client", "https://asia-east1-example.cloudfunctions.net/api_preview")],
)
def test_frontend_build_routes_to_its_own_api(service, expected_base, monkeypatch):
    monkeypatch.delenv("VITE_APP_CHECK_ENABLED", raising=False)
    monkeypatch.delenv("CLIENT_FIREBASE_APP_ID", raising=False)
    apps = [
        {"displayName": "RAG Admin Preview", "appId": "admin-preview"},
        {"displayName": "RAG Client Preview", "appId": "client-preview"},
    ]
    with patch.object(deploy, "firebase", side_effect=[apps, {"sdkConfig": {"apiKey": "public"}}]):
        values = deploy.frontend_env("example", "preview", service)
    assert values["VITE_API_BASE_URL"] == expected_base


def test_production_client_build_rejects_ambiguous_web_app_selection(monkeypatch):
    monkeypatch.delenv("CLIENT_FIREBASE_APP_ID", raising=False)
    apps = [
        {"displayName": "Existing Client", "appId": "one"},
        {"displayName": "Other Web App", "appId": "two"},
    ]
    with (
        patch.object(deploy, "firebase", return_value=apps),
        pytest.raises(RuntimeError, match="CLIENT_FIREBASE_APP_ID"),
    ):
        deploy.frontend_env("example", "production", "client")
    monkeypatch.setenv("CLIENT_FIREBASE_APP_ID", "two")
    with patch.object(deploy, "firebase", side_effect=[apps, {"sdkConfig": {"apiKey": "public"}}]):
        values = deploy.frontend_env("example", "production", "client")
    assert values["VITE_FIREBASE_APP_ID"] == "two"


@pytest.mark.parametrize("value", ["tru", "1", "enabled"])
def test_app_check_deploy_flags_reject_typos(value, monkeypatch):
    with pytest.raises(ValueError, match="CHAT_APP_CHECK_ENABLED"):
        deploy.environment_values("example", "preview", "client", app_check_enabled=value)
    monkeypatch.setenv("VITE_APP_CHECK_ENABLED", value)
    with (
        patch.object(
            deploy,
            "firebase",
            side_effect=[
                [{"displayName": "RAG Client Preview", "appId": "client-preview"}],
                {"sdkConfig": {"apiKey": "public"}},
            ],
        ),
        pytest.raises(ValueError, match="VITE_APP_CHECK_ENABLED"),
    ):
        deploy.frontend_env("example", "preview", "client")
