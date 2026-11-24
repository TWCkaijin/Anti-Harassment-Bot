"""Offline checks for the bounded official-source deadline corpus draft."""

import json

import pytest

from admin_app.scripts.common.legal_corpus import digest
from admin_app.scripts.ingest.official_laws import read_jsonl, verify_plan
from admin_app.scripts.maintenance import plan_verified_laws
from admin_app.scripts.maintenance.review_service_content import (
    COLLECTIONS,
    DATABASE_DOCUMENTS,
    PROJECT,
)
from rag_core.rag.legal_lookup import LAW_ALIASES, legal_references


def synthetic_snapshot():
    databases = {}
    for database, config_id in DATABASE_DOCUMENTS.items():
        databases[database] = {
            "runtime_config": {config_id: {"rag_collections": {"law": "rag_documents"}}},
            **{collection: {} for collection in COLLECTIONS},
        }
    databases["app-dev"]["rag_documents"]["unrelated"] = {
        "content": "Synthetic non-law document",
        "metadata": {},
    }
    snapshot = {"schema_version": 1, "project": PROJECT, "databases": databases}
    snapshot["snapshot_sha256"] = digest(snapshot)
    return snapshot


def test_verified_seed_plans_only_reviewed_articles():
    rows = read_jsonl(plan_verified_laws.SEED)
    assert len(rows) == len(plan_verified_laws.EXPECTED) == 13
    assert {row["doc_id"] for row in rows} == set(plan_verified_laws.EXPECTED)
    plan = plan_verified_laws.make_verified_law_plan(synthetic_snapshot(), "app-dev")
    verify_plan(plan)
    assert plan["counts"] == {"create": 13}
    assert {change["doc_id"] for change in plan["changes"]} == set(plan_verified_laws.EXPECTED)
    assert plan["destructive_deletes"] == 0


@pytest.mark.parametrize("article_number", ["9", "12"])
def test_new_articles_are_officially_scoped_and_can_be_planned_alone(article_number):
    rows = {row["doc_id"]: row for row in read_jsonl(plan_verified_laws.SEED)}
    assert "law-D0050074-99" not in rows
    doc_id = f"law-D0050074-{article_number}"
    article = rows[doc_id]
    if article_number == "9":
        assert "申訴、告訴、告發、提起訴訟、作證、提供協助" in article["content"]
        assert "不當之差別待遇" in article["content"]
        assert "損害賠償責任" in article["content"]
    else:
        assert "非財產上損害" in article["content"]
        assert "回復名譽" in article["content"]
        assert "一倍至三倍之懲罰性賠償金" in article["content"]
    assert "第99條" not in article["content"]
    metadata = article["metadata"]
    assert metadata["source_url"] == (
        f"https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=D0050074&flno={article_number}"
    )
    assert metadata["version"] == "D0050074@2023-08-16"
    assert metadata["article_effective_date"] == "2023-08-16"

    plan = plan_verified_laws.make_verified_law_plan(
        synthetic_snapshot(), "app-dev", only_doc_id=doc_id
    )
    verify_plan(plan)
    assert plan["counts"] == {"create": 1}
    assert plan["selected_doc_ids"] == [doc_id]
    assert [change["doc_id"] for change in plan["changes"]] == [doc_id]
    assert plan["destructive_deletes"] == 0


def test_single_article_plan_rejects_fabricated_article_99():
    with pytest.raises(ValueError, match="reviewed article ID"):
        plan_verified_laws.make_verified_law_plan(
            synthetic_snapshot(), "app-dev", only_doc_id="law-D0050074-99"
        )


def test_deadline_summaries_keep_distinct_triggers_and_routes():
    rows = {row["doc_id"]: row for row in read_jsonl(plan_verified_laws.SEED)}
    workplace = rows["law-N0030014-32-1"]["content"]
    school_filing = rows["law-H0080067-31"]["content"]
    school_appeal = rows["law-H0080067-37"]["content"]
    criminal = rows["law-C0010001-237"]["content"]
    assert "地方主管機關" in workplace and "雇主" in workplace
    assert "被申訴人非具權勢地位" in workplace
    assert "被申訴人具權勢地位" in workplace
    assert "自該行為終了時起逾五年" in workplace
    assert "自知悉性騷擾時起逾三年" in workplace
    assert "自該行為終了時起逾七年" in workplace
    assert "雇主未處理" in workplace and "合理時間" not in workplace
    assert "只有這兩個特別情形" in workplace
    assert "較長期限優先" not in workplace
    assert "首次申請" in school_filing and "三十日" in school_appeal
    assert "收到書面通知次日" in school_appeal
    assert "告訴乃論" in criminal and "知悉犯人" in criminal
    assert "不是所有刑事案件" in criminal
    assert rows["law-D0050074-14"]["metadata"]["article_effective_date"] == "2024-03-08"
    assert rows["law-N0030014-32-1"]["metadata"]["article_effective_date"] == "2024-03-08"
    assert rows["law-H0080067-37"]["metadata"]["article_effective_date"] == "2024-03-08"
    assert rows["law-C0010001-237"]["metadata"]["article_effective_date"] is None


def test_procedural_article_lookup_does_not_expand_bulk_law_set():
    assert "刑事訴訟法" not in LAW_ALIASES
    assert legal_references("刑事訴訟法第237條的告訴期間") == [("刑事訴訟法", "237")]


def test_import_rejects_unreviewed_effective_date(tmp_path, monkeypatch):
    rows = read_jsonl(plan_verified_laws.SEED)
    target = next(row for row in rows if row["doc_id"] == "law-H0080067-37")
    target["metadata"]["article_effective_date"] = "2023-08-16"
    seed = tmp_path / "seed.jsonl"
    seed.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(plan_verified_laws, "SEED", seed)
    with pytest.raises(ValueError, match="re-review official source"):
        plan_verified_laws.make_verified_law_plan(synthetic_snapshot(), "app-dev")
