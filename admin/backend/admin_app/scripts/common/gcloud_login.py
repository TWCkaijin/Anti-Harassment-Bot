"""Opt-in, in-memory gcloud user credentials for maintenance commands."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from google.oauth2.credentials import Credentials


def get_gcloud_login_credentials(project: str) -> Credentials:
    """Return a short-lived access token without printing or saving its value."""
    executable = shutil.which("gcloud") or next(
        (
            path
            for path in ("/opt/homebrew/bin/gcloud", "/usr/local/bin/gcloud")
            if Path(path).exists()
        ),
        None,
    )
    if executable is None:
        raise RuntimeError("gcloud_login_token_unavailable")
    try:
        token = subprocess.run(
            [executable, "auth", "print-access-token", "--quiet"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            env={**os.environ, "PATH": "/opt/homebrew/bin:/usr/bin:/bin"},
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("gcloud_login_token_unavailable") from None
    if not token:
        raise RuntimeError("gcloud_login_token_unavailable")
    return Credentials(token, quota_project_id=project)
