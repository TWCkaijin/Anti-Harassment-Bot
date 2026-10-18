"""Regressions for CI build identity and secret-safe deployment diagnostics."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts import deploy, provision_build


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
