"""Temporary IAM orchestration is tested entirely with in-memory policies."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from scripts import verify_runtime_iam_operator as wrapper

NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)
USER = "operator@example.test"


@pytest.fixture
def plan():
    return wrapper.create_plan(USER, wrapper.probe.PROJECT, now=NOW)


class FakeOperator:
    def __init__(self, plan):
        self.policies = {
            binding["service_account"]: {
                "bindings": [
                    {
                        "role": "roles/iam.serviceAccountUser",
                        "members": ["user:existing@example.test"],
                    },
                    {
                        "role": wrapper.ROLE,
                        "members": ["user:other@example.test"],
                        "condition": {"title": "existing-binding", "expression": "false"},
                    },
                ]
            }
            for binding in plan["bindings"]
        }
        self.original = deepcopy(self.policies)
        self.mutations = []
        self.fail_add_number = None
        self.fail_remove = None
        self.token_deadline = None

    def policy(self, service_account):
        return deepcopy(self.policies[service_account])

    def mutate(self, verb, binding, condition_file):
        assert condition_file.is_file()
        assert condition_file.stat().st_mode & 0o777 == 0o600
        self.mutations.append((verb, binding["service_account"]))
        row = {
            "role": binding["role"],
            "members": [binding["member"]],
            "condition": deepcopy(binding["condition"]),
        }
        rows = self.policies[binding["service_account"]]["bindings"]
        if verb == "add":
            rows.append(row)
            if sum(operation == "add" for operation, _ in self.mutations) == self.fail_add_number:
                raise wrapper.OperatorProbeError("temporary_binding_add_failed")
        else:
            if binding["service_account"] == self.fail_remove:
                raise wrapper.OperatorProbeError("temporary_binding_remove_failed")
            rows.remove(row)

    def token_provider(self, expires):
        self.token_deadline = expires
        return lambda identity=None: "synthetic-token"


def rehash(plan):
    plan["plan_sha256"] = wrapper.sha256(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )


def test_plan_is_fixed_to_four_accounts_same_user_and_thirty_minutes(plan):
    deadline = wrapper.validate_plan(
        plan, plan["plan_sha256"], USER, wrapper.probe.PROJECT, now=NOW
    )
    assert deadline == NOW + timedelta(minutes=30)
    assert len(plan["bindings"]) == 4
    assert {row["role"] for row in plan["bindings"]} == {wrapper.ROLE}
    assert {row["member"] for row in plan["bindings"]} == {"user:" + USER}
    assert len({row["condition"]["title"] for row in plan["bindings"]}) == 1
    assert plan["token_max_seconds"] == 900
    assert plan["write_checks"] is True


@pytest.mark.parametrize(
    "offset", [timedelta(minutes=31), timedelta(minutes=29, seconds=30), -timedelta(seconds=10)]
)
def test_expired_near_expiry_or_future_plan_is_rejected(plan, offset):
    with pytest.raises(wrapper.OperatorProbeError):
        wrapper.validate_plan(
            plan, plan["plan_sha256"], USER, wrapper.probe.PROJECT, now=NOW + offset
        )


def test_reviewer_hash_and_active_operator_must_match(plan):
    with pytest.raises(wrapper.OperatorProbeError, match="hash_mismatch"):
        wrapper.validate_plan(plan, "other-hash", USER, wrapper.probe.PROJECT, now=NOW)
    with pytest.raises(wrapper.OperatorProbeError, match="operator_or_project_changed"):
        wrapper.validate_plan(
            plan, plan["plan_sha256"], "another@example.test", wrapper.probe.PROJECT, now=NOW
        )


@pytest.mark.parametrize(
    "mutation",
    ["foreign_member", "other_role", "longer_expiry", "extra_account", "different_document"],
)
def test_scope_tampering_is_rejected_even_after_rehashing(plan, mutation):
    if mutation == "foreign_member":
        plan["bindings"][0]["member"] = "user:someone-else@example.test"
    elif mutation == "other_role":
        plan["bindings"][0]["role"] = "roles/owner"
    elif mutation == "longer_expiry":
        plan["expires_at"] = wrapper.iso_utc(NOW + timedelta(hours=1))
    elif mutation == "extra_account":
        plan["bindings"].append(deepcopy(plan["bindings"][0]))
    else:
        plan["probe_document"] = "runtime_config/app_main"
    rehash(plan)
    with pytest.raises(wrapper.OperatorProbeError):
        wrapper.validate_plan(plan, plan["plan_sha256"], USER, wrapper.probe.PROJECT, now=NOW)


def test_success_removes_only_temporary_bindings_and_keeps_existing_policies(plan):
    operator = FakeOperator(plan)
    report = wrapper.apply_plan(plan, operator, run_probe=lambda token_provider: {"passed": True})
    assert report["passed"] is True
    assert len(report["grants"]) == len(report["cleanup"]) == 4
    assert operator.policies == operator.original
    assert all(
        row["binding_removed"] and row["other_bindings_unchanged"] for row in report["cleanup"]
    )
    assert operator.token_deadline == NOW + timedelta(minutes=30)


def test_failed_matrix_still_removes_all_temporary_bindings(plan):
    operator = FakeOperator(plan)
    report = wrapper.apply_plan(plan, operator, run_probe=lambda token_provider: {"passed": False})
    assert report["passed"] is False
    assert len(report["cleanup"]) == 4
    assert operator.policies == operator.original


def test_operator_matrix_stops_on_failure_and_still_revokes_all_grants(plan, monkeypatch):
    operator = FakeOperator(plan)
    calls = []

    class Runner:
        def __init__(self, *, token_provider):
            assert callable(token_provider)

        def run(self, *, read_only, stop_on_failure):
            calls.append((read_only, stop_on_failure))
            return {"passed": False, "stopped_early": True, "probes": []}

    monkeypatch.setattr(wrapper.probe, "ProbeRunner", Runner)
    report = wrapper.apply_plan(plan, operator)
    assert calls == [(False, True)]
    assert report["passed"] is False and report["matrix"]["stopped_early"] is True
    assert len(report["cleanup"]) == 4
    assert operator.policies == operator.original


def test_exception_during_probe_still_removes_all_temporary_bindings(plan):
    operator = FakeOperator(plan)

    def explode(token_provider):
        raise RuntimeError("do not emit raw sensitive request details")

    report = wrapper.apply_plan(plan, operator, run_probe=explode)
    assert report["error"] == "unexpected_probe_failure"
    assert operator.policies == operator.original
    assert "sensitive" not in str(report)


def test_partially_successful_add_is_also_removed(plan):
    operator = FakeOperator(plan)
    operator.fail_add_number = 2
    report = wrapper.apply_plan(
        plan, operator, run_probe=lambda token_provider: pytest.fail("probe should not start")
    )
    assert report["error"] == "temporary_binding_add_failed"
    assert len(report["cleanup"]) == 2
    assert operator.policies == operator.original


def test_existing_audit_title_aborts_before_any_mutation(plan):
    operator = FakeOperator(plan)
    last = plan["bindings"][-1]
    operator.policies[last["service_account"]]["bindings"].append(
        {"role": wrapper.ROLE, "members": [last["member"]], "condition": last["condition"]}
    )
    before = deepcopy(operator.policies)
    report = wrapper.apply_plan(plan, operator)
    assert report["error"] == "audit_title_already_exists"
    assert operator.mutations == []
    assert operator.policies == before


def test_one_cleanup_failure_does_not_skip_other_accounts(plan):
    operator = FakeOperator(plan)
    operator.fail_remove = plan["bindings"][-1]["service_account"]
    report = wrapper.apply_plan(plan, operator, run_probe=lambda token_provider: {"passed": True})
    assert report["passed"] is False
    assert len(report["cleanup"]) == 4
    assert sum(row["binding_removed"] for row in report["cleanup"]) == 3
    assert report["expires_at"] == wrapper.iso_utc(NOW + timedelta(minutes=30))


def test_tokens_cannot_outlive_grant_expiry(monkeypatch):
    operator = wrapper.GcloudOperator()
    operator.account = USER
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return "synthetic-token"

    monkeypatch.setattr(operator, "run", run)
    monkeypatch.setattr(wrapper, "utc_now", lambda: NOW)
    token_provider = operator.token_provider(NOW + timedelta(seconds=90))
    assert token_provider("rag-client-prod") == "synthetic-token"
    assert "--lifetime=85" in calls[0]
    assert (
        "--impersonate-service-account=" + wrapper.probe.principal_email("rag-client-prod")
        in calls[0]
    )
    capped = operator.token_provider(NOW + timedelta(minutes=30))
    capped("rag-client-dev")
    assert "--lifetime=900" in calls[1]


def test_tokens_are_not_minted_when_temporary_window_is_expiring(monkeypatch):
    operator = wrapper.GcloudOperator()
    monkeypatch.setattr(wrapper, "utc_now", lambda: NOW)
    monkeypatch.setattr(operator, "run", lambda *args, **kwargs: pytest.fail("must not mint token"))
    with pytest.raises(wrapper.probe.ProbeError, match="window_expired"):
        operator.token_provider(NOW + timedelta(seconds=20))("rag-admin-prod")


def test_service_account_cannot_be_used_as_current_operator():
    with pytest.raises(wrapper.OperatorProbeError, match="must_be_a_user"):
        wrapper.create_plan(
            wrapper.probe.principal_email("rag-admin-prod"), wrapper.probe.PROJECT, now=NOW
        )


def test_other_active_project_is_audited_while_target_remains_fixed():
    plan = wrapper.create_plan(USER, "other-active-project", now=NOW)
    assert plan["active_gcloud_project"] == "other-active-project"
    assert plan["project"] == wrapper.probe.PROJECT
    wrapper.validate_plan(plan, plan["plan_sha256"], USER, "other-active-project", now=NOW)
    plan["project"] = "other-active-project"
    rehash(plan)
    with pytest.raises(wrapper.OperatorProbeError, match="unexpected_target_project"):
        wrapper.validate_plan(plan, plan["plan_sha256"], USER, "other-active-project", now=NOW)


def test_bounded_wait_follows_all_grants_and_precedes_matrix_without_extending_expiry(
    plan, monkeypatch
):
    operator = FakeOperator(plan)
    waits = []
    monkeypatch.setattr(wrapper, "utc_now", lambda: NOW)

    def sleep(seconds):
        assert len(operator.mutations) == 4
        assert all(verb == "add" for verb, _ in operator.mutations)
        waits.append(seconds)

    monkeypatch.setattr(wrapper.time, "sleep", sleep)

    def matrix(token_provider):
        assert waits == [30, 30, 30, 30]
        return {"passed": True}

    report = wrapper.apply_plan(plan, operator, run_probe=matrix, propagation_wait_seconds=120)
    assert report["passed"] is True and report["propagation_wait_completed"] is True
    assert report["expires_at"] == plan["expires_at"]
    assert operator.policies == operator.original


def test_wait_cannot_extend_or_consume_expiring_plan(plan, monkeypatch):
    operator = FakeOperator(plan)
    monkeypatch.setattr(wrapper, "utc_now", lambda: NOW + timedelta(minutes=28))
    report = wrapper.apply_plan(plan, operator, propagation_wait_seconds=120)
    assert report["error"] == "insufficient_plan_window_for_propagation"
    assert operator.mutations == [] and operator.policies == operator.original


@pytest.mark.parametrize(
    "stderr,expected",
    [
        (
            "PERMISSION_DENIED iam.serviceAccounts.getAccessToken secret",
            "temporary_impersonation_permission_denied",
        ),
        ("SERVICE_DISABLED secret", "temporary_impersonation_service_disabled"),
        ("Reauthentication required secret", "operator_reauthentication_required"),
        ("unrecognized arguments --lifetime secret", "temporary_impersonation_invalid_arguments"),
        ("private unexpected diagnostic secret", "temporary_impersonation_failed"),
    ],
)
def test_credential_errors_are_classified_without_disclosing_raw_output(
    monkeypatch, stderr, expected
):
    from types import SimpleNamespace

    monkeypatch.setattr(
        wrapper.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="private-token", stderr=stderr),
    )
    monkeypatch.setattr(wrapper, "utc_now", lambda: NOW)
    provider = wrapper.GcloudOperator().token_provider(NOW + timedelta(minutes=30))
    with pytest.raises(wrapper.probe.ProbeError) as error:
        provider("rag-client-prod")
    assert str(error.value) == expected
    assert "secret" not in str(error.value) and "private" not in str(error.value)
