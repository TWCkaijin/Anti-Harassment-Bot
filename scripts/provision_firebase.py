"""Idempotently provision the Client/Admin infrastructure in the selected project.

Default is a plan. --apply creates only the named migration resources and adds
scoped grants; it never removes existing accounts, databases, or IAM bindings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION = "asia-east1"


class Provisioner:
    def __init__(self, project: str, apply: bool):
        self.project = project
        self.apply = apply

    def run(self, args: list[str], *, read=False, data: str | None = None):
        if not read and not self.apply:
            print(json.dumps({"planned": args[:5]}))
            return {}
        result = subprocess.run(
            ["gcloud", *args, "--project", self.project, "--quiet", "--format=json"],
            input=data,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(f"gcloud {args[:4]} failed: {result.stderr[-1800:]}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def sa(self, identity: str) -> str:
        return f"{identity}@{self.project}.iam.gserviceaccount.com"

    def bind_project(self, identity: str, role: str, databases: list[str] | None = None):
        args = [
            "projects",
            "add-iam-policy-binding",
            self.project,
            f"--member=serviceAccount:{self.sa(identity)}",
            f"--role={role}",
        ]
        if databases:
            expression = " || ".join(
                f'resource.name=="projects/{self.project}/databases/{db}"' for db in databases
            )
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
                json.dump({"title": f"{identity}-databases", "expression": expression}, handle)
                handle.flush()
                return self.run([*args, "--condition-from-file", handle.name])
        return self.run([*args, "--condition=None"])

    def databases(self):
        current = self.run(["firestore", "databases", "list"], read=True)
        ids = {item["name"].rsplit("/", 1)[-1] for item in current}
        default = next(item for item in current if item["name"].endswith("/(default)"))
        for database in ["app-dev", "admin-private", "admin-private-dev"]:
            if database not in ids:
                self.run(
                    [
                        "firestore",
                        "databases",
                        "create",
                        f"--database={database}",
                        f"--location={default['locationId']}",
                        "--type=firestore-native",
                        "--delete-protection",
                    ]
                )
            print(
                json.dumps(
                    {"database": database, "location": default["locationId"], "applied": self.apply}
                )
            )

    def identities(self):
        accounts = self.run(["iam", "service-accounts", "list"], read=True)
        existing = {account["email"] for account in accounts}
        for identity in [
            "rag-client-prod",
            "rag-client-dev",
            "rag-admin-prod",
            "rag-admin-dev",
            "rag-tasks-prod",
            "rag-tasks-dev",
            "rag-deploy",
        ]:
            if self.sa(identity) not in existing:
                self.run(
                    [
                        "iam",
                        "service-accounts",
                        "create",
                        identity,
                        f"--display-name=RAG {identity.removeprefix('rag-')}",
                    ]
                )
        for suffix, business, private in [
            ("prod", "(default)", "admin-private"),
            ("dev", "app-dev", "admin-private-dev"),
        ]:
            self.bind_project(f"rag-client-{suffix}", "roles/datastore.viewer", [business])
            self.bind_project(f"rag-admin-{suffix}", "roles/datastore.user", [business, private])
            for app in ["client", "admin"]:
                self.bind_project(f"rag-{app}-{suffix}", "roles/logging.logWriter")
            self.run(
                [
                    "iam",
                    "service-accounts",
                    "add-iam-policy-binding",
                    self.sa(f"rag-tasks-{suffix}"),
                    f"--member=serviceAccount:{self.sa(f'rag-admin-{suffix}')}",
                    "--role=roles/iam.serviceAccountUser",
                ]
            )
        print(json.dumps({"identities": "configured", "applied": self.apply}))

    def storage(self):
        buckets = self.run(["storage", "buckets", "list"], read=True)
        names = {bucket.get("name", "") for bucket in buckets}
        for suffix in ["prod", "dev"]:
            name = f"{self.project}-admin-{suffix}"
            url = f"gs://{name}"
            if name not in names and url not in names:
                self.run(
                    [
                        "storage",
                        "buckets",
                        "create",
                        url,
                        "--location=US",
                        "--uniform-bucket-level-access",
                        "--public-access-prevention",
                    ]
                )
            self.run(
                [
                    "storage",
                    "buckets",
                    "add-iam-policy-binding",
                    url,
                    f"--member=serviceAccount:{self.sa(f'rag-admin-{suffix}')}",
                    "--role=roles/storage.objectAdmin",
                ]
            )
            print(json.dumps({"bucket": name, "applied": self.apply}))

    def tasks(self):
        self.run(
            [
                "services",
                "enable",
                "cloudtasks.googleapis.com",
                "cloudscheduler.googleapis.com",
                "secretmanager.googleapis.com",
                "iamcredentials.googleapis.com",
            ]
        )
        current = (
            self.run(["tasks", "queues", "list", f"--location={REGION}"], read=True)
            if self.apply
            else []
        )
        names = {item["name"].rsplit("/", 1)[-1] for item in current}
        for suffix in ["prod", "dev"]:
            queue = f"rag-admin-{suffix}"
            if queue not in names:
                self.run(
                    [
                        "tasks",
                        "queues",
                        "create",
                        queue,
                        f"--location={REGION}",
                        "--max-concurrent-dispatches=2",
                        "--max-dispatches-per-second=2",
                        "--max-attempts=5",
                        "--min-backoff=10s",
                        "--max-backoff=300s",
                    ]
                )
            self.run(
                [
                    "tasks",
                    "queues",
                    "add-iam-policy-binding",
                    queue,
                    f"--location={REGION}",
                    f"--member=serviceAccount:{self.sa(f'rag-admin-{suffix}')}",
                    "--role=roles/cloudtasks.enqueuer",
                ]
            )
            print(json.dumps({"queue": queue, "applied": self.apply}))

    def model_secrets(self):
        self.run(["services", "enable", "secretmanager.googleapis.com"])
        current = self.run(["secrets", "list"], read=True) if self.apply else []
        names = {item["name"].rsplit("/", 1)[-1] for item in current}
        for suffix, function in [("PROD", "api"), ("DEV", "api_preview")]:
            deployed = self.run(
                ["functions", "describe", function, "--v2", f"--region={REGION}"], read=True
            )
            env = deployed.get("serviceConfig", {}).get("environmentVariables", {})
            key = env.get("OPENROUTER_API_KEY")
            old_signing = env.get("CLARIFICATION_SIGNING_KEY") or env.get("ADMIN_API_KEY") or key
            # Never copy the old Admin credential into a Client-readable secret.
            # The legacy protocol already derives one SHA-256 key per contract;
            # retaining only those derived keys preserves verification without
            # retaining a credential that can authenticate an administrator.
            legacy_keys = (
                json.dumps(
                    {
                        str(version): hashlib.sha256(
                            f"clarification-v{version}:{old_signing}".encode()
                        ).hexdigest()
                        for version in (3, 4)
                    }
                )
                if old_signing
                else None
            )
            for secret, value in [
                (f"RAG_OPENROUTER_{suffix}", key),
                (f"RAG_CLARIFICATION_{suffix}", secrets.token_urlsafe(48)),
                (f"RAG_CLARIFICATION_LEGACY_{suffix}", legacy_keys),
            ]:
                if secret not in names:
                    if not value:
                        raise RuntimeError(
                            f"Missing existing source for {secret}; supply via Secret Manager before deployment"
                        )
                    self.run(["secrets", "create", secret, "--replication-policy=automatic"])
                    self.run(["secrets", "versions", "add", secret, "--data-file=-"], data=value)
                for app in ["client", "admin"]:
                    self.run(
                        [
                            "secrets",
                            "add-iam-policy-binding",
                            secret,
                            f"--member=serviceAccount:{self.sa(f'rag-{app}-{suffix.lower()}')}",
                            "--role=roles/secretmanager.secretAccessor",
                        ]
                    )
                print(json.dumps({"secret": secret, "values_logged": False, "applied": self.apply}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", default=json.loads((ROOT / ".firebaserc").read_text())["projects"]["default"]
    )
    parser.add_argument(
        "--phase",
        choices=["databases", "identities", "storage", "tasks", "secrets", "all"],
        default="all",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    provision = Provisioner(args.project, args.apply)
    methods = {
        "databases": provision.databases,
        "identities": provision.identities,
        "storage": provision.storage,
        "tasks": provision.tasks,
        "secrets": provision.model_secrets,
    }
    for phase in list(methods) if args.phase == "all" else [args.phase]:
        methods[phase]()


if __name__ == "__main__":
    main()
