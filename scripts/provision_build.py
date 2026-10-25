"""Provision dedicated function builders; default prints a plan, --apply changes IAM.

Only buildConfig.serviceAccount is patched. No source, runtime configuration,
secret values, or application data are copied. Run as an infrastructure operator.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request

try:
    from scripts.provision_firebase import REGION, Provisioner
except ModuleNotFoundError:  # pragma: no cover
    from provision_firebase import REGION, Provisioner


def api(method, path, body=None):
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    request = urllib.request.Request(
        "https://cloudfunctions.googleapis.com/v2/" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # API bodies can contain configuration values; expose only the status.
        raise RuntimeError(f"Cloud Functions {method} failed: HTTP {error.code}") from None


def source_fingerprint(p, source):
    value = source.get("storageSource", {})
    if not all(value.get(key) for key in ["bucket", "object", "generation"]):
        raise RuntimeError("Cannot verify source identity without a versioned storage object")
    metadata = p.run(
        [
            "storage",
            "objects",
            "describe",
            f"gs://{value['bucket']}/{value['object']}#{value['generation']}",
        ],
        read=True,
    )
    fingerprint = tuple(metadata.get(key) for key in ["size", "md5_hash", "crc32c_hash"])
    if any(value is None for value in fingerprint):
        raise RuntimeError("Missing source checksum metadata")
    return fingerprint


def configure(p, environments):
    number = p.run(["projects", "describe", p.project], read=True)["projectNumber"]
    buckets = [
        f"gcf-v2-sources-{number}-{REGION}",
        f"gcf-v2-uploads-{number}.{REGION}.cloudfunctions.appspot.com",
    ]
    accounts = {x["email"] for x in p.run(["iam", "service-accounts", "list"], read=True)}
    for environment in environments:
        suffix = "dev" if environment == "preview" else "prod"
        identity = "rag-build-" + suffix
        email = p.sa(identity)
        if email not in accounts:
            p.run(
                [
                    "iam",
                    "service-accounts",
                    "create",
                    identity,
                    "--display-name=RAG function build " + suffix,
                ]
            )
        p.bind_project(identity, "roles/logging.logWriter")
        p.run(
            [
                "artifacts",
                "repositories",
                "add-iam-policy-binding",
                "gcf-artifacts",
                f"--location={REGION}",
                f"--member=serviceAccount:{email}",
                "--role=roles/artifactregistry.writer",
            ]
        )
        for bucket in buckets:
            p.run(
                [
                    "storage",
                    "buckets",
                    "add-iam-policy-binding",
                    "gs://" + bucket,
                    f"--member=serviceAccount:{email}",
                    "--role=roles/storage.objectViewer",
                ]
            )
        p.run(
            [
                "iam",
                "service-accounts",
                "add-iam-policy-binding",
                email,
                f"--member=serviceAccount:{p.sa('rag-deploy')}",
                "--role=roles/iam.serviceAccountUser",
            ]
        )
        for base in ["admin_api", "api"]:
            function = base + ("_preview" if suffix == "dev" else "")
            name = f"projects/{p.project}/locations/{REGION}/functions/{function}"
            account = f"projects/{p.project}/serviceAccounts/{email}"
            print(
                json.dumps(
                    {
                        "function": function,
                        "build_service_account": account,
                        "update_mask": "buildConfig.serviceAccount",
                        "apply": p.apply,
                    }
                ),
                flush=True,
            )
            if not p.apply:
                continue
            before = api("GET", name)
            if before.get("buildConfig", {}).get("serviceAccount") == account:
                continue
            if before.get("state") != "ACTIVE":
                raise RuntimeError(f"{function} is not ACTIVE; wait before retrying")
            operation = api(
                "PATCH",
                name + "?updateMask=buildConfig.serviceAccount",
                {"name": name, "buildConfig": {"serviceAccount": account}},
            )
            print(json.dumps({"operation": operation["name"]}), flush=True)
            deadline = time.monotonic() + 1200
            while not operation.get("done"):
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        "Build configuration operation still pending; inspect the printed operation"
                    )
                time.sleep(10)
                operation = api("GET", operation["name"])
            if "error" in operation:
                raise RuntimeError(
                    f"{function}: operation failed with code {operation['error'].get('code')}"
                )
            after = api("GET", name)
            if after["buildConfig"].get("serviceAccount") != account:
                raise RuntimeError(f"{function}: build account verification failed")
            # Compare sensitive configuration in memory, never serialize it.
            for key in [
                "environmentVariables",
                "secretEnvironmentVariables",
                "serviceAccountEmail",
            ]:
                if before.get("serviceConfig", {}).get(key) != after.get("serviceConfig", {}).get(
                    key
                ):
                    raise RuntimeError(f"{function}: unexpected change in serviceConfig.{key}")
            old_source = before["buildConfig"].get("source", {})
            new_source = after["buildConfig"].get("source", {})
            # Cloud Functions can copy the same ZIP to a new generation on a
            # configuration-only update. Compare content metadata, not generation.
            if old_source != new_source and source_fingerprint(p, old_source) != source_fingerprint(
                p, new_source
            ):
                raise RuntimeError(f"{function}: unexpected source content change")
            print(
                json.dumps({"verified": function, "source_and_runtime_preserved": True}), flush=True
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="anti-harassment-bot")
    parser.add_argument(
        "--environment", choices=["preview", "production", "all"], default="preview"
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    configure(
        Provisioner(args.project, args.apply),
        ["preview", "production"] if args.environment == "all" else [args.environment],
    )


if __name__ == "__main__":
    main()
