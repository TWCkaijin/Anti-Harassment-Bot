"""Check both self-contained Functions source packages locally, without deploying.

Only allowlisted runtime source files are hashed. Credential contents are never
read. This inventory checks configured exclusions, not a Firebase upload archive.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path

EXPECTED = {"api": "client", "admin": "admin"}
FORBIDDEN_PARTS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "tests",
    "scripts",
    "resource",
    "data",
    "docs",
    "frontend",
    "firebase_admin.json",
    "password_cli.py",
    "worker_cli.py",
}


def excluded(relative: str, patterns: list[str]) -> bool:
    parts = relative.split("/")
    prefixes = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    # Firebase's globstar may match zero directories, unlike Python fnmatch.
    return any(
        fnmatch.fnmatchcase(prefix, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(prefix, pattern[3:]))
        or (pattern.endswith("/**") and fnmatch.fnmatchcase(prefix, pattern[:-3]))
        for prefix in prefixes
        for pattern in patterns
    )


def inventory(root: Path, entry: dict) -> dict:
    service = EXPECTED[entry["codebase"]]
    expected_source = f"build/functions/{service}"
    if entry["source"] != expected_source:
        raise ValueError(f"Unexpected {service} source: {entry['source']}")
    source = root / expected_source
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f"Missing or symbolic package: {expected_source}; run build_functions.py")
    patterns = entry.get("ignore", [])
    for candidate in ["venv", "__pycache__/entry.pyc", ".env", ".env.preview", "debug.log"]:
        if not excluded(candidate, patterns):
            raise ValueError(f"{service}: runtime/discovery path is not excluded: {candidate}")
    allowed_roots = {"main.py", "requirements.txt", f"{service}_app", "rag_core"}
    files = []
    for directory, dirs, names in os.walk(source, followlinks=False):
        base = Path(directory)
        for name in list(dirs):
            path = base / name
            relative = path.relative_to(source).as_posix()
            if excluded(relative, patterns):
                dirs.remove(name)
                continue
            if path.is_symlink():
                raise ValueError(f"{service}: symbolic directory: {relative}")
            if any(part in FORBIDDEN_PARTS for part in path.relative_to(source).parts):
                raise ValueError(f"{service}: unrelated directory included: {relative}")
            if relative.split("/")[0] not in allowed_roots:
                raise ValueError(f"{service}: unexpected package root: {relative}")
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(source).as_posix()
            if excluded(relative, patterns):
                continue
            parts = path.relative_to(source).parts
            if path.is_symlink():
                raise ValueError(f"{service}: symbolic file: {relative}")
            if (
                parts[0] not in allowed_roots
                or any(part in FORBIDDEN_PARTS for part in parts)
                or name.startswith((".env", "gha-creds-"))
                or path.suffix in {".key", ".pem", ".p12", ".pyc", ".log"}
            ):
                raise ValueError(f"{service}: sensitive or unrelated file included: {relative}")
            files.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    names = {row["path"] for row in files}
    required = {
        "main.py",
        "requirements.txt",
        f"{service}_app/__init__.py",
        "rag_core/__init__.py",
        "rag_core/core/config.py",
    }
    if not required <= names:
        raise ValueError(f"{service}: missing runtime files: {sorted(required - names)}")
    if not any(p.startswith("rag_core/skills/") and p.endswith("/SKILL.md") for p in names):
        raise ValueError(f"{service}: built-in Skills missing")
    return {"service": service, "source": expected_source, "files": files}


def check(root: Path) -> dict:
    config = json.loads((root / "firebase.json").read_text())
    entries = config.get("functions", [])
    if len(entries) != 2 or {item["codebase"] for item in entries} != set(EXPECTED):
        raise ValueError("Expected independent api and admin Functions codebases")
    preview = json.loads((root / "firebase.preview.json").read_text())
    if preview.get("functions") != entries:
        raise ValueError("Production and preview Functions source definitions differ")
    packages = [inventory(root, entry) for entry in entries]
    return {
        "status": "passed",
        "scope": "Local configured-source inventory; not an upload or deployment test.",
        "credential_contents_read": False,
        "packages": packages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--output", type=Path, help="Optional detailed JSON inventory outside function sources"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        result = check(root)
        if args.output:
            output = args.output.resolve()
            if output.is_relative_to(root / "build" / "functions"):
                raise ValueError("Inventory output must be outside function sources")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error)) from error
    print(
        json.dumps(
            {
                "status": "passed",
                "credential_contents_read": False,
                "packages": [
                    {"service": p["service"], "included_files": len(p["files"])}
                    for p in result["packages"]
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
