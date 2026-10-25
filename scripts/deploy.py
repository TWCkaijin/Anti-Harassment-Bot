"""Build and deploy one environment, backend before frontend, using CLI login or ADC.

Infrastructure and credentials are provisioned separately. This command never
copies a developer .env or service-account JSON into a deployment source.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from scripts.build_functions import ROOT
except ImportError:
    from build_functions import ROOT

REGION = "asia-east1"
FIREBASE_CLI_VERSION = "15.33.0"


def verify_firebase_cli():
    executable = shutil.which("firebase")
    if not executable:
        raise RuntimeError("Firebase CLI is missing from PATH")
    output = run([executable, "--version"], capture=True)
    versions = re.findall(r"(?m)^\s*(\d+\.\d+\.\d+)\s*$", output)
    if versions != [FIREBASE_CLI_VERSION]:
        raise RuntimeError(
            f"Firebase CLI {FIREBASE_CLI_VERSION} is required; PATH selected {executable}"
        )
    print(f"Firebase CLI {FIREBASE_CLI_VERSION}: {executable}", flush=True)


def run(args, *, env=None, capture=False):
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=capture, check=False)
    if result.returncode:
        if capture:
            # Firebase JSON errors are operational; do not dump arbitrary SDK
            # metadata or a credential-bearing subprocess environment.
            # Never print arbitrary Firebase error objects: nested metadata may
            # include environment variables. Extract known permission names only.
            permissions = sorted(
                set(
                    re.findall(
                        r"\b(?:iam\.serviceAccounts\.actAs|cloudfunctions\.functions\.update|"
                        r"secretmanager\.versions\.access|artifactregistry\.repositories\.uploadArtifacts|"
                        r"storage\.objects\.get)\b",
                        result.stdout + result.stderr,
                    )
                )
            )
            detail = f"; permissions referenced: {', '.join(permissions)}" if permissions else ""
            raise RuntimeError(
                f"{args[0:2]} exited {result.returncode}{detail}. "
                "Inspect Cloud Audit Logs for this deployment time and principal; "
                "verify both runtime and build service-account actAs grants."
            )
        raise RuntimeError(f"{args[0:2]} exited {result.returncode}")
    return result.stdout


def firebase(project, *args, env=None):
    value = run(
        ["firebase", *args, "--project", project, "--json", "--non-interactive"],
        env=env,
        capture=True,
    )
    return json.loads(value)["result"]


def function_info(project, function, *, include_build=False):
    result = subprocess.run(
        [
            "gcloud",
            "functions",
            "describe",
            function,
            "--v2",
            f"--region={REGION}",
            f"--project={project}",
            "--format=json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        if "not found" in result.stderr.lower() or "NOT_FOUND" in result.stderr:
            return None
        raise RuntimeError(f"Cannot inspect function {function}")
    value = json.loads(result.stdout)
    return value if include_build else value["serviceConfig"]


def verify_build_account(project, environment, function):
    value = function_info(project, function, include_build=True)
    suffix = "prod" if environment == "production" else "dev"
    expected = (
        f"projects/{project}/serviceAccounts/rag-build-{suffix}@{project}.iam.gserviceaccount.com"
    )
    if value is None or value.get("buildConfig", {}).get("serviceAccount") != expected:
        raise RuntimeError(
            f"{function}: dedicated build account is not configured. An infrastructure operator "
            f"must run scripts/provision_build.py --project {project} "
            f"--environment {environment} --apply before deploying."
        )


def environment_values(project, environment, service, worker_url=""):
    production = environment == "production"
    suffix = "prod" if production else "dev"
    values = {
        "ENVIRONMENT": environment,
        "FIRESTORE_DATABASE_ID": "(default)" if production else "app-dev",
        "RUNTIME_CONFIG_DOCUMENT_ID": "app_main" if production else "app_dev",
        "EMBEDDING_MODEL": "intfloat/multilingual-e5-large",
        "EMBEDDING_PROVIDER": "openrouter",
        "EMBEDDING_DIMENSIONS": "1024",
        "CLARIFICATION_LEGACY_VALID_UNTIL": "2026-10-11T20:00:00+00:00",
    }
    if service == "admin":
        values.update(
            {
                "ADMIN_PRIVATE_DATABASE_ID": "admin-private" if production else "admin-private-dev",
                "ADMIN_STORAGE_BUCKET": f"{project}-admin-{suffix}",
                "ADMIN_HOSTING_SITE": f"{project}-admin",
                "ADMIN_ORIGINS": json.dumps(
                    [f"https://{project}-admin.web.app", f"https://{project}-admin.firebaseapp.com"]
                ),
                "ADMIN_COOKIE_SECURE": "true",
                "ADMIN_TASKS_QUEUE": f"rag-admin-{suffix}",
                "ADMIN_TASKS_LOCATION": REGION,
                "ADMIN_TASKS_SERVICE_ACCOUNT": f"rag-tasks-{suffix}@{project}.iam.gserviceaccount.com",
                "ADMIN_TASKS_TARGET_URL": worker_url,
                "ADMIN_TASKS_AUDIENCE": worker_url,
            }
        )
    return values


def write_env(target: Path, values):
    (target / ".env").write_text(
        "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items())
    )


def frontend_env(project, environment, service):
    production = environment == "production"
    apps = firebase(project, "apps:list", "WEB")
    apps = apps if isinstance(apps, list) else apps["apps"]
    display = {
        ("admin", True): "RAG Admin Production",
        ("admin", False): "RAG Admin Preview",
        ("client", False): "RAG Client Preview",
    }.get((service, production))
    if display:
        app = next((app for app in apps if app.get("displayName") == display), None)
    else:
        app = next((app for app in apps if not app.get("displayName", "").startswith("RAG ")), None)
    if app is None:
        raise RuntimeError(f"Missing {service} {environment} Firebase Web App registration")
    sdk = firebase(project, "apps:sdkconfig", "WEB", app["appId"])
    sdk = sdk.get("sdkConfig", sdk)
    function = "api" if production else "api_preview"
    return {
        **os.environ,
        "VITE_ENV": environment,
        "VITE_API_BASE_URL": f"https://{REGION}-{project}.cloudfunctions.net/{function}",
        "VITE_FIREBASE_API_KEY": sdk.get("apiKey", ""),
        "VITE_FIREBASE_PROJECT_ID": project,
        "VITE_FIREBASE_APP_ID": app["appId"],
        "VITE_FIREBASE_MEASUREMENT_ID": sdk.get("measurementId", ""),
        "VITE_ANALYTICS_ENABLED": os.getenv("VITE_ANALYTICS_ENABLED", "false"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["production", "preview"], required=True)
    parser.add_argument("--service", choices=["client", "admin", "all"], default="all")
    parser.add_argument("--project", default="anti-harassment-bot")
    parser.add_argument("--backend-only", action="store_true")
    parser.add_argument("--frontend-only", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "deployment.json")
    args = parser.parse_args()
    if args.backend_only and args.frontend_only:
        parser.error("Select only one partial deployment option")
    verify_firebase_cli()
    services = ["admin", "client"] if args.service == "all" else [args.service]
    config = "firebase.json" if args.environment == "production" else "firebase.preview.json"
    production = args.environment == "production"
    functions = {
        "client": "api" if production else "api_preview",
        "admin": "admin_api" if production else "admin_api_preview",
    }
    discovery_env = {
        **os.environ,
        "ENVIRONMENT": "ci",
        "FIRESTORE_DATABASE_ID": "app-dev",
        "FIREBASE_PROJECT_ID": args.project,
        "RUNTIME_CONFIG_DOCUMENT_ID": "app_dev",
        "PYTHON_DOTENV_DISABLED": "1",
        "RAG_FUNCTION_DISCOVERY": "1",
    }
    results = {
        "environment": args.environment,
        "project": args.project,
        "functions": {},
        "hosting": {},
    }
    if not args.frontend_only:
        for service in services:
            verify_build_account(args.project, args.environment, functions[service])
        run(
            [
                sys.executable,
                "scripts/build_functions.py",
                "--service",
                args.service,
                "--discovery-venv",
            ]
        )
        for service in services:
            function = functions[service]
            existing = function_info(args.project, function) if service == "admin" else None
            values = environment_values(
                args.project, args.environment, service, existing["uri"] if existing else ""
            )
            target = ROOT / "build" / "functions" / service
            write_env(target, values)
            codebase = "api" if service == "client" else "admin"
            firebase(
                args.project,
                "deploy",
                "--config",
                config,
                "--only",
                f"functions:{codebase}:{function}",
                env=discovery_env,
            )
            deployed = function_info(args.project, function)
            if service == "admin" and not existing:
                # First deploy establishes the canonical run.app URI. Only this
                # initial bootstrap needs a service configuration update.
                uri = deployed["uri"]
                run(
                    [
                        "gcloud",
                        "run",
                        "services",
                        "update",
                        deployed["service"].rsplit("/", 1)[-1],
                        f"--region={REGION}",
                        f"--project={args.project}",
                        "--quiet",
                        f"--update-env-vars=ADMIN_TASKS_TARGET_URL={uri},ADMIN_TASKS_AUDIENCE={uri}",
                    ],
                    capture=True,
                )
            results["functions"][service] = {"name": function, "uri": deployed["uri"]}
    if not args.backend_only:
        for service in services:
            run(
                ["pnpm", "--filter", f"@rag/{service}", "build", "--mode", args.environment],
                env=frontend_env(args.project, args.environment, service),
            )
        targets = ",".join(f"hosting:{service}" for service in services)
        if production:
            response = firebase(args.project, "deploy", "--config", config, "--only", targets)
        else:
            response = firebase(
                args.project,
                "hosting:channel:deploy",
                "dev-preview",
                "--config",
                config,
                "--only",
                ",".join(services),
                "--expires",
                "30d",
            )
        results["hosting"] = response
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
