"""Plan temporary operator impersonation grants for the fixed runtime IAM probe.

Default: read the active gcloud user/project and write a reviewable 30-minute
plan. --apply requires that plan and its exact hash, runs the full synthetic
read/write matrix, then removes only the uniquely conditioned bindings in a
finally block. No application records or credential values enter the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

try:
    from scripts import verify_runtime_iam as probe
except ModuleNotFoundError:
    import verify_runtime_iam as probe

ROLE = "roles/iam.serviceAccountTokenCreator"
CONDITION_DESCRIPTION = "Temporary runtime IAM verification; remove after the synthetic probe"
PLAN_TTL_SECONDS = 1800
TOKEN_MAX_SECONDS = 900


class OperatorProbeError(RuntimeError):
    """Fixed diagnostic codes only; never propagate raw CLI output."""


def credential_error_code(stderr):
    """Classify known failures without exposing credential-tool output."""
    message = stderr.lower()
    if "reauthentication" in message or "re-authenticate" in message or "invalid_grant" in message:
        return "operator_reauthentication_required"
    if "service_disabled" in message:
        return "temporary_impersonation_service_disabled"
    if "permission_denied" in message and "iam.serviceaccounts.getaccesstoken" in message:
        return "temporary_impersonation_permission_denied"
    if "unrecognized arguments" in message or "invalid value for [--lifetime]" in message:
        return "temporary_impersonation_invalid_arguments"
    return "temporary_impersonation_failed"


def utc_now():
    return datetime.now(UTC)


def iso_utc(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise OperatorProbeError("invalid_plan_time") from exc
    if result.tzinfo is None:
        raise OperatorProbeError("invalid_plan_time")
    return result.astimezone(UTC)


def sha256(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_operator(account, project):
    if not isinstance(project, str) or not project.strip():
        raise OperatorProbeError("active_project_setting_unreadable")
    if not isinstance(account, str) or not re.fullmatch(r"[^@\s:]+@[^@\s:]+\.[^@\s:]+", account):
        raise OperatorProbeError("exactly_one_active_user_required")
    if account.endswith("gserviceaccount.com"):
        raise OperatorProbeError("active_operator_must_be_a_user")


def create_plan(account, project, *, now=None):
    probe.validate_probe_path()
    validate_operator(account, project)
    created = (now or utc_now()).replace(microsecond=0)
    expires = created + timedelta(seconds=PLAN_TTL_SECONDS)
    condition = {
        "title": "rag-runtime-iam-probe-" + uuid.uuid4().hex[:20],
        "description": CONDITION_DESCRIPTION,
        "expression": f'request.time < timestamp("{iso_utc(expires)}")',
    }
    plan = {
        "schema_version": 1,
        "project": probe.PROJECT,
        "active_gcloud_project": project,
        "operator_user": account,
        "created_at": iso_utc(created),
        "expires_at": iso_utc(expires),
        "token_max_seconds": TOKEN_MAX_SECONDS,
        "probe_document": probe.PROBE_PATH,
        "write_checks": True,
        "bindings": [
            {
                "service_account": probe.principal_email(identity),
                "member": f"user:{account}",
                "role": ROLE,
                "condition": deepcopy(condition),
            }
            for identity in probe.PRINCIPALS
        ],
    }
    plan["plan_sha256"] = sha256(plan)
    return plan


def validate_plan(plan, expected_hash, account, project, *, now=None):
    probe.validate_probe_path()
    validate_operator(account, project)
    actual_hash = sha256({key: value for key, value in plan.items() if key != "plan_sha256"})
    if actual_hash != expected_hash or plan.get("plan_sha256") != expected_hash:
        raise OperatorProbeError("reviewed_plan_hash_mismatch")
    if plan.get("project") != probe.PROJECT:
        raise OperatorProbeError("unexpected_target_project")
    if plan.get("active_gcloud_project") != project or plan.get("operator_user") != account:
        raise OperatorProbeError("active_operator_or_project_changed")
    current = now or utc_now()
    created, expires = parse_time(plan.get("created_at")), parse_time(plan.get("expires_at"))
    remaining = (expires - current).total_seconds()
    if (expires - created).total_seconds() != PLAN_TTL_SECONDS or created > current + timedelta(
        seconds=5
    ):
        raise OperatorProbeError("invalid_plan_lifetime")
    if not 60 <= remaining <= PLAN_TTL_SECONDS:
        raise OperatorProbeError("plan_expired_or_too_close_to_expiry")
    if (
        plan.get("schema_version") != 1
        or plan.get("token_max_seconds") != TOKEN_MAX_SECONDS
        or plan.get("probe_document") != probe.PROBE_PATH
        or plan.get("write_checks") is not True
    ):
        raise OperatorProbeError("unexpected_probe_scope")
    bindings = plan.get("bindings", [])
    if len(bindings) != len(probe.PRINCIPALS):
        raise OperatorProbeError("unexpected_binding_scope")
    first_condition = bindings[0].get("condition", {})
    title = first_condition.get("title", "")
    if not re.fullmatch(r"rag-runtime-iam-probe-[a-f0-9]{20}", title):
        raise OperatorProbeError("invalid_audit_title")
    condition = {
        "title": title,
        "description": CONDITION_DESCRIPTION,
        "expression": f'request.time < timestamp("{iso_utc(expires)}")',
    }
    expected = [
        {
            "service_account": probe.principal_email(identity),
            "member": f"user:{account}",
            "role": ROLE,
            "condition": condition,
        }
        for identity in probe.PRINCIPALS
    ]
    if bindings != expected:
        raise OperatorProbeError("unexpected_binding_scope")
    return expires


class GcloudOperator:
    def __init__(self):
        self.account = None

    def run(self, args, *, code, credential=False):
        environment = os.environ.copy()
        environment["PATH"] = os.pathsep.join(
            ["/opt/homebrew/bin", "/usr/bin", "/bin", environment.get("PATH", "")]
        )
        command = [probe.GCLOUD, *args, "--quiet"]
        if self.account:
            command.append(f"--account={self.account}")
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=45, check=False, env=environment
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OperatorProbeError(code) from exc
        if result.returncode:
            raise OperatorProbeError(credential_error_code(result.stderr) if credential else code)
        # Returning a credential is permitted only to the in-memory token provider.
        return result.stdout.strip() if credential else result.stdout

    def active_identity(self):
        try:
            accounts = json.loads(
                self.run(
                    ["auth", "list", "--filter=status:ACTIVE", "--format=json"],
                    code="active_account_read_failed",
                )
            )
            active = [row["account"] for row in accounts if row.get("status") == "ACTIVE"]
            project = self.run(
                ["config", "get-value", "project"], code="active_project_read_failed"
            ).strip()
        except (ValueError, TypeError, KeyError) as exc:
            raise OperatorProbeError("active_identity_read_failed") from exc
        if len(active) != 1:
            raise OperatorProbeError("exactly_one_active_user_required")
        validate_operator(active[0], project)
        self.account = active[0]
        return active[0], project

    def policy(self, service_account):
        try:
            return json.loads(
                self.run(
                    [
                        "iam",
                        "service-accounts",
                        "get-iam-policy",
                        service_account,
                        f"--project={probe.PROJECT}",
                        "--format=json",
                    ],
                    code="policy_read_failed",
                )
            )
        except ValueError as exc:
            raise OperatorProbeError("policy_response_invalid") from exc

    def mutate(self, verb, binding, condition_file):
        if verb not in {"add", "remove"}:
            raise OperatorProbeError("unsupported_iam_operation")
        self.run(
            [
                "iam",
                "service-accounts",
                f"{verb}-iam-policy-binding",
                binding["service_account"],
                f"--member={binding['member']}",
                f"--role={ROLE}",
                f"--condition-from-file={condition_file}",
                f"--project={probe.PROJECT}",
                "--format=json",
            ],
            code=f"temporary_binding_{verb}_failed",
        )

    def token_provider(self, expires):
        def token(identity=None):
            args = ["auth", "print-access-token", f"--project={probe.PROJECT}"]
            if identity is not None:
                remaining = math.floor((expires - utc_now()).total_seconds()) - 5
                if remaining < 30:
                    raise probe.ProbeError("temporary_impersonation_window_expired")
                args.extend(
                    [
                        f"--impersonate-service-account={probe.principal_email(identity)}",
                        f"--lifetime={min(TOKEN_MAX_SECONDS, remaining)}",
                    ]
                )
            try:
                value = self.run(args, code="temporary_impersonation_failed", credential=True)
            except OperatorProbeError as exc:
                raise probe.ProbeError(str(exc)) from None
            if not value:
                raise probe.ProbeError("temporary_impersonation_failed")
            return value

        return token


def has_exact_binding(policy, binding):
    return any(
        row.get("role") == binding["role"]
        and row.get("condition") == binding["condition"]
        and binding["member"] in row.get("members", [])
        for row in policy.get("bindings", [])
    )


def policy_digest(policy):
    normalized = [
        {**row, "members": sorted(row.get("members", []))} for row in policy.get("bindings", [])
    ]
    return sha256(sorted(normalized, key=lambda row: json.dumps(row, sort_keys=True)))


def wait_for_propagation(seconds):
    remaining = seconds
    while remaining:
        print(
            json.dumps({"stage": "iam_propagation_wait", "remaining_seconds": remaining}),
            flush=True,
        )
        interval = min(30, remaining)
        time.sleep(interval)
        remaining -= interval


def apply_plan(plan, operator, *, run_probe=None, propagation_wait_seconds=0):
    report = {
        "project": plan["project"],
        "operator_user": plan["operator_user"],
        "plan_sha256": plan["plan_sha256"],
        "expires_at": plan["expires_at"],
        "grants": [],
        "cleanup": [],
        "passed": False,
        "propagation_wait_seconds": propagation_wait_seconds,
    }
    baseline, attempted = {}, []
    try:
        probe.validate_probe_path()
        expires = parse_time(plan["expires_at"])
        if (
            isinstance(propagation_wait_seconds, bool)
            or not isinstance(propagation_wait_seconds, int)
            or not 0 <= propagation_wait_seconds <= 300
        ):
            raise OperatorProbeError("invalid_propagation_wait")
        if (
            propagation_wait_seconds
            and (expires - utc_now()).total_seconds() <= propagation_wait_seconds + 60
        ):
            raise OperatorProbeError("insufficient_plan_window_for_propagation")
        # Read every service-account policy before adding any binding. A title
        # collision aborts the entire operation and preserves existing trust.
        for binding in plan["bindings"]:
            policy = operator.policy(binding["service_account"])
            if any(
                row.get("condition", {}).get("title") == binding["condition"]["title"]
                for row in policy.get("bindings", [])
            ):
                raise OperatorProbeError("audit_title_already_exists")
            baseline[binding["service_account"]] = policy_digest(policy)
        with tempfile.TemporaryDirectory(prefix="rag-iam-probe-") as directory:
            condition_file = Path(directory) / "condition.json"
            probe.write_report(condition_file, plan["bindings"][0]["condition"])
            try:
                for binding in plan["bindings"]:
                    attempted.append(binding)
                    operator.mutate("add", binding, condition_file)
                    if not has_exact_binding(operator.policy(binding["service_account"]), binding):
                        raise OperatorProbeError("temporary_binding_not_observed")
                    report["grants"].append(
                        {"service_account": binding["service_account"], "status": "added"}
                    )
                if propagation_wait_seconds:
                    if (expires - utc_now()).total_seconds() <= propagation_wait_seconds + 60:
                        raise OperatorProbeError("insufficient_plan_window_for_propagation")
                    wait_for_propagation(propagation_wait_seconds)
                    report["propagation_wait_completed"] = True
                if run_probe:
                    report["matrix"] = run_probe(operator.token_provider(expires))
                else:
                    report["matrix"] = probe.ProbeRunner(
                        token_provider=operator.token_provider(expires)
                    ).run(read_only=False, stop_on_failure=True)
            finally:
                for binding in reversed(attempted):
                    cleanup = {"service_account": binding["service_account"]}
                    try:
                        policy = operator.policy(binding["service_account"])
                        if has_exact_binding(policy, binding):
                            operator.mutate("remove", binding, condition_file)
                            policy = operator.policy(binding["service_account"])
                        cleanup["binding_removed"] = not has_exact_binding(policy, binding)
                        cleanup["other_bindings_unchanged"] = (
                            policy_digest(policy) == baseline[binding["service_account"]]
                        )
                        cleanup["status"] = (
                            "removed"
                            if cleanup["binding_removed"]
                            else "still_present_until_expiry"
                        )
                    except OperatorProbeError as exc:
                        cleanup.update(
                            status=str(exc), binding_removed=False, other_bindings_unchanged=None
                        )
                    except Exception:
                        cleanup.update(
                            status="unexpected_cleanup_failure",
                            binding_removed=False,
                            other_bindings_unchanged=None,
                        )
                    report["cleanup"].append(cleanup)
    except OperatorProbeError as exc:
        report["error"] = str(exc)
    except Exception:
        # Exceptions from authentication/HTTP libraries may embed request details.
        report["error"] = "unexpected_probe_failure"
    report["passed"] = (
        not report.get("error")
        and report.get("matrix", {}).get("passed", False)
        and len(report["cleanup"]) == len(plan["bindings"])
        and all(
            row["binding_removed"] and row["other_bindings_unchanged"] for row in report["cleanup"]
        )
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--propagation-wait-seconds", type=int, choices=range(301), default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.apply and (args.plan is None or not args.expected_plan_sha256):
        parser.error("--apply requires --plan and --expected-plan-sha256")
    if not args.apply and (args.plan or args.expected_plan_sha256):
        parser.error("--plan and --expected-plan-sha256 require --apply")
    if args.propagation_wait_seconds and not args.apply:
        parser.error("--propagation-wait-seconds requires --apply")
    if args.output.exists():
        parser.error("--output must not already exist")
    operator = GcloudOperator()
    try:
        account, project = operator.active_identity()
        if args.apply:
            plan = json.loads(args.plan.read_text())
            validate_plan(plan, args.expected_plan_sha256, account, project)
            result = apply_plan(
                plan, operator, propagation_wait_seconds=args.propagation_wait_seconds
            )
        else:
            result = create_plan(account, project)
    except (OperatorProbeError, probe.ProbeError, ValueError) as exc:
        result = {
            "passed": False,
            "error": str(exc)
            if isinstance(exc, (OperatorProbeError, probe.ProbeError))
            else "invalid_plan_json",
        }
    probe.write_report(args.output, result)
    print(json.dumps(result | {"output": str(args.output)}), flush=True)
    if result.get("passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
