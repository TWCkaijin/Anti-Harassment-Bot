"""Configure the already-provisioned Admin queues after a Firebase function deploy.

Default is a read-only plan; --apply adds scoped worker IAM, a Scheduler job,
private Firestore indexes/TTL, and lifecycle rules for diagnostic job objects.
It never deletes resources or changes corpus source retention. Re-run after each
admin function URL change and redeploy with the emitted nonsecret environment.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

REGION = "asia-east1"
ROOT = Path(__file__).resolve().parents[1]


class WorkerSetup:
    def __init__(self, project, environment, *, apply=False, target_url=None):
        self.project, self.environment, self.apply = project, environment, apply
        self.suffix = "prod" if environment == "production" else "dev"
        self.database = "admin-private" if environment == "production" else "admin-private-dev"
        self.function = "admin_api" if environment == "production" else "admin_api_preview"
        self.queue = f"rag-admin-{self.suffix}"
        self.worker = f"rag-tasks-{self.suffix}@{project}.iam.gserviceaccount.com"
        self.admin = f"rag-admin-{self.suffix}@{project}.iam.gserviceaccount.com"
        self.bucket = f"{project}-admin-{self.suffix}"
        deployed = self.run(
            ["functions", "describe", self.function, "--v2", f"--region={REGION}"], read=True
        )
        service = deployed["serviceConfig"]
        self.url = (target_url or service["uri"]).rstrip("/")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname.endswith(".run.app")
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Worker target must be the default HTTPS run.app service URL")
        if target_url and target_url.rstrip("/") != service["uri"].rstrip("/"):
            raise ValueError("Target does not match the selected deployed function")
        self.service = service["service"].rsplit("/", 1)[-1]

    def run(self, args, *, read=False):
        if not read and not self.apply:
            print(json.dumps({"planned": args}, ensure_ascii=False))
            return {}
        result = subprocess.run(
            ["gcloud", *args, "--project", self.project, "--quiet", "--format=json"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"gcloud {args[:4]} failed: {result.stderr[-2000:]}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def settings(self):
        return {
            "ADMIN_TASKS_TARGET_URL": self.url,
            "ADMIN_TASKS_AUDIENCE": self.url,
            "ADMIN_TASKS_SERVICE_ACCOUNT": self.worker,
            "ADMIN_TASKS_QUEUE": self.queue,
            "ADMIN_TASKS_LOCATION": REGION,
            "ADMIN_STORAGE_BUCKET": self.bucket,
            "ADMIN_PRIVATE_DATABASE_ID": self.database,
        }

    def configure(self):
        self.run(
            ["services", "enable", "cloudscheduler.googleapis.com", "cloudtasks.googleapis.com"]
        )
        for service in ("cloudscheduler.googleapis.com", "cloudtasks.googleapis.com"):
            self.run(["beta", "services", "identity", "create", f"--service={service}"])
        # Both OIDC producers use their standard service-agent roles to mint
        # tokens. No project-wide Token Creator grant is necessary.
        number = self.run(["projects", "describe", self.project], read=True)["projectNumber"]
        for agent, role in [
            (
                f"service-{number}@gcp-sa-cloudtasks.iam.gserviceaccount.com",
                "roles/cloudtasks.serviceAgent",
            ),
            (
                f"service-{number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com",
                "roles/cloudscheduler.serviceAgent",
            ),
        ]:
            self.run(
                [
                    "projects",
                    "add-iam-policy-binding",
                    self.project,
                    f"--member=serviceAccount:{agent}",
                    f"--role={role}",
                    "--condition=None",
                ]
            )
        # Firebase's public HTTP invoker policy already includes this caller.
        # Internal endpoints independently verify the exact OIDC identity.
        # An extra run.invoker binding would conflict with Firebase CLI's
        # declared [allUsers] policy and force IAM writes on every deployment.
        self.run(
            [
                "iam",
                "service-accounts",
                "add-iam-policy-binding",
                self.worker,
                f"--member=serviceAccount:{self.admin}",
                "--role=roles/iam.serviceAccountUser",
            ]
        )
        self.run(
            [
                "tasks",
                "queues",
                "add-iam-policy-binding",
                self.queue,
                f"--location={REGION}",
                f"--member=serviceAccount:{self.admin}",
                "--role=roles/cloudtasks.enqueuer",
            ]
        )
        self.run(
            [
                "tasks",
                "queues",
                "update",
                self.queue,
                f"--location={REGION}",
                "--max-concurrent-dispatches=8",
                "--max-dispatches-per-second=2",
                "--max-attempts=5",
                "--min-backoff=10s",
                "--max-backoff=300s",
                "--log-sampling-ratio=0.1",
            ]
        )
        self.firestore()
        self.storage()
        existing = self.run(["scheduler", "jobs", "list", f"--location={REGION}"], read=True)
        name = f"rag-admin-reconcile-{self.suffix}"
        verb = "update" if any(item["name"].endswith(f"/{name}") for item in existing) else "create"
        headers_flag = "--update-headers" if verb == "update" else "--headers"
        self.run(
            [
                "scheduler",
                "jobs",
                verb,
                "http",
                name,
                f"--location={REGION}",
                "--schedule=*/5 * * * *",
                "--time-zone=Etc/UTC",
                f"--uri={self.url}/internal/maintenance/reconcile",
                "--http-method=POST",
                f"{headers_flag}=Content-Type=application/json",
                "--message-body={}",
                f"--oidc-service-account-email={self.worker}",
                f"--oidc-token-audience={self.url}",
                "--attempt-deadline=180s",
                "--max-retry-attempts=2",
                "--min-backoff=30s",
                "--max-backoff=120s",
            ]
        )

    def firestore(self):
        # Expiry is enforced synchronously by the API; TTL is eventual cleanup.
        # corpus_imports.expires_at is a confirmation deadline, NEVER a TTL.
        current = self.run(
            ["firestore", "fields", "ttls", "list", f"--database={self.database}"], read=True
        )
        for collection, field_name in [
            ("admin_sessions", "expires_at"),
            ("admin_rate_limits", "expires_at"),
            ("admin_jobs", "expires_at"),
            ("corpus_imports", "cleanup_expires_at"),
        ]:
            suffix = f"/collectionGroups/{collection}/fields/{field_name}"
            enabled = any(
                item.get("name", "").endswith(suffix)
                and item.get("ttlConfig", {}).get("state") in {"ACTIVE", "CREATING"}
                for item in current
            )
            if not enabled:
                self.run(
                    [
                        "firestore",
                        "fields",
                        "ttls",
                        "update",
                        field_name,
                        f"--collection-group={collection}",
                        f"--database={self.database}",
                        "--enable-ttl",
                        "--async",
                    ]
                )
        indexes = self.run(
            ["firestore", "indexes", "composite", "list", f"--database={self.database}"], read=True
        )
        for collection, fields in [
            ("admin_jobs", [("status", "ASCENDING"), ("updated_at", "ASCENDING")]),
            (
                "admin_jobs",
                [
                    ("environment", "ASCENDING"),
                    ("status", "ASCENDING"),
                    ("updated_at", "ASCENDING"),
                ],
            ),
            ("corpus_imports", [("status", "ASCENDING"), ("expires_at", "ASCENDING")]),
            (
                "admin_jobs",
                [("environment", "ASCENDING"), ("kind", "ASCENDING"), ("created_at", "DESCENDING")],
            ),
        ]:
            present = any(
                f"/collectionGroups/{collection}/" in item.get("name", "")
                and [
                    (field.get("fieldPath"), field.get("order"))
                    for field in item.get("fields", [])
                    if field.get("fieldPath") != "__name__"
                ]
                == fields
                for item in indexes
            )
            if not present:
                self.run(
                    [
                        "firestore",
                        "indexes",
                        "composite",
                        "create",
                        f"--collection-group={collection}",
                        f"--database={self.database}",
                        "--query-scope=COLLECTION",
                        *[
                            f"--field-config=field-path={name},order={direction.lower()}"
                            for name, direction in fields
                        ],
                        "--async",
                    ]
                )

    def storage(self):
        bucket = self.run(["storage", "buckets", "describe", f"gs://{self.bucket}"], read=True)
        existing = bucket.get("lifecycle_config", bucket.get("lifecycle", {}))
        rules = list(existing.get("rule", []))
        for age, prefix in [
            (1, "jobs/chat_test/"),
            (7, "jobs/corpus_parse/"),
            (7, "jobs/corpus_import/"),
            (7, "jobs/corpus_delete/"),
        ]:
            rule = {
                "action": {"type": "Delete"},
                "condition": {"age": age, "matchesPrefix": [prefix]},
            }
            if rule not in rules:
                rules.append(rule)
        if rules != existing.get("rule", []):
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
                json.dump({"rule": rules}, handle)
                handle.flush()
                self.run(
                    [
                        "storage",
                        "buckets",
                        "update",
                        f"gs://{self.bucket}",
                        f"--lifecycle-file={handle.name}",
                    ]
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", default=json.loads((ROOT / ".firebaserc").read_text())["projects"]["default"]
    )
    parser.add_argument("--environment", choices=["production", "preview"], required=True)
    parser.add_argument("--target-url")
    parser.add_argument("--write-env", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    setup = WorkerSetup(
        args.project, args.environment, apply=args.apply, target_url=args.target_url
    )
    setup.configure()
    settings = setup.settings()
    if args.write_env:
        args.write_env.write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
    print(
        json.dumps(
            {"environment": args.environment, "applied": args.apply, "settings": settings},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
