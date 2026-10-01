"""Answer checks use synthetic laws; these tests do not assert real legal rules."""

from types import SimpleNamespace

import pytest

from backend.app.agents.guided_chat import _duration_values, check_answer_units


def source(identifier="fixture/1", law="測試法", article="1", checked=True):
    result = {
        "doc_id": identifier,
        "law_name": law,
        "article_number": article,
        "label": f"{law}第{article}條",
    }
    if checked:
        result.update(version="synthetic", checked_at="2026-10-01")
    return result


def checked(text, sources=(), contents=None, **kwargs):
    unit = SimpleNamespace(text=text, source_ids=[item["doc_id"] for item in sources])
    documents = [
        {"source": item, "content": (contents or {}).get(item["doc_id"], "")} for item in sources
    ]
    return check_answer_units([unit], list(sources), "合成問題", documents=documents, **kwargs)[0]


def test_general_legal_direction_without_sources_preserves_original_formatting():
    text = (
        "  可能涉及《性別平等工作法》，可先了解雇主的處理機制。\r\n\r\n"
        "- 你可以先保留訊息。\r\n  - 也可向信任的人尋求支持！!\r\n"
    )
    diagnostics = {}
    result = checked(text, diagnostics=diagnostics)
    assert result["text"] == text
    assert result["source_ids"] == []
    assert not diagnostics.get("rewrites")


@pytest.mark.parametrize(
    "date",
    [
        "2026年9月30日",
        "2026 年 9 月 30 日",
        "民國115年9月30日",
        "115年9月30日",
        "二〇二六年九月三十日",
        "9月30日",
        "2026年9月",
        "2026年",
        "民國115年",
        "2026-09-30",
        "2026/9/30",
    ],
)
def test_event_date_does_not_invalidate_supported_filing_period(date):
    evidence = source()
    text = f"依《測試法》第1條，對{date}事件的申訴期限為二年。"
    result = checked(text, [evidence], {"fixture/1": "合成條文：申訴期限為二年。"})
    assert result["text"] == text
    assert result["source_ids"] == ["fixture/1"]


def test_calendar_components_do_not_supply_evidence_for_a_period():
    evidence = source()
    result = checked(
        "依《測試法》第1條，申訴期限為九個月。",
        [evidence],
        {"fixture/1": "本條於2026年9月30日公布。"},
    )
    assert "九個月" not in result["text"]
    assert "期限待核對" in result["text"]
    assert _duration_values("一年六個月，24小時；2026年9月30日") == {
        ("1", "年"),
        ("6", "月"),
        ("24", "小時"),
    }


@pytest.mark.parametrize("policy", ["repair", "annotate"])
def test_local_precision_notices_preserve_other_actions_and_format(policy):
    text = "- 先保留訊息，依《測試法》第999條可申訴。\n\n- 可尋求支持，申訴期限為一年。"
    result = checked(text, content_policy=policy)
    assert result["text"] == (
        "- 先保留訊息，依《測試法》〔條號待核對〕可申訴。\n\n"
        "- 可尋求支持，申訴期限為〔期限待核對〕。"
    )
    assert result["source_ids"] == []


def test_earlier_warning_never_silently_deletes_later_units():
    seen, diagnostics = set(), {}
    results = [
        checked(text, seen_reasons=seen, diagnostics=diagnostics)["text"]
        for text in [
            "依《測試法》第999條可申訴。",
            "申訴期限為一年。你可以先休息。",
            "申訴期限為二年。",
        ]
    ]
    assert all(results)
    assert "條號待核對" in results[0]
    assert "期限待核對" in results[1] and "你可以先休息。" in results[1]
    assert "期限待核對" in results[2]
    assert len(diagnostics["rewrites"]) == 3


def test_paragraph_pronoun_uses_preceding_law_and_article_not_other_cited_law():
    evidence = [source(), source("fixture/2", "另一測試法", "2")]
    contents = {"fixture/1": "申訴期限為二年。", "fixture/2": "申訴期限為一年。"}
    text = "依《測試法》第1條可評估。本條的申訴期限為二年。"
    assert checked(text, evidence, contents)["text"] == text
    wrong = checked(text.replace("二年", "一年"), evidence, contents)
    assert "一年" not in wrong["text"]
    assert "依《測試法》第1條可評估。" in wrong["text"]
    assert wrong["source_ids"] == ["fixture/1"]


def test_same_law_article_context_cannot_borrow_another_articles_deadline():
    evidence = [source(), source("fixture/2", "測試法", "2")]
    contents = {"fixture/1": "申訴期限為二年。", "fixture/2": "申訴期限為一年。"}
    result = checked("依《測試法》第1條。本條的申訴期限為一年。", evidence, contents)
    assert "一年" not in result["text"]
    assert "期限待核對" in result["text"]


def test_named_law_and_article_pair_must_be_supported_by_one_source():
    evidence = [source("work/13", "性別平等工作法", "13"), source("civil/12", "民法", "12")]
    result = checked("可能涉及性別平等工作法。依該法第12條評估。", evidence)
    assert "可能涉及性別平等工作法。" in result["text"]
    assert "第12條" not in result["text"]
    assert "civil/12" not in result["source_ids"]


def test_new_paragraph_can_name_a_different_law_without_old_context():
    evidence = [source(), source("fixture/2", "另一測試法", "2")]
    contents = {"fixture/1": "申訴期限為二年。", "fixture/2": "申訴期限為一年。"}
    text = "依《測試法》第1條。\n\n依《另一測試法》第2條，申訴期限為一年。"
    assert checked(text, evidence, contents)["text"] == text


def test_unknown_version_only_qualifies_current_version_and_exact_period():
    evidence = source(checked=False)
    text = "依現行《測試法》第1條，申訴期限為二年。\n可先保存相關訊息。"
    result = checked(text, [evidence], {"fixture/1": "申訴期限為二年。"})
    assert result["text"] == (
        "依版本待核對的《測試法》第1條，申訴期限為〔期限待核對〕。\n可先保存相關訊息。"
    )


def test_resource_hours_are_not_automatically_statutory_deadlines():
    text = "可參考24小時支持資源。申訴服務開放24小時，也可先休息一天。"
    assert checked(text)["text"] == text


def test_article_suffix_and_alias_are_supported_without_changing_display():
    evidence = source("work/12-1", "性別平等工作法", "12-1")
    text = "依性工法第十二條之一可初步評估。"
    assert checked(text, [evidence])["text"] == text


def test_unknown_source_identifier_remains_a_contract_error():
    with pytest.raises(ValueError, match="outside this retrieval"):
        check_answer_units(
            [SimpleNamespace(text="可先尋求支持。", source_ids=["invented"])], [], ""
        )


@pytest.mark.parametrize("metadata_number", ["32-1", "32之1", "32條之1"])
@pytest.mark.parametrize("display_number", ["第32-1條", "第32之1條", "第32條之1", "第三十二條之一"])
def test_equivalent_article_suffix_formats_keep_valid_reference(metadata_number, display_number):
    evidence = source("work/32-1", "性別平等工作法", metadata_number)
    text = f"可依《性別平等工作法》{display_number}初步評估。"
    assert checked(text, [evidence])["text"] == text


@pytest.mark.parametrize(
    "date", ["2026年10月31日", "民國115年10月31日", "2026-10-31", "2026/10/31"]
)
def test_actual_calendar_cutoff_requires_support_while_event_dates_remain(date):
    text = f"事件發生於2026年9月30日；須於{date}前提出申訴，並可先保留訊息。"
    result = checked(text)
    assert "事件發生於2026年9月30日" in result["text"]
    assert date not in result["text"]
    assert "截止日期待核對" in result["text"]
    assert "並可先保留訊息。" in result["text"]


def test_calendar_cutoff_needs_deadline_evidence_not_just_source_publication_date():
    evidence = source()
    text = "依《測試法》第1條，須於2026年10月31日前申訴。"
    unsupported = checked(text, [evidence], {"fixture/1": "本法於2026年10月31日公布。"})
    assert "2026年10月31日" not in unsupported["text"]
    assert checked(text, [evidence], {"fixture/1": "申訴期限至2026年10月31日。"})["text"] == text


def test_range_and_compound_durations_are_not_calendar_dates():
    assert _duration_values("申訴期限為一年六月") == {("1", "年"), ("6", "月")}
    result = checked("申訴期限為一至二年。")
    assert "一至二年" not in result["text"]
    assert "一至" not in result["text"]
