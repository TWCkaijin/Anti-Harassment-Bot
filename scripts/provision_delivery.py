"""Provision Hosting registrations and repository-scoped keyless deployment."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from provision_firebase import Provisioner

POLICY_PATH = Path(__file__).resolve().parents[1] / "infrastructure/iam/ci-deployment-policy.json"
REGION = "asia-east1"


def deploy_permissions(p, *, allow_project_hosting_admin=False):
    """Bind update/read access directly to resources, never broad admin roles."""
    policy = json.loads(POLICY_PATH.read_text())
    member = f"serviceAccount:{p.sa('rag-deploy')}"
    existing_roles = p.run(["iam", "roles", "list"], read=True)
    names = {item["name"].rsplit("/", 1)[-1] for item in existing_roles}
    for role_id, definition in policy["roles"].items():
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as handle:
            json.dump(definition, handle)
            handle.flush()
            verb = "update" if role_id in names else "create"
            p.run(["iam", "roles", verb, role_id, f"--file={handle.name}"])
    p.bind_project("rag-deploy", f"projects/{p.project}/roles/ragDeployMetadata")
    for function in policy["function_ids"]:
        p.run(
            [
                "functions",
                "add-iam-policy-binding",
                function,
                "--gen2",
                f"--region={REGION}",
                f"--member={member}",
                f"--role=projects/{p.project}/roles/ragFunctionUpdater",
            ]
        )
        if p.apply:
            deployed = p.run(
                ["functions", "describe", function, "--v2", f"--region={REGION}"], read=True
            )
            run_service = deployed["serviceConfig"]["service"].rsplit("/", 1)[-1]
        else:
            run_service = f"<serviceConfig.service of {function}>"
        p.run(
            [
                "run",
                "services",
                "add-iam-policy-binding",
                run_service,
                f"--region={REGION}",
                f"--member={member}",
                f"--role=projects/{p.project}/roles/ragRunMetadata",
            ]
        )
    for secret in policy["secret_ids"]:
        p.run(
            [
                "secrets",
                "add-iam-policy-binding",
                secret,
                f"--member={member}",
                "--role=roles/secretmanager.viewer",
            ]
        )
    for identity in policy["runtime_service_accounts"]:
        p.run(
            [
                "iam",
                "service-accounts",
                "add-iam-policy-binding",
                p.sa(identity),
                f"--member={member}",
                "--role=roles/iam.serviceAccountUser",
            ]
        )
    p.run(
        [
            "artifacts",
            "repositories",
            "add-iam-policy-binding",
            "gcf-artifacts",
            f"--location={REGION}",
            f"--member={member}",
            f"--role=projects/{p.project}/roles/ragArtifactMetadata",
        ]
    )
    if allow_project_hosting_admin:
        p.bind_project("rag-deploy", "roles/firebasehosting.admin")
    else:
        print(
            json.dumps(
                {
                    "unresolved": policy["unresolved_hosting_grant"],
                    "hosting_ci_enabled": False,
                    "function_policy": str(POLICY_PATH),
                }
            )
        )


def firebase(project, *args):
    result = subprocess.run(
        ["firebase", *args, "--project", project, "--json", "--non-interactive"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)["result"]


def hosting(p):
    sites = firebase(p.project, "hosting:sites:list")
    site_list = sites.get("sites", []) if isinstance(sites, dict) else sites
    if p.apply and not any(item["name"].endswith(f"/{p.project}-admin") for item in site_list):
        firebase(p.project, "hosting:sites:create", f"{p.project}-admin")
    apps = firebase(p.project, "apps:list", "WEB")
    app_list = apps.get("apps", []) if isinstance(apps, dict) else apps
    names = {app.get("displayName") for app in app_list}
    for name in ["RAG Client Preview", "RAG Admin Production", "RAG Admin Preview"]:
        if name not in names and p.apply:
            firebase(p.project, "apps:create", "WEB", name)
    print(json.dumps({"hosting_site": f"{p.project}-admin", "applied": p.apply}))


def federation(p, repository, *, allow_project_hosting_admin=False):
    repo = json.loads(
        subprocess.check_output(
            ["gh", "api", f"repos/{repository}"],
            text=True,
        )
    )
    repository_id = str(repo["id"])
    owner_id = str(repo["owner"]["id"])
    repository = repo["full_name"]
    project = p.run(["projects", "describe", p.project], read=True)
    number = project["projectNumber"]
    pool = "rag-github"
    pools = p.run(["iam", "workload-identity-pools", "list", "--location=global"], read=True)
    if not any(item["name"].endswith("/" + pool) for item in pools):
        p.run(
            [
                "iam",
                "workload-identity-pools",
                "create",
                pool,
                "--location=global",
                "--display-name=RAG GitHub deployment",
            ]
        )
    providers = (
        p.run(
            [
                "iam",
                "workload-identity-pools",
                "providers",
                "list",
                "--location=global",
                f"--workload-identity-pool={pool}",
            ],
            read=True,
        )
        if p.apply
        else []
    )
    condition = (
        f"assertion.repository_id == '{repository_id}' && "
        f"assertion.repository_owner_id == '{owner_id}' && assertion.event_name == 'push' && ("
        f"(assertion.ref == 'refs/heads/main' && assertion.workflow_ref == '{repository}/.github/workflows/deploy-main.yml@refs/heads/main') || "
        f"(assertion.ref == 'refs/heads/dev' && assertion.workflow_ref == '{repository}/.github/workflows/deploy-dev.yml@refs/heads/dev'))"
    )
    operation = (
        "update-oidc"
        if any(item["name"].endswith("/github") for item in providers)
        else "create-oidc"
    )
    p.run(
        [
            "iam",
            "workload-identity-pools",
            "providers",
            operation,
            "github",
            "--location=global",
            f"--workload-identity-pool={pool}",
            "--issuer-uri=https://token.actions.githubusercontent.com",
            "--attribute-mapping=google.subject=assertion.sub,attribute.repository_id=assertion.repository_id",
            f"--attribute-condition={condition}",
        ]
    )
    p.run(
        [
            "iam",
            "service-accounts",
            "add-iam-policy-binding",
            p.sa("rag-deploy"),
            "--role=roles/iam.workloadIdentityUser",
            f"--member=principalSet://iam.googleapis.com/projects/{number}/locations/global/workloadIdentityPools/{pool}/attribute.repository_id/{repository_id}",
        ]
    )
    deploy_permissions(p, allow_project_hosting_admin=allow_project_hosting_admin)
    provider = f"projects/{number}/locations/global/workloadIdentityPools/{pool}/providers/github"
    if p.apply:
        for name, value in {
            "WORKLOAD_IDENTITY_PROVIDER": provider,
            "DEPLOY_SERVICE_ACCOUNT": p.sa("rag-deploy"),
            "FIREBASE_PROJECT_ID": p.project,
        }.items():
            subprocess.run(
                ["gh", "variable", "set", name, "--repo", repository, "--body", value], check=True
            )
    print(json.dumps({"provider": provider, "repository_id": repository_id, "applied": p.apply}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="anti-harassment-bot")
    parser.add_argument("--repository", default="TWCkaijin/Anti-Harassment-Bot")
    parser.add_argument("--phase", choices=["hosting", "federation"], required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--allow-project-hosting-admin",
        action="store_true",
        help="Explicitly include the unresolved project-wide Hosting Admin grant after approval.",
    )
    args = parser.parse_args()
    p = Provisioner(args.project, args.apply)
    if args.phase == "hosting":
        hosting(p)
    else:
        federation(p, args.repository, allow_project_hosting_admin=args.allow_project_hosting_admin)


if __name__ == "__main__":
    main()
