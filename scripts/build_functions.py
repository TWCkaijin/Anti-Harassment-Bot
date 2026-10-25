"""Build self-contained Firebase sources from the two apps and shared runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    ".env",
    "firebase_admin.json",
    "node_modules",
    "__pycache__",
    "tests",
    ".git",
    ".venv",
    "venv",
}


def uv_executable() -> str:
    local_uv = Path.home() / ".local" / "bin" / "uv"
    return shutil.which("uv") or (str(local_uv) if local_uv.is_file() else "uv")


def copy_package(source: Path, target: Path) -> None:
    def ignore(_directory, names):
        return [
            name
            for name in names
            if name in FORBIDDEN or name.startswith(".env.") or name.endswith((".pyc", ".log"))
        ]

    shutil.copytree(source, target, ignore=ignore)


def build(service: str) -> Path:
    target = ROOT / "build" / "functions" / service
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copy2(ROOT / service / "backend" / "main.py", target / "main.py")
    copy_package(ROOT / service / "backend" / f"{service}_app", target / f"{service}_app")
    copy_package(ROOT / "shared" / "python" / "rag_core", target / "rag_core")
    # Operational ingestion CLIs never need to ship in the serving application.
    if (target / "admin_app" / "scripts").exists():
        shutil.rmtree(target / "admin_app" / "scripts")
    for local_cli in ("password_cli.py", "worker_cli.py"):
        (target / "admin_app" / local_cli).unlink(missing_ok=True)
    command = [
        uv_executable(),
        "export",
        "--frozen",
        "--no-default-groups",
        "--no-emit-project",
        "--format",
        "requirements-txt",
    ]
    if service == "admin":
        command += ["--extra", "admin"]
    exported = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=True)
    (target / "requirements.txt").write_text(exported.stdout)
    forbidden = [
        str(p.relative_to(target))
        for p in target.rglob("*")
        if p.is_symlink() or p.name in FORBIDDEN or p.suffix in {".key", ".pem"}
    ]
    if forbidden:
        raise RuntimeError(f"Forbidden deployment files: {forbidden}")
    if not list((target / "rag_core" / "skills").rglob("SKILL.md")):
        raise RuntimeError("Built-in Skills are missing")
    if service == "client" and (target / "admin_app").exists():
        raise RuntimeError("Administrator code is present in the client package")
    return target


def verify_isolated_import(target: Path, service: str) -> None:
    """Load each app with only its exported dependencies and generated source."""
    code = """
import importlib.util
import pathlib
import sys
root = pathlib.Path(sys.argv[1]).resolve()
service = sys.argv[2]
sys.path.insert(0, str(root))
import main
app = main.app if service == 'client' else main.get_app()
routes = {rule.rule for rule in app.url_map.iter_rules()}
if service == 'client':
    assert '/api/v1/chat/' in routes
    assert not any('/admin' in rule for rule in routes)
    assert importlib.util.find_spec('admin_app') is None
    assert importlib.util.find_spec('argon2') is None
    assert importlib.util.find_spec('openpyxl') is None
else:
    assert '/api/v1/admin/auth/login' in routes
    assert not any('/v1/chat' in rule for rule in routes)
    assert importlib.util.find_spec('client_app') is None
assert app.test_client().get('/api/v1/admin/config').status_code == (404 if service == 'client' else 401)
print(service + ': isolated import and route boundary passed')
"""
    env = {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "SYSTEMROOT", "TMPDIR", "LANG", "LC_ALL"}
    }
    env.update(
        {
            "ENVIRONMENT": "ci",
            "PYTHON_DOTENV_DISABLED": "1",
            "OPENROUTER_API_KEY": "ci-placeholder-not-a-real-key",
            "GOOGLE_CLOUD_PROJECT": "demo-rag-harass-bot",
            "FIRESTORE_DATABASE_ID": "app-dev",
            "ADMIN_PRIVATE_DATABASE_ID": "admin-private-dev",
            "RUNTIME_CONFIG_DOCUMENT_ID": "app_dev",
        }
    )
    subprocess.run(
        [str(target / "venv/bin/python"), "-I", "-c", code, str(target), service],
        cwd=target,
        env=env,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=["client", "admin", "all"], default="all")
    parser.add_argument(
        "--discovery-venv",
        action="store_true",
        help="Create each Firebase CLI discovery environment",
    )
    args = parser.parse_args()
    built = []
    for service in ["client", "admin"] if args.service == "all" else [args.service]:
        target = build(service)
        if args.discovery_venv:
            uv = uv_executable()
            subprocess.run([uv, "venv", str(target / "venv"), "--python", "3.13"], check=True)
            subprocess.run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(target / "venv" / "bin" / "python"),
                    "-r",
                    str(target / "requirements.txt"),
                ],
                check=True,
            )
            verify_isolated_import(target, service)
        built.append({"service": service, "source": str(target.relative_to(ROOT))})
    print(json.dumps({"packages": built}))


if __name__ == "__main__":
    main()
