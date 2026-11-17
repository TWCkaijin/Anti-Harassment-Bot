"""The gcloud option is explicit and these tests never contact cloud services."""

import json
import sys
from types import SimpleNamespace

import pytest

from admin_app.scripts.common.gcloud_login import get_gcloud_login_credentials
from admin_app.scripts.common.legal_corpus import digest, plan_import
from admin_app.scripts.ingest import official_laws


def test_gcloud_token_stays_in_memory_and_uses_quiet_mode(monkeypatch, capsys):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="synthetic-access-token\n")

    monkeypatch.setattr("shutil.which", lambda name: "/fake/gcloud")
    monkeypatch.setattr("subprocess.run", fake_run)
    credentials = get_gcloud_login_credentials("test-project")
    assert credentials.token == "synthetic-access-token"
    assert credentials.quota_project_id == "test-project"
    assert calls[0][0][-3:] == ["auth", "print-access-token", "--quiet"]
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["env"]["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"
    assert capsys.readouterr() == ("", "")


def test_explicit_credentials_bind_selected_project_and_database(monkeypatch):
    calls = []
    credentials = object()

    def fake_client(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(project=kwargs["project"], _database=kwargs["database"])

    monkeypatch.setattr("google.cloud.firestore.Client", fake_client)
    result = official_laws.firestore_client("test-project", "app-dev", credentials=credentials)
    assert result.project == "test-project"
    assert calls == [{"project": "test-project", "database": "app-dev", "credentials": credentials}]


def test_default_client_still_uses_deployment_bound_factory(monkeypatch):
    db = SimpleNamespace(project="test-project", _database="app-dev")
    calls = []
    monkeypatch.setattr(
        "rag_core.firestore_clients.get_business_db",
        lambda: calls.append("business_factory") or db,
    )
    assert official_laws.firestore_client("test-project", "app-dev") is db
    assert calls == ["business_factory"]
    with pytest.raises(ValueError, match="project/database"):
        official_laws.firestore_client("test-project", "(default)")


def test_snapshot_flag_passes_gcloud_credentials_without_network(tmp_path, monkeypatch):
    credentials = object()
    calls = []
    db = SimpleNamespace(
        project="test-project",
        _database="app-dev",
        collection=lambda name: SimpleNamespace(stream=lambda: []),
    )
    monkeypatch.setattr(official_laws, "get_gcloud_login_credentials", lambda project: credentials)

    def fake_client(project, database, *, credentials=None):
        calls.append((project, database, credentials))
        return db

    monkeypatch.setattr(official_laws, "firestore_client", fake_client)
    output = tmp_path / "law-snapshot.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "official_laws",
            "snapshot",
            "--project",
            "test-project",
            "--database",
            "app-dev",
            "--output",
            str(output),
            "--use-gcloud-login",
        ],
    )
    official_laws.main()
    assert calls == [("test-project", "app-dev", credentials)]
    assert json.loads(output.read_text())["documents"] == []
    assert output.stat().st_mode & 0o777 == 0o600


def test_apply_flag_passes_gcloud_credentials_without_embedding_or_write(tmp_path, monkeypatch):
    credentials = object()
    calls = []
    plan = plan_import([], [], "rag_documents", "test-project", "app-dev")
    plan["snapshot_status"] = "cloud_snapshot"
    plan["plan_sha256"] = digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))

    monkeypatch.setattr(official_laws, "get_gcloud_login_credentials", lambda project: credentials)
    monkeypatch.setattr(
        official_laws,
        "firestore_client",
        lambda project, database, *, credentials=None: (
            calls.append((project, database, credentials))
            or SimpleNamespace(project=project, _database=database)
        ),
    )

    async def fake_apply(plan, *, db, embed, backup_path):
        calls.append(("apply_plan", db.project, str(backup_path)))
        return "synthetic-batch"

    monkeypatch.setattr(official_laws, "apply_plan", fake_apply)
    monkeypatch.setattr(
        "rag_core.rag.embeddings.EmbeddingClient",
        lambda: SimpleNamespace(
            embed=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("embedding must not run in CLI wiring test")
            )
        ),
    )
    backup = tmp_path / "backup.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "official_laws",
            "apply",
            "--project",
            "test-project",
            "--database",
            "app-dev",
            "--plan",
            str(path),
            "--expected-plan-sha256",
            plan["plan_sha256"],
            "--backup",
            str(backup),
            "--use-gcloud-login",
        ],
    )
    official_laws.main()
    assert calls == [
        ("test-project", "app-dev", credentials),
        ("apply_plan", "test-project", str(backup)),
    ]
    assert not backup.exists()


@pytest.mark.parametrize("apply", [False, True])
def test_rollback_flag_passes_gcloud_credentials_without_write(tmp_path, monkeypatch, apply):
    credentials = object()
    calls = []
    backup = tmp_path / "backup.jsonl"
    backup.write_text(
        json.dumps({"kind": "header", "project": "test-project", "database": "app-dev"}) + "\n"
    )
    monkeypatch.setattr(official_laws, "get_gcloud_login_credentials", lambda project: credentials)
    monkeypatch.setattr(
        official_laws,
        "firestore_client",
        lambda project, database, *, credentials=None: (
            calls.append((project, database, credentials))
            or SimpleNamespace(project=project, _database=database)
        ),
    )
    monkeypatch.setattr(
        official_laws,
        "rollback",
        lambda records, db, *, apply: calls.append(("rollback", db.project, apply)) or {},
    )
    argv = [
        "official_laws",
        "rollback",
        "--project",
        "test-project",
        "--database",
        "app-dev",
        "--backup",
        str(backup),
        "--use-gcloud-login",
    ]
    if apply:
        argv.append("--apply")
    monkeypatch.setattr(sys, "argv", argv)
    official_laws.main()
    assert calls == [
        ("test-project", "app-dev", credentials),
        ("rollback", "test-project", apply),
    ]
