"""Replay worker setup against the distinct Scheduler create/update CLI contracts."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts import configure_admin_workers as worker_setup


@pytest.mark.parametrize("environment,suffix", [("preview", "dev"), ("production", "prod")])
def test_scheduler_setup_can_create_repair_and_repeat(monkeypatch, environment, suffix):
    project = "test-worker-setup"
    url = "https://admin-worker-test.asia-east1.run.app"
    name = f"rag-admin-reconcile-{suffix}"
    jobs = {"unrelated-job": {"name": "projects/test/locations/asia-east1/jobs/unrelated-job"}}
    unrelated = deepcopy(jobs["unrelated-job"])
    scheduler_verbs = []

    def fake_gcloud(argv, **kwargs):
        assert argv[0] == "gcloud"
        args = argv[1:]
        output = {}
        if args[:2] == ["functions", "describe"]:
            output = {"serviceConfig": {"uri": url, "service": "projects/test/services/admin"}}
        elif args[:2] == ["projects", "describe"]:
            output = {"projectNumber": "123456"}
        elif args[:3] == ["scheduler", "jobs", "list"]:
            output = list(jobs.values())
        elif args[:2] == ["scheduler", "jobs"]:
            verb, protocol, job_name = args[2:5]
            assert protocol == "http"
            options = dict(
                arg[2:].split("=", 1) for arg in args[5:] if arg.startswith("--") and "=" in arg
            )
            # Match gcloud's parser: --headers belongs only to create;
            # --update-headers belongs only to update.
            allowed = "headers" if verb == "create" else "update-headers"
            rejected = "update-headers" if verb == "create" else "headers"
            if rejected in options:
                return SimpleNamespace(returncode=2, stdout="", stderr=f"unrecognized --{rejected}")
            assert allowed in options
            assert (job_name in jobs) == (verb == "update")
            scheduler_verbs.append(verb)
            headers = jobs.get(job_name, {}).get("headers", {}).copy()
            header, value = options.pop(allowed).split("=", 1)
            headers[header] = value
            jobs[job_name] = {
                "name": f"projects/test/locations/asia-east1/jobs/{job_name}",
                "headers": headers,
                "options": options,
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(output), stderr="")

    monkeypatch.setattr(worker_setup.subprocess, "run", fake_gcloud)
    monkeypatch.setattr(worker_setup.WorkerSetup, "firestore", lambda self: None)
    monkeypatch.setattr(worker_setup.WorkerSetup, "storage", lambda self: None)
    setup = worker_setup.WorkerSetup(project, environment, apply=True)
    setup.configure()
    expected = deepcopy(jobs[name])
    assert expected["headers"] == {"Content-Type": "application/json"}
    options = expected["options"]
    assert options["uri"] == f"{url}/internal/maintenance/reconcile"
    assert options["oidc-token-audience"] == url
    assert (
        options["oidc-service-account-email"]
        == f"rag-tasks-{suffix}@{project}.iam.gserviceaccount.com"
    )
    assert options["http-method"] == "POST"
    assert options["message-body"] == "{}"
    assert options["schedule"] == "*/5 * * * *"

    jobs[name]["headers"]["Content-Type"] = "text/plain"
    jobs[name]["options"]["uri"] = "https://stale-target.invalid"
    setup.configure()
    assert jobs[name] == expected
    setup.configure()
    assert jobs[name] == expected
    assert scheduler_verbs == ["create", "update", "update"]
    assert jobs["unrelated-job"] == unrelated
