"""Verify runtime IAM using one fixed synthetic document and status-only output.

Without --apply this prints the exact probe plan and performs no authentication
or network calls. --apply --read-only probes reads; --apply additionally probes
create-only writes and deletes only documents created by this invocation.
Credentials remain in memory and are never included in errors or reports.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

PROJECT = "anti-harassment-bot"
DATABASES = ("(default)", "app-dev", "admin-private", "admin-private-dev")
PROBE_PATH = "migration_permission_probe/not-present"
GCLOUD = "/opt/homebrew/bin/gcloud"
PRINCIPALS = {
    "rag-client-prod": {"read": {"(default)"}, "write": set()},
    "rag-client-dev": {"read": {"app-dev"}, "write": set()},
    "rag-admin-prod": {
        "read": {"(default)", "admin-private"},
        "write": {"(default)", "admin-private"},
    },
    "rag-admin-dev": {
        "read": {"app-dev", "admin-private-dev"},
        "write": {"app-dev", "admin-private-dev"},
    },
}


class ProbeError(RuntimeError):
    """Only fixed, non-sensitive diagnostic codes may leave the auth boundary."""


def principal_email(identity):
    if identity not in PRINCIPALS:
        raise ProbeError("unsupported_identity")
    return f"{identity}@{PROJECT}.iam.gserviceaccount.com"


def access_token(identity=None):
    command = [GCLOUD, "auth", "print-access-token", "--quiet"]
    if identity is not None:
        command.append(f"--impersonate-service-account={principal_email(identity)}")
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        ["/opt/homebrew/bin", "/usr/bin", "/bin", environment.get("PATH", "")]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("authentication_unavailable") from exc
    # Neither stdout nor stderr from credential tooling may enter a report.
    if result.returncode or not result.stdout.strip():
        if (
            "iam.serviceAccounts.getAccessToken" in result.stderr
            or "PERMISSION_DENIED" in result.stderr
        ):
            raise ProbeError("impersonation_permission_denied")
        if "Reauthentication" in result.stderr or "re-authenticate" in result.stderr:
            raise ProbeError("operator_reauthentication_required")
        raise ProbeError(
            "impersonation_unavailable" if identity else "operator_authentication_unavailable"
        )
    return result.stdout.strip()


def document_url(database):
    if database not in DATABASES:
        raise ProbeError("unsupported_database")
    validate_probe_path()
    return (
        f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/"
        f"{quote(database, safe='()')}/documents/{PROBE_PATH}"
    )


def validate_probe_path(path=None):
    """Reject reserved Firestore IDs before authentication or any cloud mutation."""
    selected = PROBE_PATH if path is None else path
    parts = selected.split("/") if isinstance(selected, str) else []
    if len(parts) != 2 or any(
        not part
        or part in {".", ".."}
        or len(part.encode("utf-8")) > 1500
        or re.fullmatch(r"__.*__", part, flags=re.DOTALL)
        for part in parts
    ):
        raise ProbeError("invalid_probe_document_path")
    return selected


def expectation(identity, database, operation):
    return "allow" if database in PRINCIPALS[identity][operation] else "deny"


def probe_plan(*, read_only=False):
    validate_probe_path()
    return {
        "project": PROJECT,
        "document": PROBE_PATH,
        "network_calls": False,
        "read_only": read_only,
        "probes": [
            {
                "principal": principal_email(identity),
                "database": database,
                "read": expectation(identity, database, "read"),
                **({} if read_only else {"write": expectation(identity, database, "write")}),
            }
            for identity in PRINCIPALS
            for database in DATABASES
        ],
    }


class ProbeRunner:
    def __init__(self, *, token_provider=access_token, client=None):
        self.token_provider = token_provider
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.operator_token = None
        self.run_id = uuid.uuid4().hex

    def request(self, method, database, token, **kwargs):
        try:
            return self.client.request(
                method,
                document_url(database),
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ProbeError("probe_transport_failed") from exc

    def cleanup(self, database, token, update_time, *, use_operator=False):
        if not isinstance(update_time, str) or not update_time:
            return {"status": None, "result": "missing_update_time", "complete": False}
        params = {"currentDocument.updateTime": update_time}
        try:
            if use_operator:
                self.operator_token = self.operator_token or self.token_provider()
                token = self.operator_token
            response = self.request("DELETE", database, token, params=params)
            if response.status_code in (200, 404):
                return {
                    "status": response.status_code,
                    "identity": "operator" if use_operator else "runtime",
                    "complete": True,
                }
            self.operator_token = self.operator_token or self.token_provider()
            fallback = self.request("DELETE", database, self.operator_token, params=params)
            return {
                "status": response.status_code,
                "operator_status": fallback.status_code,
                "identity": "operator_fallback",
                "complete": fallback.status_code in (200, 404),
            }
        except ProbeError as exc:
            return {"status": None, "result": str(exc), "complete": False}

    def probe(self, identity, database, token, *, read_only=False, stop_on_failure=False):
        read_expected = expectation(identity, database, "read")
        result = {"principal": principal_email(identity), "database": database}
        try:
            read = self.request("GET", database, token)
        except ProbeError as exc:
            return result | {"result": str(exc), "passed": False}
        result["read"] = {
            "status": read.status_code,
            "expected": read_expected,
            "passed": read.status_code in ({200, 404} if read_expected == "allow" else {403}),
        }
        result["passed"] = result["read"]["passed"]
        if read_only:
            return result
        if stop_on_failure and not result["passed"]:
            return result | {
                "write": {"status": None, "result": "unexpected_read_permission_not_modified"}
            }
        # Never overwrite or delete a document that predates this invocation.
        if read.status_code == 200:
            return result | {
                "write": {"status": None, "result": "preexisting_probe_not_modified"},
                "passed": False,
            }
        if read.status_code not in (403, 404):
            return result | {
                "write": {"status": None, "result": "read_inconclusive"},
                "passed": False,
            }
        write_expected = expectation(identity, database, "write")
        try:
            write = self.request(
                "PATCH",
                database,
                token,
                params={"currentDocument.exists": "false"},
                json={"fields": {"synthetic_permission_probe": {"stringValue": self.run_id}}},
            )
        except ProbeError as exc:
            # A transport error can occur after a server commits. Recovery reads
            # only this fixed synthetic path and deletes only our unique marker.
            return result | {
                "write": {"status": None, "result": str(exc)},
                "cleanup": self.recover_uncertain_write(database),
                "passed": False,
            }
        result["write"] = {
            "status": write.status_code,
            "expected": write_expected,
            "passed": write.status_code == (200 if write_expected == "allow" else 403),
        }
        if write.status_code == 200:
            try:
                update_time = write.json().get("updateTime")
            except (ValueError, AttributeError):
                update_time = None
            result["cleanup"] = (
                self.cleanup(database, token, update_time, use_operator=write_expected == "deny")
                if update_time
                else self.recover_uncertain_write(database)
            )
        result["passed"] = (
            result["passed"]
            and result["write"]["passed"]
            and result.get("cleanup", {}).get("complete", True)
            and (write_expected == "deny" or result.get("cleanup", {}).get("identity") == "runtime")
        )
        return result

    def recover_uncertain_write(self, database):
        try:
            self.operator_token = self.operator_token or self.token_provider()
            response = self.request("GET", database, self.operator_token)
            if response.status_code == 404:
                return {"status": 404, "identity": "operator", "complete": True}
            if response.status_code != 200:
                return {"status": response.status_code, "identity": "operator", "complete": False}
            data = response.json()
            marker = data.get("fields", {}).get("synthetic_permission_probe", {}).get("stringValue")
            if marker != self.run_id:
                return {"status": 200, "result": "unrelated_probe_not_modified", "complete": True}
            return self.cleanup(
                database, self.operator_token, data.get("updateTime"), use_operator=True
            )
        except (ProbeError, ValueError, AttributeError):
            return {"status": None, "result": "uncertain_write_cleanup_failed", "complete": False}

    def run(self, *, read_only=False, progress=None, stop_on_failure=False):
        validate_probe_path()
        rows, authentication = [], []
        stopped_early = False
        for identity in PRINCIPALS:
            try:
                token = self.token_provider(identity)
            except ProbeError as exc:
                auth = {"principal": principal_email(identity), "result": str(exc), "passed": False}
                authentication.append(auth)
                if progress:
                    progress(auth)
                if stop_on_failure:
                    stopped_early = True
                    break
                continue
            authentication.append(
                {"principal": principal_email(identity), "result": "authenticated", "passed": True}
            )
            for database in DATABASES:
                row = self.probe(
                    identity, database, token, read_only=read_only, stop_on_failure=stop_on_failure
                )
                rows.append(row)
                if progress:
                    progress(row)
                if stop_on_failure and not row["passed"]:
                    stopped_early = True
                    break
            token = None
            if stopped_early:
                break
        return {
            "project": PROJECT,
            "document": PROBE_PATH,
            "read_only": read_only,
            "authentication": authentication,
            "probes": rows,
            "stopped_early": stopped_early,
            "passed": all(item["passed"] for item in authentication)
            and len(rows) == 16
            and all(item["passed"] for item in rows),
        }


def write_report(path, report):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.apply:
        report = ProbeRunner().run(
            read_only=args.read_only, progress=lambda row: print(json.dumps(row), flush=True)
        )
    else:
        report = probe_plan(read_only=args.read_only)
    if args.output:
        write_report(args.output, report)
    print(json.dumps(report), flush=True)
    if args.apply and not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
