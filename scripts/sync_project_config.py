"""Generate .firebaserc from project.env, or check that it is in sync.

Firebase CLI reads project and Hosting target names only from JSON, so this
script derives them from the single project ID instead of editing them by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from scripts.project_config import admin_site, client_site, project_id
except ModuleNotFoundError:  # pragma: no cover
    from project_config import admin_site, client_site, project_id

ROOT = Path(__file__).resolve().parents[1]
FIREBASERC = ROOT / ".firebaserc"


def expected_firebaserc(project: str) -> str:
    config = {
        "projects": {"default": project},
        "targets": {
            project: {"hosting": {"client": [client_site(project)], "admin": [admin_site(project)]}}
        },
    }
    return json.dumps(config, indent=2) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail when .firebaserc is stale")
    mode.add_argument("--write", action="store_true", help="Rewrite .firebaserc")
    args = parser.parse_args(argv)
    expected = expected_firebaserc(project_id())
    current = FIREBASERC.read_text() if FIREBASERC.is_file() else ""
    if args.write:
        FIREBASERC.write_text(expected)
        print(f"Wrote {FIREBASERC.name}")
        return 0
    if json.loads(current or "null") != json.loads(expected):
        print(f"{FIREBASERC.name} does not match project.env; run with --write", file=sys.stderr)
        return 1
    print(f"{FIREBASERC.name} matches project.env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
