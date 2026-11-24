"""Claim repair regressions using synthetic evidence, without legal correctness claims.

Ported from the retired v2/v3 suites; they now call the shared checker directly.
"""

from types import SimpleNamespace

import pytest

from rag_core.core.answer_validation import check_answer_units


def law_source(identifier, law, article):
    return {
        "doc_id": identifier,
        "law_name": law,
        "article_number": article,
        "label": f"{law}第{article}條",
        "version": "synthetic-version",
        "checked_at": "2026-10-01",
    }


def check(text, sources=(), message="合成問題", *, source_ids=None, **kwargs):
    unit = SimpleNamespace(
        text=text,
        source_ids=[item["doc_id"] for item in sources] if source_ids is None else source_ids,
    )
    return check_answer_units([unit], list(sources), message, **kwargs)[0]


def test_law_and_article_must_match_the_same_source():
    # Neither supplied document supports this law/article pair.
    claim = "合成測試：依性別平等工作法第12條評估。"
    section = check(
        claim,
        [
            law_source("law/work-13", "性別平等工作法", "13"),
            law_source("law/civil-12", "民法", "12"),
        ],
    )
    assert claim not in section["text"]


def test_article_matching_is_exact_and_normalizes_chinese_numbers():
    unsupported = "合成測試：依性別平等工作法第12條評估。"
    section = check(unsupported, [law_source("law/work-112", "性別平等工作法", "112")])
    assert unsupported not in section["text"]
    supported = "合成測試：依性別平等工作法第十二條評估。"
    section = check(supported, [law_source("law/work-12", "性別平等工作法", "12")])
    assert supported == section["text"]


def test_checked_metadata_does_not_support_a_contradicting_deadline():
    evidence = law_source("law/work-12", "性別平等工作法", "12")
    claim = "合成測試：依性別平等工作法第12條，申訴期限為一年。"
    section = check(
        claim,
        [evidence],
        documents=[{"source": evidence, "content": "合成測試資料：申訴期限為二年。"}],
    )
    assert claim not in section["text"]


def test_repair_does_not_leave_a_citation_attached_only_to_removed_claim():
    section = check(
        "合成測試：依性別平等工作法第999條評估。你可以先休息。",
        [law_source("law/work-12", "性別平等工作法", "12")],
    )
    assert "你可以先休息" in section["text"]
    assert "第999條" not in section["text"]
    assert section["source_ids"] == []


def test_known_old_version_does_not_establish_current_law_or_deadline():
    text = check(
        "依現行性騷擾防治法第13條，你應於事發後一年提出申訴。",
        [{"doc_id": "law/old", "version": "2009"}],
        "申訴",
    )["text"]
    assert "一年" not in text
    assert "目前沒有可核對的官方來源" in text
    assert "版本待核對" not in text
    assert "期限待核對" not in text


JUDGMENT = {"doc_id": "rag_judgments/synthetic", "type": "judgment", "label": "合成判決"}


def test_completed_judgment_replaces_pending_outcome_but_keeps_other_advice():
    text = check(
        "此案目前在起訴階段，後續法院將審理。你可以先記錄可核對的事實。",
        [JUDGMENT],
        "請找真實判決",
        documents=[{"source": JUDGMENT, "content": "本院判決處刑如下：主文載明裁判結果。"}],
    )["text"]
    assert "目前在起訴階段" not in text
    assert "後續法院將審理" not in text
    assert "判決有裁判結果" in text
    assert "你可以先記錄可核對的事實" in text


def test_judgment_pending_guard_requires_a_completed_verdict_in_cited_source():
    source = {**JUDGMENT, "label": "合成資料"}
    text = check(
        "此案目前在起訴階段。",
        [source],
        "案件進度",
        documents=[{"source": source, "content": "案件起訴資料，尚無法院裁判。"}],
    )["text"]
    assert text == "此案目前在起訴階段。"


@pytest.mark.parametrize(
    "claim",
    [
        "此案後續法院將審理。",
        "法院已認定本案，後續審理結果將依刑事程序裁決。",
    ],
)
def test_judgment_title_blocks_pending_claim_even_when_retrieved_chunk_omits_verdict(claim):
    source = {
        "doc_id": "rag_judgments/synthetic-chunk",
        "type": "judgment",
        "label": "某法院某年度刑事判決",
    }
    text = check(
        claim,
        [source],
        "案件進度",
        documents=[{"source": source, "content": "裁判書中的起訴書附件片段。"}],
    )["text"]
    assert "後續" not in text
    assert "判決有裁判結果" in text


def test_claim_repair_keeps_unrelated_support_and_resource_duration():
    text = check(
        "先保留證據。申訴期限為一年。可查看24小時支持資源與現行資源資訊。",
        message="法律資訊",
    )["text"]
    assert "先保留證據" in text
    assert "24小時支持資源" in text
    assert "現行資源資訊" in text
    assert "申訴期限為一年" not in text


def test_annotation_mode_keeps_raw_claim_in_diagnostics_without_displaying_false_precision():
    diagnostics = {}
    text = check(
        "依刑法第999條可以申訴。",
        message="法律資訊",
        content_policy="annotate",
        diagnostics=diagnostics,
    )["text"]
    assert "第999條" not in text
    assert "沒有可核對的官方來源" in text
    assert diagnostics["rewrites"][0]["reasons"] == ["unsupported_article"]
    assert "before" not in diagnostics["rewrites"][0]
    assert "after" not in diagnostics["rewrites"][0]
