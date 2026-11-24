import hashlib
from datetime import timedelta

import pytest

from admin_app import auth, jobs, maintenance


def test_diagnostics_expire_at_24h_and_cannot_be_polled_or_retried(
    signed_in, services, monkeypatch
):
    monkeypatch.setitem(jobs.HANDLERS, "chat_test", lambda *args: {})
    job = jobs.submit_job("chat_test", {})
    assert job["expires_at"] - job["created_at"] == timedelta(hours=24)
    client, headers = signed_in
    jobs.job_ref(job["job_id"]).update(
        {
            "expires_at": jobs.now() - timedelta(seconds=1),
            "owner_session_id": auth.digest(client.get_cookie(auth.COOKIE_NAME).value),
        }
    )
    assert client.get(f"/api/v1/admin/jobs/{job['job_id']}").status_code == 410
    assert (
        client.post(
            f"/api/v1/admin/jobs/{job['job_id']}/retry", json={}, headers=headers
        ).status_code
        == 410
    )
    jobs.execute_job(job["job_id"], 0)
    assert jobs.job_ref(job["job_id"]).get().to_dict()["status"] == "failed"


def test_reconcile_redrives_lost_queued_task_without_browser(services, monkeypatch):
    monkeypatch.setitem(jobs.HANDLERS, "fixture", lambda *args: {})
    job = jobs.submit_job("fixture", {})
    jobs.job_ref(job["job_id"]).update({"updated_at": jobs.now() - timedelta(hours=1)})
    assert jobs.reconcile_jobs() == {"examined": 1, "recovered": 1}
    assert jobs.get_job(job["job_id"])["dispatch_generation"] == 1


def test_scheduled_reconcile_never_dispatches_another_environment(services, monkeypatch):
    monkeypatch.setitem(jobs.HANDLERS, "fixture", lambda *args: {})
    local = jobs.submit_job("fixture", {})
    foreign = jobs.submit_job("fixture", {})
    foreign_ref = jobs.job_ref(foreign["job_id"])
    foreign_ref.update({"environment": "development", "dispatch_pending": True})
    before = foreign_ref.get().to_dict()
    services[3].clear()
    jobs.job_ref(local["job_id"]).update({"dispatch_pending": True})
    assert jobs.reconcile_jobs() == {"examined": 1, "recovered": 1}
    assert services[3] == [(local["job_id"], 0)]
    assert foreign_ref.get().to_dict() == before


def test_scheduled_reconcile_requires_worker_oidc(app, monkeypatch):
    monkeypatch.setattr(jobs, "verify_worker_request", lambda: False)
    assert app.test_client().post("/internal/maintenance/reconcile").status_code == 401


def draft(services, name):
    db, store, settings, _ = services
    original = store.put_bytes(f"corpus/imports/{name}/original", b"synthetic")
    ref = db.collection("corpus_imports").document(name)
    value = {
        "import_id": name,
        "source_id": f"src_{name}",
        "plan_hash": "fixture-hash",
        "status": "preview",
        "expires_at": jobs.now() - timedelta(hours=2),
        "original": original,
        "preview": None,
    }
    ref.set(value)
    key = f"{settings.environment}:corpus_import:corpus-import:{name}:fixture-hash"
    return ref, original, hashlib.sha256(key.encode()).hexdigest()[:32]


def test_expired_draft_cleanup_preserves_confirmed_assets_and_unlinked_jobs(services, monkeypatch):
    monkeypatch.setattr(maintenance, "get_admin_settings", lambda: services[2])
    db, store, _, _ = services
    disposable, removed, _ = draft(services, "orphan")
    active, retained, _ = draft(services, "active")
    queued, queued_ref, job_id = draft(services, "queued")
    db.collection("corpus_source_assets").document("src_active").set({"original": retained})
    db.collection("admin_jobs").document(job_id).set({"status": "queued"})
    assert maintenance.cleanup_expired_previews() == 1
    assert disposable.get().to_dict()["status"] == "expired"
    assert removed["name"] not in store.objects
    assert active.get().to_dict()["status"] == queued.get().to_dict()["status"] == "preview"
    assert retained["name"] in store.objects and queued_ref["name"] in store.objects


def test_cleanup_retries_generation_safe_deletion_after_interruption(services, monkeypatch):
    monkeypatch.setattr(maintenance, "get_admin_settings", lambda: services[2])
    ref, original, _ = draft(services, "interrupted")
    ref.update({"status": "expired_cleanup"})
    services[1].delete(original)
    assert maintenance.cleanup_expired_previews() == 1
    assert ref.get().to_dict()["status"] == "expired"


def parse_draft(services, monkeypatch, name, *, state="parse_queued", revision=3):
    monkeypatch.setattr(maintenance, "get_admin_settings", lambda: services[2])
    monkeypatch.setitem(
        jobs.HANDLERS, "corpus_parse", lambda *args: pytest.fail("cleanup must not parse")
    )
    ref, original, _ = draft(services, name)
    job = jobs.submit_job("corpus_parse", {"import_id": name, "parse_revision": revision})
    ref.update(
        {
            "status": state,
            "parse_revision": revision,
            "parse_job_id": job["job_id"],
            "environment": services[2].environment,
            "plan_hash": None,
        }
    )
    return ref, original, jobs.job_ref(job["job_id"])


def test_expired_parse_cancels_queue_and_cleans_historical_and_unlinked_assets(
    services, monkeypatch
):
    ref, original, parse_job = parse_draft(services, monkeypatch, "parsed")
    store = services[1]
    historical = store.put_bytes("corpus/imports/parsed/preview-1.json", b"unlinked-old")
    linked = store.put_bytes("corpus/imports/parsed/preview-2.json", b"linked")
    unlinked = store.put_bytes("corpus/imports/parsed/preview-3.json", b"uploaded-before-crash")
    extra = store.put_bytes("corpus/imports/parsed/historical.json", b"legacy-linked")
    ref.update({"preview": linked, "draft_assets": [extra, linked]})
    assert maintenance.cleanup_expired_previews() == 1
    assert all(
        item["name"] not in store.objects
        for item in [original, historical, linked, unlinked, extra]
    )
    cleaned = ref.get().to_dict()
    assert cleaned["status"] == "expired" and cleaned["draft_assets"] == []
    assert cleaned["parse_revision"] == 4 and cleaned["cleanup_parse_revision"] == 3
    cancelled = parse_job.get().to_dict()
    assert cancelled["status"] == "cancelled" and cancelled["dispatch_generation"] == 1
    assert cancelled["cancel_requested"] and not cancelled["dispatch_pending"]
    jobs.execute_job(parse_job.id, 0)
    assert parse_job.get().to_dict() == cancelled


def test_expired_draft_waits_for_active_parse_then_fences_expired_worker(services, monkeypatch):
    ref, original, parse_job = parse_draft(services, monkeypatch, "active-parser", state="parsing")
    claimed = jobs._claim(parse_job.id, 0)
    context = jobs.JobContext(parse_job.id, claimed["lease_token"])
    assert maintenance.cleanup_expired_previews() == 0
    assert ref.get().to_dict()["status"] == "parsing"
    assert original["name"] in services[1].objects
    parse_job.update({"lease_until": jobs.now() - timedelta(seconds=1)})
    assert maintenance.cleanup_expired_previews() == 1
    with pytest.raises(jobs.LeaseLost):
        context.checkpoint({"late_result": True})


@pytest.mark.parametrize("state", ["mapping_required", "parse_failed"])
def test_terminal_parse_job_does_not_keep_unconfirmed_draft_forever(services, monkeypatch, state):
    ref, _, parse_job = parse_draft(services, monkeypatch, state, state=state)
    parse_job.update({"status": "failed"})
    assert maintenance.cleanup_expired_previews() == 1
    assert ref.get().to_dict()["status"] == "expired"
    assert parse_job.get().to_dict()["status"] == "failed"


def test_interrupted_cleanup_retains_original_parse_revision_for_orphan_lookup(
    services, monkeypatch
):
    ref, _, _ = parse_draft(services, monkeypatch, "interrupted-parse")
    store = services[1]
    orphan = store.put_bytes("corpus/imports/interrupted-parse/preview-3.json", b"orphan")
    delete = store.delete

    def interrupt(reference):
        if reference["name"] == orphan["name"]:
            raise TimeoutError("synthetic interruption")
        delete(reference)

    monkeypatch.setattr(store, "delete", interrupt)
    with pytest.raises(TimeoutError):
        maintenance.cleanup_expired_previews()
    assert ref.get().to_dict()["status"] == "expired_cleanup"
    assert ref.get().to_dict()["cleanup_parse_revision"] == 3
    monkeypatch.setattr(store, "delete", delete)
    assert maintenance.cleanup_expired_previews() == 1
    assert orphan["name"] not in store.objects
    assert ref.get().to_dict()["parse_revision"] == 4


def test_legacy_unlinked_development_import_stays_protected_in_preview_database(
    services, monkeypatch
):
    monkeypatch.setattr(maintenance, "get_admin_settings", lambda: services[2])
    ref, original, _ = draft(services, "legacy-development")
    key = "development:corpus_import:corpus-import:legacy-development:fixture-hash"
    linked = hashlib.sha256(key.encode()).hexdigest()[:32]
    services[0].collection("admin_jobs").document(linked).set({"status": "queued"})
    assert maintenance.cleanup_expired_previews() == 0
    assert ref.get().to_dict()["status"] == "preview"
    assert original["name"] in services[1].objects


def test_failed_confirmation_reservation_without_job_expires_after_grace(services, monkeypatch):
    monkeypatch.setattr(maintenance, "get_admin_settings", lambda: services[2])
    ref, original, _ = draft(services, "reserved-without-job")
    ref.update({"confirm_reserved": True})
    assert maintenance.cleanup_expired_previews() == 1
    assert ref.get().to_dict()["status"] == "expired"
    assert original["name"] not in services[1].objects


def test_cleanup_limit_applies_across_all_draft_states(services, monkeypatch):
    monkeypatch.setattr(maintenance, "get_admin_settings", lambda: services[2])
    first, _, _ = draft(services, "first")
    second, _, _ = draft(services, "second")
    first.update({"status": "mapping_required"})
    assert maintenance.cleanup_expired_previews(limit=1) == 1
    assert {first.get().to_dict()["status"], second.get().to_dict()["status"]} == {
        "expired",
        "preview",
    }
