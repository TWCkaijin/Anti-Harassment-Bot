import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["OPENROUTER_API_KEY"] = "fixture-not-a-real-key"
os.environ["ENVIRONMENT"] = "ci"

import pytest

from admin_app import auth, infrastructure, jobs
from admin_app.config import AdminSettings

from .fakes import FakeDB, FakeStore


@pytest.fixture
def services(monkeypatch):
    db, store = FakeDB(), FakeStore()
    settings = AdminSettings(
        environment="ci",
        admin_cookie_secure=False,
        admin_origins=["http://localhost:5174"],
        admin_login_limit=30,
        admin_login_global_limit=1000,
    )
    for module in (auth, jobs):
        monkeypatch.setattr(module, "get_admin_settings", lambda: settings)
    monkeypatch.setattr(infrastructure, "get_admin_settings", lambda: settings)
    monkeypatch.setattr(infrastructure, "private_db", lambda: db)
    monkeypatch.setattr(infrastructure, "atomic", db.atomic)
    monkeypatch.setattr(infrastructure, "object_store", lambda: store)
    dispatched = []
    monkeypatch.setattr(
        jobs,
        "enqueue_job",
        lambda job: dispatched.append((job["job_id"], job["dispatch_generation"])),
    )
    return db, store, settings, dispatched


@pytest.fixture
def app(services, monkeypatch):
    import admin_app.main as main

    monkeypatch.setattr(main, "get_admin_settings", lambda: services[2])
    result = main.create_app(include_corpus=False)
    result.config.update(TESTING=True)
    return result


@pytest.fixture
def signed_in(app, services):
    from admin_app.password_cli import configure_password

    configure_password("a-long-test-password")
    client = app.test_client()
    response = client.post(
        "/api/v1/admin/auth/login",
        json={"password": "a-long-test-password"},
        headers={"Origin": "http://localhost:5174"},
    )
    assert response.status_code == 200
    headers = {"Origin": "http://localhost:5174", "X-CSRF-Token": response.json["csrf_token"]}
    return client, headers
