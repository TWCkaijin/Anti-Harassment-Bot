"""Expose rag_core.core.project to the standalone operator scripts.

The scripts run as ``python scripts/<name>.py`` without the application's
PYTHONPATH, so this adds shared/python once and re-exports the helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[1] / "shared" / "python"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from rag_core.core.project import (  # noqa: E402
    admin_bucket,
    admin_site,
    client_site,
    hosting_origins,
    preview_origin_regex,
    project_id,
    read_project_env,
    service_account,
)

__all__ = [
    "admin_bucket",
    "admin_site",
    "client_site",
    "hosting_origins",
    "preview_origin_regex",
    "project_id",
    "read_project_env",
    "service_account",
]
