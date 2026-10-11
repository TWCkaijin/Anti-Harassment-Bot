"""Run the two local APIs and two Vite servers; Ctrl-C stops their process groups."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = (
    ("client-api", 8000, "http://127.0.0.1:8000/"),
    ("admin-api", 8001, "http://127.0.0.1:8001/api/v1/admin/health"),
    ("client-web", 5173, "http://127.0.0.1:5173/"),
    ("admin-web", 5174, "http://127.0.0.1:5174/"),
)


def environment(env_file: Path):
    env = dict(os.environ)
    for key in (
        "K_SERVICE",
        "FUNCTION_TARGET",
        "FUNCTION_NAME",
        "FIRESTORE_EMULATOR_HOST",
        "PYTHON_DOTENV_DISABLED",
        "RAG_FUNCTION_DISCOVERY",
    ):
        env.pop(key, None)
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(
                str(ROOT / p) for p in ("shared/python", "client/backend", "admin/backend")
            ),
            "RAG_ENV_FILE": str(env_file),
            "FLASK_SKIP_DOTENV": "1",
            "ENVIRONMENT": "development",
            "FIREBASE_PROJECT_ID": "anti-harassment-bot",
            "GOOGLE_CLOUD_PROJECT": "anti-harassment-bot",
            "FIRESTORE_DATABASE_ID": "app-dev",
            "RUNTIME_CONFIG_DOCUMENT_ID": "app_dev",
            "ADMIN_PRIVATE_DATABASE_ID": "admin-private-dev",
            "ADMIN_COOKIE_SECURE": "false",
            "ADMIN_ORIGINS": '["http://localhost:5174","http://127.0.0.1:5174"]',
            "ADMIN_STORAGE_BUCKET": "anti-harassment-bot-admin-dev",
            "ADMIN_TASKS_QUEUE": "",
            "ADMIN_TASKS_TARGET_URL": "",
            "ADMIN_TASKS_SERVICE_ACCOUNT": "",
            "ADMIN_TASKS_AUDIENCE": "",
            "VITE_API_BASE_URL": "/api",
        }
    )
    return env


def commands():
    flask = [sys.executable, "-m", "flask", "--app"]
    return [
        [*flask, "client_app.main:create_app", "run", "--host", "127.0.0.1", "--port", "8000"],
        [*flask, "admin_app.main:create_app", "run", "--host", "127.0.0.1", "--port", "8001"],
        ["pnpm", "--filter", "@rag/client", "dev"],
        ["pnpm", "--filter", "@rag/admin", "dev"],
    ]


def check_ports():
    for name, port, _ in SERVICES:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(
                    f"Port {port} is unavailable ({name}); stop its existing owner first."
                ) from None


def preflight(env_file):
    if os.name != "posix":
        raise RuntimeError("This launcher requires macOS/Linux (POSIX process groups).")
    if not env_file.is_file():
        raise RuntimeError(
            "No env file found. Create .admin-local/development.env from .env.example and configure your key."
        )
    if importlib.util.find_spec("flask") is None:
        raise RuntimeError(
            "Install dependencies with uv sync --frozen --all-extras; run via uv run --frozen python scripts/dev.py."
        )
    try:
        subprocess.run(["pnpm", "--version"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "pnpm is unavailable; install pnpm 10.27.0 and run pnpm install --frozen-lockfile."
        ) from None
    for app in ("client", "admin"):
        if not (ROOT / app / "frontend/node_modules/.bin/vite").is_file():
            raise RuntimeError(
                "Frontend dependencies are missing; run pnpm install --frozen-lockfile."
            )
    check_ports()


def stop_all(processes):
    # Each child starts its own session. Signal that entire group, including
    # pnpm's Vite child, even when the immediate process has already exited.
    for proc in processes:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    for proc in processes:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=max(0.01, deadline - time.monotonic()))
    for proc in processes:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def wait_ready(processes, timeout=120):
    pending = dict((name, url) for name, _, url in SERVICES)
    deadline = time.monotonic() + timeout
    # Local health requests must never go through an inherited HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while pending and time.monotonic() < deadline:
        ensure_running(processes)
        for name, url in list(pending.items()):
            try:
                with opener.open(url, timeout=1) as response:
                    if response.status == 200:
                        del pending[name]
            except (OSError, urllib.error.URLError):
                pass
        if pending:
            time.sleep(0.25)
    ensure_running(processes)
    if pending:
        raise RuntimeError("Startup timed out: " + ", ".join(pending))


def ensure_running(processes):
    for (name, _, _), proc in zip(SERVICES, processes, strict=True):
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited with status {proc.returncode}; see its log.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, help="Explicit env file (relative to current directory)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check local prerequisites/ports without starting services.",
    )
    args = parser.parse_args()
    preferred = ROOT / ".admin-local/development.env"
    env_file = (args.env_file or (preferred if preferred.is_file() else ROOT / ".env")).resolve()
    processes = []
    logs = []
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        preflight(env_file)
        print(
            f"Env file: {env_file}\nDevelopment databases: app-dev / admin-private-dev", flush=True
        )
        if args.check:
            print("Preflight passed (cloud credentials and model access have not been tested).")
            return 0
        log_root = ROOT / ".admin-local/dev-runs"
        log_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_dir = Path(tempfile.mkdtemp(prefix="run-", dir=log_root))
        print(
            f"Logs: {log_dir}\nStarting four services; no background worker is started.", flush=True
        )
        env = environment(env_file)
        for (name, _, _), command in zip(SERVICES, commands(), strict=True):
            path = log_dir / (name + ".log")
            stream = os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
            logs.append(stream)
            processes.append(
                subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            )
        wait_ready(processes)
        print(
            "Ready:\n  Client http://127.0.0.1:5173\n  Admin  http://127.0.0.1:5174\nCtrl-C stops all four services.",
            flush=True,
        )
        while True:
            ensure_running(processes)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping local services...", flush=True)
        return 0
    except (RuntimeError, OSError) as error:
        print(f"Startup failed: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        # Ignore further interrupts during cleanup so children cannot be orphaned.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        stop_all(processes)
        for stream in logs:
            stream.close()
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


if __name__ == "__main__":
    raise SystemExit(main())
