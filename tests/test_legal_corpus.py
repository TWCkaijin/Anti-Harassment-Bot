import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.rag.legal_lookup import LAW_ALIASES, article_number, legal_references
from backend.scripts.common.legal_corpus import (
    apply_current_supplements,
    audit_legacy,
    build_corpus,
    digest,
    plan_import,
)
from backend.scripts.ingest.official_laws import (
    apply_plan,
    decode_firestore,
    encode_firestore,
    read_jsonl,
    read_rollback_journal,
    rollback,
    verify_plan,
)


def sample_bulk():
    return {
        "UpdateDate": "2026/9/18",
        "Laws": [
            {
                "LawName": name,
                "LawURL": f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A{index:07d}",
                "LawModifiedDate": "20230816",
                "LawEffectiveDate": "20240308",
                "LawEffectiveNote": "部分條文另定施行日",
                "LawArticles": [
                    {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第一章"},
                    {
                        "ArticleType": "A",
                        "ArticleNo": "第 12 條",
                        "ArticleContent": "本文\n但例外條件應完整保留。",
                    },
                ],
            }
            for index, name in enumerate(LAW_ALIASES, 1)
        ],
    }


def test_article_identifiers_preserve_law_and_subarticle_direction():
    assert article_number("第三十二條之一") == "32-1"
    assert article_number("第 １２３ 條") == "123"
    assert legal_references("性別工作平等法第十二條與刑法第304條。") == [
        ("性別平等工作法", "12"),
        ("中華民國刑法", "304"),
    ]
    assert legal_references("職場性騷擾") == []
    assert legal_references("性別平等工作法。個人陳述第12條") == [("性別平等工作法", None)]


def test_bulk_import_preserves_whole_articles_and_does_not_invent_effective_dates():
    bulk = sample_bulk()
    bulk["Laws"][0]["LawEffectiveDate"] = "99991231"
    docs, manifest = build_corpus(bulk, checked_at="2026-10-01", source_sha256="a" * 64)
    assert len(docs) == 6
    assert all("但例外條件" in row["content"] for row in docs)
    first = next(row for row in docs if row["metadata"]["law_name"] == bulk["Laws"][0]["LawName"])
    assert first["metadata"]["effective_date"] is None
    assert (
        first["metadata"]["promulgated_date_scope"] == "law_latest_amendment_not_article_specific"
    )
    assert manifest["review_status"] != "expert_reviewed"
    with pytest.raises(ValueError, match="six laws"):
        build_corpus(
            {**bulk, "Laws": bulk["Laws"][:-1]}, checked_at="2026-10-01", source_sha256="a"
        )


def test_official_snapshot_integrity_and_pending_law_are_separate():
    root = Path(__file__).parents[1] / "resource/legal/official"
    docs = read_jsonl(root / "articles.jsonl")
    manifest = json.loads((root / "manifest.json").read_text())
    assert digest(docs) == manifest["documents_sha256"]
    assert len(docs) == manifest["document_count"]
    assert len(manifest["laws"]) == 6
    current = next(row for row in docs if row["doc_id"] == "law-B0000001-1223")
    future = next(row for row in docs if row["doc_id"].startswith("law-B0000001-1223-pending"))
    assert current["metadata"]["corpus_active"] is True
    assert "兄弟姊妹" in current["content"]
    assert future["metadata"]["corpus_active"] is False
    article12 = next(row for row in docs if row["doc_id"] == "law-N0030014-12")
    assert "任何人" in article12["content"]
    assert "受自己指揮、監督" in article12["content"]
    assert article12["metadata"]["chunk_count"] == 1
    stale = json.loads((root.parent / "current_supplements.json").read_text())
    with pytest.raises(ValueError, match="check date"):
        apply_current_supplements(docs, {**manifest, "checked_at": "2026-10-02"}, stale)


def test_legacy_audit_marks_outdated_seed_without_promoting_other_rows():
    legacy = [
        {"doc_id": "old", "content": "性騷擾防治法第13條，事件發生後一年內申訴", "metadata": {}}
    ]
    result = audit_legacy(legacy, [])
    assert "outdated_article_13_one_year_complaint_rule" in result["documents"][0]["conflicts"]
    assert "source_url" in result["documents"][0]["missing_metadata"]


def test_import_plan_quarantines_conflicts_and_keeps_unrelated_documents():
    docs, _ = build_corpus(sample_bulk(), checked_at="2026-10-01", source_sha256="a")
    existing = [
        {
            "doc_id": "old",
            "data": {"content": "性騷擾防治法第13條，事件發生後一年內申訴", "metadata": {}},
        },
        {"doc_id": "unrelated", "data": {"content": "一般支持指引", "metadata": {}}},
    ]
    plan = plan_import(docs, existing, "laws", "test-only")
    assert plan["destructive_deletes"] == 0
    assert plan["counts"]["quarantine"] == 1
    assert all(row["doc_id"] != "unrelated" for row in plan["changes"])
    verify_plan(plan)
    plan["changes"][0]["payload"]["content"] += "tampered"
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_plan(plan)


class FakeRef:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def get(self, **kwargs):
        return SimpleNamespace(
            exists=self.name in self.db.docs, to_dict=lambda: self.db.docs.get(self.name)
        )


class FakeDB:
    project = "test-only"

    def __init__(self, docs=None):
        self.docs = docs or {}
        self.writes = 0

    def collection(self, name):
        return SimpleNamespace(document=lambda doc_id: FakeRef(self, doc_id))

    def transaction(self):
        return self

    def set(self, ref, payload):
        self.docs[ref.name] = payload
        self.writes += 1

    def delete(self, ref):
        self.docs.pop(ref.name, None)
        self.writes += 1


@pytest.mark.asyncio
async def test_apply_journals_originals_and_rollback_refuses_concurrent_changes(
    tmp_path, monkeypatch
):
    from google.cloud import firestore

    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    docs, _ = build_corpus(sample_bulk(), checked_at="2026-10-01", source_sha256="a")
    docs = docs[:2]
    plan = plan_import(docs, [], "laws", "test-only")
    db = FakeDB()
    backup = tmp_path / "rollback.jsonl"

    async def embed(content):
        return [0.1, 0.2]

    await apply_plan(plan, db=db, embed=embed, backup_path=backup)
    assert db.writes == 2
    journal = read_jsonl(backup)
    assert journal[0]["kind"] == "header"
    assert journal[1]["before"] is None
    assert rollback(journal, db, apply=False)["restorable"] == 2
    assert db.writes == 2
    first_id = docs[0]["doc_id"]
    db.docs[first_id] = {**db.docs[first_id], "content": "concurrent legitimate edit"}
    result = rollback(journal, db, apply=True)
    assert result == {"restorable": 1, "already_original": 0, "conflicts": 1}
    assert db.docs[first_id]["content"] == "concurrent legitimate edit"
    assert len(db.docs) == 1


@pytest.mark.asyncio
async def test_apply_rejects_stale_snapshot_before_embedding_or_write(tmp_path, monkeypatch):
    from google.cloud import firestore

    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    docs, _ = build_corpus(sample_bulk(), checked_at="2026-10-01", source_sha256="a")
    plan = plan_import(docs[:1], [], "laws", "test-only")
    db = FakeDB({docs[0]["doc_id"]: {"content": "new cloud edit"}})

    async def embed(content):
        raise AssertionError("stale plan must not call embedding")

    with pytest.raises(ValueError, match="changed since"):
        await apply_plan(plan, db=db, embed=embed, backup_path=tmp_path / "rollback.jsonl")
    assert db.writes == 0


@pytest.mark.parametrize("partial_tail", [b'{"kind":"document","doc_id":', b'{"note":"\xe4\xb8'])
def test_rollback_journal_recovers_prior_durable_rows_after_interrupted_append(
    tmp_path, monkeypatch, partial_tail
):
    from google.cloud import firestore

    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    after = {"content": "imported", "_legal_import": {"batch_id": "test-batch"}}
    rows = [
        {"kind": "header", "project": "test-only", "collection": "laws"},
        {
            "kind": "document",
            "doc_id": "already-written",
            "before": None,
            "after_sha256": digest(after),
        },
    ]
    path = tmp_path / "interrupted.jsonl"
    path.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows) + partial_tail)
    journal, ignored_tail = read_rollback_journal(path)
    assert journal == rows
    assert ignored_tail is True
    db = FakeDB({"already-written": after})
    assert rollback(journal, db, apply=True)["restorable"] == 1
    assert db.docs == {}


@pytest.mark.parametrize(
    "contents",
    [
        b'{"kind":"header"}\nBROKEN\n',
        b'{"kind":"header"}\nBROKEN\n{"kind":"complete"}\n',
        b'{"kind":"head',
        b'{"kind":"header"}\n[]',
    ],
)
def test_rollback_journal_does_not_ignore_corruption_or_missing_header(tmp_path, contents):
    path = tmp_path / "corrupt.jsonl"
    path.write_bytes(contents)
    with pytest.raises(ValueError):
        read_rollback_journal(path)


def test_valid_final_journal_record_without_newline_is_retained(tmp_path):
    path = tmp_path / "valid.jsonl"
    path.write_text('{"kind":"header"}\n{"kind":"complete"}')
    journal, ignored_tail = read_rollback_journal(path)
    assert journal[-1] == {"kind": "complete"}
    assert ignored_tail is False


@pytest.mark.asyncio
async def test_import_and_rollback_preserve_firestore_nanosecond_timestamps(tmp_path, monkeypatch):
    from google.api_core.datetime_helpers import DatetimeWithNanoseconds
    from google.cloud import firestore
    from google.cloud.firestore_v1.vector import Vector

    monkeypatch.setattr(firestore, "transactional", lambda function: function)
    stamp = DatetimeWithNanoseconds.from_rfc3339("2026-10-01T00:00:00.123456789Z")
    roundtrip = decode_firestore(encode_firestore(stamp))
    assert roundtrip.rfc3339() == stamp.rfc3339()
    assert roundtrip.nanosecond == 123456789
    docs, _ = build_corpus(sample_bulk(), checked_at="2026-10-01", source_sha256="a")
    doc = docs[0]
    before = {
        "content": "old official text",
        "metadata": {"updated_at": stamp},
        "embedding": Vector([0.3, 0.4]),
    }
    db = FakeDB({doc["doc_id"]: before})
    snapshot = [{"doc_id": doc["doc_id"], "data": encode_firestore(before)}]
    plan = plan_import([doc], snapshot, "laws", "test-only")

    async def embed(content):
        return [0.1, 0.2]

    backup = tmp_path / "nano-backup.jsonl"
    await apply_plan(plan, db=db, embed=embed, backup_path=backup)
    journal, ignored_tail = read_rollback_journal(backup)
    assert ignored_tail is False
    assert rollback(journal, db, apply=True)["restorable"] == 1
    restored = db.docs[doc["doc_id"]]
    assert restored["metadata"]["updated_at"].nanosecond == 123456789
    assert encode_firestore(restored) == encode_firestore(before)
