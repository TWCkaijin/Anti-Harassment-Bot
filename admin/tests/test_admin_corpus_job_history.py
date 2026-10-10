"""Corpus recovery history is bounded and never discloses diagnostic work."""

import base64
from datetime import timedelta

import pytest

from admin_app import jobs


def seed(monkeypatch, kind, *, created_at=None, environment=None, expired=False):
    monkeypatch.setitem(jobs.HANDLERS, kind, lambda *args: {})
    job = jobs.submit_job(kind, {"private_input": "must-not-be-in-history"})
    changes = {"status": "succeeded", "result": {"import_id": "synthetic-import"}}
    if created_at:
        changes["created_at"] = created_at
    if environment:
        changes["environment"] = environment
    if expired:
        changes["expires_at"] = jobs.now() - timedelta(seconds=1)
    jobs.job_ref(job["job_id"]).update(changes)
    return job["job_id"]


def test_history_cursor_handles_equal_timestamps_without_duplicates(services, monkeypatch):
    current = jobs.now()
    expected = {
        seed(monkeypatch, kind, created_at=current)
        for kind in ("corpus_parse", "corpus_import", "corpus_delete")
    }
    first = jobs.list_corpus_jobs(limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = jobs.list_corpus_jobs(limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    items = first["items"] + second["items"]
    assert [item["job_id"] for item in items] == sorted(expected, reverse=True)
    for item in items:
        assert item["kind"] in jobs.CORPUS_JOB_KINDS
        assert item["created_at"] == current.isoformat()
        assert item["updated_at"] and item["result"] == {"import_id": "synthetic-import"}
        assert not {"input", "payload", "input_ref", "owner_session_id"}.intersection(item)


def test_history_excludes_other_environment_diagnostics_and_expired_results_before_storage_read(
    services, monkeypatch
):
    visible = seed(monkeypatch, "corpus_import")
    hidden = [
        seed(monkeypatch, "chat_test"),
        seed(monkeypatch, "corpus_parse", environment="preview"),
        seed(monkeypatch, "corpus_delete", expired=True),
    ]
    for job_id in hidden:
        jobs.job_ref(job_id).update(
            {"result_ref": {"bucket": "test-private", "name": "secret", "generation": "1"}}
        )
    monkeypatch.setattr(
        services[1], "read_bytes", lambda _: pytest.fail("hidden result object must not be read")
    )
    result = jobs.list_corpus_jobs()
    assert [item["job_id"] for item in result["items"]] == [visible]
    assert result["next_cursor"] is None


def test_pending_history_exposes_only_safe_target_ids_and_cancellation_state(services, monkeypatch):
    monkeypatch.setitem(jobs.HANDLERS, "corpus_parse", lambda *args: {})
    job = jobs.submit_job(
        "corpus_parse",
        {"import_id": "synthetic-import", "source_id": "not a safe id", "text": "private"},
    )
    jobs.job_ref(job["job_id"]).update({"cancel_requested": True})
    item = jobs.list_corpus_jobs()["items"][0]
    assert item["import_id"] == "synthetic-import" and item["cancel_requested"] is True
    assert "source_id" not in item and "text" not in item


def test_expired_page_still_advances_cursor_without_reading_retained_result(services, monkeypatch):
    current = jobs.now()
    visible = seed(monkeypatch, "corpus_import", created_at=current - timedelta(seconds=1))
    seed(monkeypatch, "corpus_parse", created_at=current, expired=True)
    first = jobs.list_corpus_jobs(limit=1)
    assert first["items"] == [] and first["next_cursor"]
    second = jobs.list_corpus_jobs(limit=1, cursor=first["next_cursor"])
    assert [item["job_id"] for item in second["items"]] == [visible]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("limit", [0, 101, True, "50"])
def test_history_rejects_unbounded_page_size(services, limit):
    with pytest.raises(jobs.JobFailure) as exc:
        jobs.list_corpus_jobs(limit=limit)
    assert exc.value.error["http_status"] == 400


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor!",
        "x" * 2049,
        base64.urlsafe_b64encode(
            jobs.canonical(
                {
                    "scope": "corpus-jobs:production",
                    "created_at": jobs.now().isoformat(),
                    "job_id": "a" * 32,
                }
            )
        ).decode(),
        base64.urlsafe_b64encode(
            jobs.canonical(
                {
                    "scope": "corpus-jobs:ci",
                    "created_at": jobs.now().isoformat(),
                    "job_id": "../other/path",
                }
            )
        ).decode(),
    ],
)
def test_history_rejects_invalid_or_cross_environment_cursor(services, cursor):
    with pytest.raises(jobs.JobFailure) as exc:
        jobs.list_corpus_jobs(cursor=cursor)
    assert exc.value.error["code"] == "invalid_cursor"
