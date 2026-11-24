"""The project ID has one source (project.env) and every derived name follows it."""

import json
from pathlib import Path

import pytest

from rag_core.core import project
from rag_core.core.config import DEFAULT_CORS_ORIGINS, DEFAULT_CORS_PREVIEW_ORIGIN_REGEXES, Settings
from scripts import sync_project_config

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    return monkeypatch


def test_project_env_is_the_default_source(clean_env):
    assert project.read_project_env(ROOT / "project.env") == {
        "FIREBASE_PROJECT_ID": "anti-harassment-bot"
    }
    assert project.project_id() == "anti-harassment-bot"


def test_firebase_project_id_overrides_the_file_but_google_cloud_project_does_not(clean_env):
    clean_env.setenv("GOOGLE_CLOUD_PROJECT", "demo-emulator-project")
    assert project.project_id() == "anti-harassment-bot"
    clean_env.setenv("FIREBASE_PROJECT_ID", "other-project-42")
    assert project.project_id() == "other-project-42"


def test_google_cloud_project_is_the_last_fallback(clean_env, monkeypatch):
    monkeypatch.setattr(project, "_project_env_file", lambda: None)
    clean_env.setenv("GOOGLE_CLOUD_PROJECT", "fallback-project")
    assert project.project_id() == "fallback-project"


def test_missing_or_malformed_project_is_an_error(clean_env, monkeypatch, tmp_path):
    monkeypatch.setattr(project, "_project_env_file", lambda: None)
    with pytest.raises(project.ProjectConfigError, match="FIREBASE_PROJECT_ID"):
        project.project_id()
    clean_env.setenv("FIREBASE_PROJECT_ID", "Bad_Project")
    with pytest.raises(project.ProjectConfigError, match="Invalid"):
        project.project_id()
    broken = tmp_path / "project.env"
    broken.write_text("FIREBASE_PROJECT_ID\n")
    with pytest.raises(project.ProjectConfigError, match="Invalid line"):
        project.read_project_env(broken)


def test_derived_names():
    p = "other-project-42"
    assert project.client_site(p) == p
    assert project.admin_site(p) == "other-project-42-admin"
    assert project.hosting_origins("site-a") == [
        "https://site-a.web.app",
        "https://site-a.firebaseapp.com",
    ]
    assert project.admin_bucket(p, "production") == "other-project-42-admin-prod"
    assert project.admin_bucket(p, "development") == "other-project-42-admin-dev"
    assert (
        project.service_account("rag-client-dev", p)
        == "rag-client-dev@other-project-42.iam.gserviceaccount.com"
    )
    with pytest.raises(project.ProjectConfigError):
        project.preview_origin_regex("bad.site")


def test_default_cors_is_unchanged_and_passes_the_safety_check():
    assert DEFAULT_CORS_ORIGINS == (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://anti-harassment-bot.web.app",
        "https://anti-harassment-bot.firebaseapp.com",
    )
    assert DEFAULT_CORS_PREVIEW_ORIGIN_REGEXES == (
        r"\Ahttps://anti-harassment-bot--dev-preview-[a-z0-9-]+\.web\.app\Z",
    )
    # The validator rejects anything that is not an anchored Hosting preview shape.
    assert Settings(cors_preview_origin_regexes=[project.preview_origin_regex("other-project-42")])


def test_firebaserc_matches_project_env(clean_env):
    current = json.loads((ROOT / ".firebaserc").read_text())
    assert current == json.loads(sync_project_config.expected_firebaserc(project.project_id()))
    assert sync_project_config.main(["--check"]) == 0


def test_hosting_configs_name_targets_not_sites():
    for name in ("firebase.json", "firebase.preview.json", "firebase.main-preview.json"):
        hosting = json.loads((ROOT / name).read_text())["hosting"]
        for entry in hosting if isinstance(hosting, list) else [hosting]:
            assert "site" not in entry and "target" in entry, name
