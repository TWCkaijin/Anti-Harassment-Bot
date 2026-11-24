"""The deployment's Google Cloud project and the names derived from it.

Standard library only: deployment and provisioning scripts import this module
without loading the application settings. Resolution order is the
``FIREBASE_PROJECT_ID`` environment variable, then ``project.env`` at the repository
root (also shipped inside each Functions package), then ``GOOGLE_CLOUD_PROJECT``.
The generic Google variable comes last because tools and emulators set it for
their own purposes (the test suite points it at a demo project).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ENV_FILE = "project.env"
_PROJECT_ID = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
_HOSTING_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class ProjectConfigError(RuntimeError):
    """The project ID is missing or malformed."""


def _project_env_file() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / PROJECT_ENV_FILE
        if candidate.is_file():
            return candidate
    return None


def read_project_env(path: Path) -> dict[str, str]:
    """Parse the plain KEY=VALUE file shared with shell and GitHub Actions."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise ProjectConfigError(f"Invalid line in {path.name}: {line!r}")
        values[key.strip()] = value.strip()
    return values


def project_id() -> str:
    value = os.environ.get("FIREBASE_PROJECT_ID")
    if not value:
        path = _project_env_file()
        value = read_project_env(path).get("FIREBASE_PROJECT_ID") if path else None
    value = value or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not value:
        raise ProjectConfigError(
            "Set FIREBASE_PROJECT_ID or add it to project.env at the repository root"
        )
    if not _PROJECT_ID.fullmatch(value):
        raise ProjectConfigError(f"Invalid Google Cloud project ID: {value!r}")
    return value


def client_site(project: str) -> str:
    return project


def admin_site(project: str) -> str:
    return f"{project}-admin"


def hosting_origins(site: str) -> list[str]:
    return [f"https://{site}.web.app", f"https://{site}.firebaseapp.com"]


def preview_origin_regex(site: str, channel: str = "dev-preview") -> str:
    """Anchored origin pattern for a Hosting preview channel of ``site``.

    Names are restricted to lowercase letters, digits and hyphens, so they are
    inserted literally; the result keeps the shape config.py accepts.
    """
    for name in (site, channel):
        if not _HOSTING_NAME.fullmatch(name):
            raise ProjectConfigError(f"Invalid Hosting name: {name!r}")
    return rf"\Ahttps://{site}--{channel}-[a-z0-9-]+\.web\.app\Z"


def admin_bucket(project: str, environment: str) -> str:
    suffix = "prod" if environment == "production" else "dev"
    return f"{project}-admin-{suffix}"


def service_account(name: str, project: str) -> str:
    return f"{name}@{project}.iam.gserviceaccount.com"
