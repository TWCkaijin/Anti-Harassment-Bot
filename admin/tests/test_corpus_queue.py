"""Queued parsing, explicit field mapping, retry fencing and draft recovery."""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from admin.tests.test_corpus import Context, drain, import_job, run_parse
from admin.tests.test_corpus import queued as queued
from admin.tests.test_corpus import system as system
from admin_app.corpus.parsing import CorpusError, inspect_structured_upload, parse_upload
from admin_app.corpus.service import IMPORTS, MAX_PARSE_REVISIONS


def test_upload_queues_without_parsing_or_embedding(queued, monkeypatch):
    import admin_app.corpus.service

    monkeypatch.setattr(
        admin_app.corpus.service,
        "parse_upload",
        lambda *args, **kwargs: pytest.fail("HTTP upload must not parse"),
    )
    draft = queued.service.queue_preview("unparsed.pdf", b"a parser will reject this later", "law")
    assert draft["status"] == "parse_queued"
    assert draft["parse_job_id"] == draft["job"]["job_id"]
    assert queued.queued_jobs[draft["parse_job_id"]]["kind"] == "corpus_parse"
    assert len(queued.store.objects) == 1
    assert not queued.calls and not queued.business.data


def test_text_parse_worker_builds_preview_without_embeddings_and_is_replayable(queued):
    draft = queued.service.queue_preview("synthetic.txt", b"Synthetic content only.", "law")
    preview = run_parse(queued, draft)
    assert preview["status"] == "preview"
    assert preview["chunks"][0]["content"] == "Synthetic content only."
    assert preview["plan_hash"]
    assert not queued.calls and not queued.business.data
    before = copy.deepcopy(queued.store.objects)
    assert run_parse(queued, draft)["plan_hash"] == preview["plan_hash"]
    assert queued.store.objects == before


def test_structured_scan_requires_explicit_mapping_before_confirmation(queued):
    raw = "正文,法律名稱,條號,不使用\n合成文字,合成規則,第1條,不應加入\n".encode()
    draft = queued.service.queue_preview("synthetic.csv", raw, "law")
    scanned = run_parse(queued, draft)
    assert scanned["status"] == "mapping_required"
    assert scanned["available_fields"] == ["正文", "法律名稱", "條號", "不使用"]
    assert scanned["suggested_mapping"]["content"] == "正文"
    assert scanned["sample_rows"][0]["正文"] == "合成文字"
    with pytest.raises(CorpusError, match="尚未完成"):
        queued.service.confirm(draft["import_id"], "not-ready")
    mapped = queued.service.map_import(
        draft["import_id"], {"content": "正文", "law_name": "法律名稱", "article_number": "條號"}, 1
    )
    preview = run_parse(queued, mapped)
    assert preview["status"] == "preview"
    assert preview["parse_revision"] == 2
    assert preview["chunks"][0]["content"] == "合成文字"
    assert preview["chunks"][0]["metadata"]["law_name"] == "合成規則"
    assert "不使用" not in preview["chunks"][0]["metadata"]
    assert not queued.calls and not queued.business.data


@pytest.mark.parametrize(
    "mapping",
    [{}, {"content": "missing"}, {"embedding": "正文", "content": "正文"}, {"content": ["正文"]}],
)
def test_mapping_rejects_missing_unknown_or_unsafe_fields_without_changing_draft(queued, mapping):
    draft = queued.service.queue_preview("synthetic.csv", "正文\n合成文字\n".encode(), "law")
    run_parse(queued, draft)
    before = copy.deepcopy(queued.private.data)
    with pytest.raises(CorpusError):
        queued.service.map_import(draft["import_id"], mapping, 1)
    assert queued.private.data == before


def test_mapping_revision_and_confirmation_reservation_prevent_races(queued):
    draft = queued.service.queue_preview(
        "synthetic.json", b'[{"first":"one","second":"two"}]', "law"
    )
    run_parse(queued, draft)
    mapped = queued.service.map_import(draft["import_id"], {"content": "first"}, 1)
    with pytest.raises(CorpusError):
        queued.service.map_import(draft["import_id"], {"content": "second"}, 1)
    preview = run_parse(queued, mapped)
    queued.service.confirm(draft["import_id"], preview["plan_hash"])
    with pytest.raises(CorpusError, match="不能變更"):
        queued.service.map_import(draft["import_id"], {"content": "second"}, 2)


def test_supplied_mapping_is_checked_by_worker_and_can_be_corrected(queued):
    draft = queued.service.queue_preview(
        "synthetic.json", b'[{"body":"content"}]', "law", column_mapping={"content": "missing"}
    )
    with pytest.raises(CorpusError, match="找不到"):
        run_parse(queued, draft)
    failed = queued.service.import_preview(draft["import_id"])
    assert failed["status"] == "parse_failed"
    assert failed["available_fields"] == ["body"]
    fixed = queued.service.map_import(draft["import_id"], {"content": "body"}, 1)
    assert run_parse(queued, fixed)["status"] == "preview"
    assert not queued.calls


def test_parse_retry_reuses_object_after_private_commit_failure(queued, monkeypatch):
    draft = queued.service.queue_preview("synthetic.txt", b"Synthetic retry content", "law")
    original_put = queued.store.put_bytes_idempotent
    failed = False

    def put_then_interrupt(*args):
        nonlocal failed
        result = original_put(*args)
        if not failed:
            queued.private.fail_commit = True
            failed = True
        return result

    monkeypatch.setattr(queued.store, "put_bytes_idempotent", put_then_interrupt)
    with pytest.raises(RuntimeError, match="atomic commit"):
        run_parse(queued, draft)
    assert queued.service.import_preview(draft["import_id"])["status"] == "parse_failed"
    assert len(queued.store.objects) == 2
    assert run_parse(queued, draft)["status"] == "preview"
    assert len(queued.store.objects) == 2
    assert not queued.calls


def test_cancelled_parse_lease_cannot_publish_preview(queued, monkeypatch):
    from admin_app.jobs import JobCancelled

    draft = queued.service.queue_preview("synthetic.txt", b"Synthetic lease content", "law")
    context = Context()
    context.job_id = draft["parse_job_id"]
    context.lease_token = "current-lease"
    ref = queued.private.collection("admin_jobs").document(context.job_id)
    ref.set(
        {
            "status": "running",
            "lease_token": context.lease_token,
            "lease_until": datetime.now(UTC) + timedelta(minutes=5),
        }
    )
    original_put = queued.store.put_bytes_idempotent

    def cancel_after_upload(*args):
        result = original_put(*args)
        ref.update({"cancel_requested": True})
        return result

    monkeypatch.setattr(queued.store, "put_bytes_idempotent", cancel_after_upload)
    with pytest.raises(JobCancelled):
        queued.service.parse_job(queued.queued_jobs[context.job_id], context)
    assert queued.service.import_preview(draft["import_id"])["status"] != "preview"
    assert not queued.calls and not queued.business.data


def test_recovering_cancelled_parse_creates_new_bounded_revision(queued):
    draft = queued.service.queue_preview("synthetic.txt", b"Synthetic recovery", "law")
    queued.queued_jobs[draft["parse_job_id"]]["status"] = "cancelled"
    resumed = queued.service.resume_parse(draft["import_id"])
    assert resumed["parse_revision"] == 2
    assert resumed["parse_job_id"] != draft["parse_job_id"]
    assert run_parse(queued, resumed)["status"] == "preview"


def test_remap_then_source_delete_removes_all_historical_previews(queued):
    draft = queued.service.queue_preview(
        "synthetic.json", b'[{"first":"one","second":"two"}]', "law"
    )
    run_parse(queued, draft)
    first = run_parse(
        queued, queued.service.map_import(draft["import_id"], {"content": "first"}, 1)
    )
    final = run_parse(
        queued, queued.service.map_import(draft["import_id"], {"content": "second"}, 2)
    )
    assert first["plan_hash"] != final["plan_hash"]
    with pytest.raises(CorpusError, match="預覽不符"):
        queued.service.confirm(draft["import_id"], first["plan_hash"])
    job = queued.service.confirm(draft["import_id"], final["plan_hash"])
    source = drain(queued, import_job(final, job["job_id"]))
    assert len(queued.store.objects) == 3
    deletion = {"job_id": "d" * 32, "payload": {"source_id": source["source_id"], "revision": 1}}
    assert queued.service.delete_job(deletion, Context())["original_deleted"] is True
    assert queued.store.objects == {}


def test_draft_recovery_list_is_paginated_and_omits_private_object_references(queued):
    created = [
        queued.service.queue_preview(f"synthetic-{number}.txt", b"content", "law")
        for number in range(2)
    ]
    first = queued.service.list_imports(limit=1)
    second = queued.service.list_imports(limit=1, cursor=first["next_cursor"])
    assert {first["items"][0]["import_id"], second["items"][0]["import_id"]} == {
        item["import_id"] for item in created
    }
    assert all(
        "original" not in item and "binding" not in item
        for item in first["items"] + second["items"]
    )


def test_mapping_revision_bound_matches_storage_cleanup_bound(queued):
    draft = queued.service.queue_preview("synthetic.csv", b"body\ncontent\n", "law")
    run_parse(queued, draft)
    queued.private.collection(IMPORTS).document(draft["import_id"]).update(
        {"parse_revision": MAX_PARSE_REVISIONS}
    )
    with pytest.raises(CorpusError, match="上限"):
        queued.service.map_import(draft["import_id"], {"content": "body"}, MAX_PARSE_REVISIONS)


@pytest.mark.parametrize("raw", [b"body,body\none,two\n", b"body\none,two\n", b",body\none,two\n"])
def test_ambiguous_csv_headers_are_rejected(raw):
    with pytest.raises(CorpusError, match="欄"):
        inspect_structured_upload("synthetic.csv", raw)


def test_json_mapping_rejects_nontext_content_and_parses_metadata_object():
    with pytest.raises(CorpusError, match="非空文字"):
        parse_upload(
            "synthetic.json",
            b'[{"body":{"nested":"text"}}]',
            "law",
            column_mapping={"content": "body"},
        )
    parsed = parse_upload(
        "synthetic.json",
        b'[{"body":"text","meta":{"category":"synthetic"}}]',
        "law",
        column_mapping={"content": "body", "metadata": "meta"},
    )
    assert parsed["chunks"][0]["metadata"]["category"] == "synthetic"
