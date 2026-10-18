"""Local launcher isolation and cleanup regressions; no cloud/model calls."""

import importlib.util
import signal
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

spec = importlib.util.spec_from_file_location(
    "dev_launcher", Path(__file__).resolve().parents[2] / "scripts/dev.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_environment_overrides_production_and_disables_cloud_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "wrong-project")
    monkeypatch.setenv("ADMIN_TASKS_QUEUE", "production-queue")
    monkeypatch.setenv("K_SERVICE", "deployed")
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    env = launcher.environment(tmp_path / "config.env")
    assert env["ENVIRONMENT"] == "development"
    assert env["FIRESTORE_DATABASE_ID"] == "app-dev"
    assert env["ADMIN_PRIVATE_DATABASE_ID"] == "admin-private-dev"
    assert env["ADMIN_TASKS_QUEUE"] == ""
    assert env["ADMIN_LOCAL_JOBS"] == "true"
    assert env["VITE_API_BASE_URL"] == "/api"
    assert "K_SERVICE" not in env
    assert "FIRESTORE_EMULATOR_HOST" not in env


def test_busy_port_does_not_kill_existing_process():
    sock = Mock()
    sock.bind.side_effect = OSError("already in use")
    with (
        patch.object(launcher.socket, "socket") as factory,
        patch.object(launcher.os, "killpg") as kill,
    ):
        factory.return_value.__enter__.return_value = sock
        with pytest.raises(RuntimeError, match="8000"):
            launcher.check_ports()
    kill.assert_not_called()


def test_cleanup_signals_entire_group_even_if_parent_has_exited():
    child = Mock(pid=43210)
    child.wait.side_effect = [subprocess.TimeoutExpired("child", 1), 0]
    with patch.object(launcher.os, "killpg") as kill:
        launcher.stop_all([child])
    assert kill.call_args_list[0].args == (43210, signal.SIGTERM)
    assert kill.call_args_list[1].args == (43210, signal.SIGKILL)


def test_child_failure_during_readiness_is_reported():
    processes = [Mock(returncode=1), Mock(), Mock(), Mock()]
    processes[0].poll.return_value = 1
    with pytest.raises(RuntimeError, match="client-api exited"):
        launcher.wait_ready(processes)


def test_missing_env_file_fails_before_launch(tmp_path):
    with pytest.raises(RuntimeError, match="No env file"):
        launcher.preflight(tmp_path / "missing.env")
