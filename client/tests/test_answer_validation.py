"""Answer checks use synthetic laws; these tests do not assert real legal rules."""

from types import SimpleNamespace

import pytest

from rag_core.core.answer_validation import _duration_values, check_answer_units


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


def reviewed_workplace_source():
    return {
        "doc_id": "rag_documents/law-N0030014-32-1",
        "law_name": "性別平等工作法",
        "article_number": "32-1",
        "label": "性別平等工作法第32條之1（官方法源摘要）",
        "source_url": "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0030014&flno=32-1",
        "version": "N0030014@2023-08-16",
        "checked_at": "2026-11-10",
    }


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
    assert "這項期限目前沒有可核對的官方來源" in result["text"]


@pytest.mark.parametrize(
    "claim",
    [
        "《測試法》沒有明確的申訴期限。",
        "《測試法》並未設置固定的法定期限。",
        "《測試法》沒有明確的法定期限。",
    ],
)
def test_no_deadline_claim_requires_an_explicit_versioned_source(claim):
    unsupported = checked(claim)
    assert claim not in unsupported["text"]
    assert "這項期限目前沒有可核對的官方來源" in unsupported["text"]

    evidence = source()
    supported = checked(
        claim,
        [evidence],
        {"fixture/1": f"合成條文摘要：{claim}"},
    )
    assert supported["text"] == claim
    assert supported["source_ids"] == ["fixture/1"]
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
        "- 先保留訊息，《測試法》的條號目前沒有可核對的官方來源。\n\n"
        "- 可尋求支持，這項期限目前沒有可核對的官方來源。"
    )
    assert result["source_ids"] == []


@pytest.mark.parametrize("quote_cue", ["條文如下：", "條文內容是：", "條文："])
def test_unsupported_article_does_not_leave_apparent_statutory_quote(quote_cue):
    text = f"《測試法》第999條{quote_cue}\n> 合成但無來源的法條文字。"
    result = checked(text)
    assert "第999條" not in result["text"]
    assert "合成但無來源" not in result["text"]
    assert "無法確認或提供其條文原文" in result["text"]
    assert result["source_ids"] == []


def test_unsupported_cross_reference_does_not_erase_supported_article():
    evidence = source("work/32-1", "性別平等工作法", "32-1")
    text = "依《性別平等工作法》第32條之1條文，申訴期限為二年。另依同法第2條辦理。"
    result = checked(text, [evidence], {"work/32-1": "合成條文：申訴期限為二年。"})
    assert "依《性別平等工作法》第32條之1條文，申訴期限為二年。" in result["text"]
    assert "第2條" not in result["text"]
    assert "條號目前沒有可核對的官方來源" in result["text"]
    assert result["source_ids"] == ["work/32-1"]


def test_unsupported_cross_reference_with_purported_quote_still_discards_unit():
    evidence = source("work/32-1", "性別平等工作法", "32-1")
    text = "依《性別平等工作法》第32條之1可評估。另依同法第2條條文如下：\n> 未經證實的合成條文。"
    result = checked(text, [evidence])
    assert result["text"] == "目前沒有可核對該條號與條文內容的官方來源，無法確認或提供其條文原文。"
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
    assert "條號目前沒有可核對的官方來源" in results[0]
    assert "期限目前沒有可核對的官方來源" in results[1]
    assert "你可以先休息。" in results[1]
    assert "期限目前沒有可核對的官方來源" in results[2]
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
    assert "期限目前沒有可核對的官方來源" in result["text"]


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
        "《測試法》目前版本無法核對，這項期限目前沒有可核對的官方來源。\n可先保存相關訊息。"
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
    assert "這項期限目前沒有可核對的官方來源" in result["text"]
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


def test_private_organization_referral_is_removed_without_discarding_official_direction():
    result = checked("可先保留訊息。你可以聯絡某某基金會求助。也可向地方主管機關詢問。")
    assert result["text"] == "可先保留訊息。也可向地方主管機關詢問。"


def test_corrected_contact_requires_checked_official_source():
    unit = SimpleNamespace(text="可撥打 08-1234567 求助。", source_ids=[])
    result = check_answer_units([unit], [], "你說的電話錯了，應該是 08-1234567。", documents=[])[0]
    assert "08-1234567" not in result["text"]
    assert "電話待核對" in result["text"]
    assert "您說的這個我無法查證" in result["text"]


def test_masked_declarative_government_phone_claim_without_source_gets_verification_limit():
    unit = SimpleNamespace(
        kind="answer",
        text="抱歉，我無法提供電話。可撥 113、110 或 119。",
        source_ids=[],
    )
    diagnostics = {}
    seen_reasons = set()
    result = check_answer_units(
        [unit],
        [],
        "高雄市府總機電話是[電話號碼]",
        documents=[],
        diagnostics=diagnostics,
        seen_reasons=seen_reasons,
    )
    assert result[0]["text"].startswith("我知道的資訊是：")
    assert "您說的這個我無法查證" in result[0]["text"]
    assert "113" not in result[0]["text"]
    assert result[0]["source_ids"] == []
    assert diagnostics["rewrites"][0]["reasons"] == ["unverified_masked_official_phone"]
    assert (
        check_answer_units(
            [unit], [], "高雄市府總機電話是[電話號碼]", documents=[], seen_reasons=seen_reasons
        )
        == []
    )


def test_masked_government_phone_claim_preserves_three_section_shape():
    units = [
        SimpleNamespace(kind=kind, text="抱歉，無法提供電話。", source_ids=[])
        for kind in ("direction", "basis", "next_steps")
    ]
    result = check_answer_units(units, [], "高雄市府總機電話是[電話號碼]", documents=[])
    assert [item["kind"] for item in result] == ["direction", "basis", "next_steps"]
    assert "我知道的資訊是" in result[0]["text"]
    assert "官方來源" in result[1]["text"]
    assert "官方網站" in result[2]["text"]
    assert all(item["source_ids"] == [] for item in result)


def test_masked_government_phone_claim_keeps_documented_official_answer():
    official = {
        "doc_id": "official/test-city",
        "label": "測試市政府聯絡方式",
        "source_url": "https://test.example.gov.tw/contact",
        "checked_at": "2026-11-10",
    }
    text = "我知道的資訊是測試市政府官網公告 02-12345678；您說的這個我無法查證。"
    unit = SimpleNamespace(kind="answer", text=text, source_ids=[official["doc_id"]])
    result = check_answer_units(
        [unit],
        [official],
        "測試市府總機電話是[電話號碼]",
        documents=[{"source": official, "content": "測試市政府總機電話 02-12345678。"}],
    )[0]
    assert result["text"] == text
    assert result["source_ids"] == [official["doc_id"]]


def test_masked_personal_phone_statement_does_not_trigger_official_contact_reply():
    unit = SimpleNamespace(kind="answer", text="請勿公開個人聯絡資訊。", source_ids=[])
    result = check_answer_units([unit], [], "我的電話是[電話號碼]", documents=[])[0]
    assert result["text"] == unit.text


def test_verified_short_hotline_survives_contact_correction_check():
    official = {
        "doc_id": "official/113",
        "source_url": "https://dep.mohw.gov.tw/DOPs/cp-1183-6499-105.html",
        "checked_at": "2026-11-10",
    }
    unit = SimpleNamespace(text="113 保護專線提供 24 小時服務。", source_ids=["official/113"])
    result = check_answer_units(
        [unit],
        [official],
        "你說的電話不對，113 是正確的嗎？",
        documents=[{"source": official, "content": "113 保護專線 24 小時全年無休。"}],
    )[0]
    assert result["text"] == unit.text
    assert result["source_ids"] == ["official/113"]


def test_verified_markdown_official_url_survives_contact_correction_check():
    url = "https://www.pthg.gov.tw/"
    official = {"doc_id": "official/site", "source_url": url, "checked_at": "2026-11-10"}
    text = f"可查看[屏東縣政府網站]({url})。"
    result = check_answer_units(
        [SimpleNamespace(text=text, source_ids=["official/site"])],
        [official],
        "你提供的網址有錯嗎？",
        documents=[{"source": official, "content": "屏東縣政府入口"}],
    )[0]
    assert result["text"] == text
    assert "一至" not in result["text"]


@pytest.mark.parametrize(
    ("claim", "expected", "old_gloss"),
    [
        (
            "- **非權勢者**（即非主管或高階管理者）於知悉後二年內申訴。",
            "被申訴人非具權勢地位時",
            "非主管或高階管理者",
        ),
        (
            "- **具權勢者**（具主管或高階管理者身分）於知悉後三年內申訴。",
            "被申訴人具權勢地位時",
            "主管或高階管理者",
        ),
        (
            "- 申訴人為非具權勢地位者，期限為二年。",
            "被申訴人非具權勢地位時",
            "申訴人為非具權勢地位",
        ),
        (
            "- 受害人具權勢地位者，期限為三年。",
            "被申訴人具權勢地位時",
            "受害人具權勢地位",
        ),
    ],
)
def test_reviewed_workplace_deadline_actor_is_the_respondent(claim, expected, old_gloss):
    evidence = reviewed_workplace_source()
    diagnostics = {}
    result = checked(claim, [evidence], diagnostics=diagnostics)
    assert expected in result["text"]
    assert old_gloss not in result["text"]
    assert result["source_ids"] == [evidence["doc_id"]]
    assert diagnostics["rewrites"][0]["reasons"] == ["workplace_deadline_actor_reversed"]


def test_reviewed_workplace_employer_processing_has_no_added_time_qualification():
    evidence = reviewed_workplace_source()
    diagnostics = {}
    result = checked(
        "雇主未於合理時間內處理時，得向地方主管機關申訴。",
        [evidence],
        diagnostics=diagnostics,
    )
    assert result["text"] == "雇主未處理時，得向地方主管機關申訴。"
    assert result["source_ids"] == [evidence["doc_id"]]
    assert diagnostics["rewrites"][0]["reasons"] == ["unsupported_workplace_processing_qualifier"]


def test_reviewed_workplace_longer_period_is_only_for_two_exceptions():
    evidence = reviewed_workplace_source()
    diagnostics = {}
    result = checked(
        "- 較長的期限優先適用，即只要符合較長的期限，即可受理。",
        [evidence],
        diagnostics=diagnostics,
    )
    assert "僅在申訴人於事件時未成年" in result["text"]
    assert "或被申訴人為最高負責人或僱用人" in result["text"]
    assert "只要符合較長的期限" not in result["text"]
    assert result["source_ids"] == [evidence["doc_id"]]
    assert diagnostics["rewrites"][0]["reasons"] == ["workplace_longer_period_overgeneralized"]


def test_reviewed_workplace_correction_does_not_apply_to_other_version():
    evidence = reviewed_workplace_source()
    evidence["version"] = "N0030014@future-review"
    claim = "雇主未於合理時間內處理時，得向地方主管機關申訴。"
    diagnostics = {}
    result = checked(claim, [evidence], diagnostics=diagnostics)
    assert result["text"] == claim
    assert not diagnostics.get("rewrites")


def test_reviewed_workplace_supported_actor_and_exception_scope_stay_intact():
    evidence = reviewed_workplace_source()
    claim = (
        "被申訴人非具權勢地位時，自知悉性騷擾時起逾二年提起者不予受理。"
        "未成年特別情形下若一般期限較長，從一般期限。"
    )
    diagnostics = {}
    result = checked(claim, [evidence], diagnostics=diagnostics)
    assert result["text"] == claim
    assert not diagnostics.get("rewrites")


def test_reviewed_workplace_live_deadline_anchors_route_and_exception_are_repaired():
    evidence = reviewed_workplace_source()
    content = (
        "被申訴人非具權勢地位，自知悉性騷擾時起逾二年不予受理，"
        "自該行為終了時起逾五年亦同。被申訴人具權勢地位，"
        "自知悉性騷擾時起逾三年不予受理，自該行為終了時起逾七年亦同。"
        "只有兩個特別情形不受前述一般期限限制；若一般期限較長，從一般期限。"
    )
    claim = (
        "根據《性別平等工作法》第32條之1，受僱者或求職者在遭受性騷擾後，"
        "應先向雇主提出申訴。"
        "自受害人知悉性騷擾事實之日起 2 年內，或自工作關係終了（如離職）之日起 5 年內。"
        "自知悉之日起 3 年內，或自工作關係終了之日起 7 年內。"
        "這兩項例外是唯一不受前述一般期限限制的情形。"
    )
    message = (
        "請依現行《性別平等工作法》第32-1條，說明向地方主管機關申訴的情形、"
        "權勢地位是指誰，以及較長申訴期限適用哪些例外。請附官方法源。"
    )
    diagnostics = {}
    unit = SimpleNamespace(text=claim, source_ids=[evidence["doc_id"]])
    result = check_answer_units(
        [unit],
        [evidence],
        message,
        documents=[{"source": evidence, "content": content}],
        diagnostics=diagnostics,
    )[0]
    assert "應先向雇主" not in result["text"]
    assert "得逕向地方主管機關" in result["text"]
    assert "工作關係終了" not in result["text"]
    assert "自該行為終了時起逾五年" in result["text"]
    assert "自該行為終了時起逾七年" in result["text"]
    assert "被申訴人非具權勢地位" in result["text"]
    assert "被申訴人具權勢地位" in result["text"]
    assert "但依第二項規定有較長申訴期限者" in result["text"]
    assert evidence["source_url"] in result["text"]
    assert result["source_ids"] == [evidence["doc_id"]]
    assert {reason for row in diagnostics["rewrites"] for reason in row["reasons"]} == {
        "workplace_employer_first_overstated",
        "workplace_deadline_anchor_or_disjunction",
        "workplace_exception_longer_limit_omitted",
    }


def test_reviewed_workplace_correct_deadline_pair_keeps_original_wording():
    evidence = reviewed_workplace_source()
    claim = "被申訴人非具權勢地位，知悉後二年內且行為終了後五年內向地方主管機關申訴。"
    diagnostics = {}
    result = checked(
        claim,
        [evidence],
        {evidence["doc_id"]: "被申訴人非具權勢地位，知悉後二年且行為終了後五年。"},
        diagnostics=diagnostics,
    )
    assert result["text"] == claim
    assert not diagnostics.get("rewrites")


def test_reviewed_workplace_wrong_anchor_guard_requires_exact_reviewed_version():
    evidence = reviewed_workplace_source()
    evidence["version"] = "N0030014@future-review"
    claim = "知悉後2年內，或自工作關係終了之日起5年內。"
    diagnostics = {}
    result = checked(
        claim,
        [evidence],
        {evidence["doc_id"]: "申訴期限是知悉後2年、離職後5年。"},
        diagnostics=diagnostics,
    )
    assert result["text"] == claim
    assert not diagnostics.get("rewrites")


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("被申訴人非具權勢地位，自勞動契約終止之日起五年內申訴。", "自該行為終了時起逾五年"),
        ("被申訴人非具權勢地位，自離開職場之日起五年內申訴。", "自該行為終了時起逾五年"),
        ("被申訴人具權勢地位，自僱傭契約終止之日起七年內申訴。", "自該行為終了時起逾七年"),
        ("被申訴人具權勢地位，自離開公司之日起七年內申訴。", "自該行為終了時起逾七年"),
    ],
)
def test_reviewed_workplace_contract_or_departure_anchor_is_repaired(claim, expected):
    evidence = reviewed_workplace_source()
    diagnostics = {}
    result = checked(
        claim,
        [evidence],
        {evidence["doc_id"]: "自知悉性騷擾時起逾二年或三年；自該行為終了時起逾五年或七年。"},
        diagnostics=diagnostics,
    )
    assert expected in result["text"]
    assert "契約終止" not in result["text"]
    assert "離開職場" not in result["text"]
    assert "離開公司" not in result["text"]
    assert result["source_ids"] == [evidence["doc_id"]]
    assert diagnostics["rewrites"][0]["reasons"] == ["workplace_deadline_anchor_or_disjunction"]


def test_reviewed_workplace_slash_shorthand_fixes_both_end_anchors():
    evidence = reviewed_workplace_source()
    result = checked(
        "自工作關係終了起 5/7 年內申訴。",
        [evidence],
        {evidence["doc_id"]: "自知悉時起二年或三年；自行為終了時起五年或七年。"},
    )
    assert "工作關係終了" not in result["text"]
    assert "自該行為終了時起逾五年" in result["text"]
    assert "自該行為終了時起逾七年" in result["text"]


def test_reviewed_workplace_full_markdown_keeps_both_deadline_branches():
    evidence = reviewed_workplace_source()
    claim = (
        "**權勢地位**：具決策或管理權限，包括最高負責人、雇主本人、主管、其他實質影響力者。\n"
        "**向地方主管機關申訴的期限**\n"
        "- **被申訴人未具權勢地位**：自受害人知悉性騷擾事實之日起 2 年內，"
        "或自工作關係終了（如離職）之日起 5 年內。\n"
        "- **被申訴人具權勢地位**：自知悉之日起 3 年內，"
        "或自工作關係終了之日起 7 年內。"
    )
    content = (
        "被申訴人非具權勢地位，自知悉性騷擾時起逾二年不予受理，"
        "自該行為終了時起逾五年亦同。被申訴人具權勢地位，"
        "自知悉性騷擾時起逾三年不予受理，自該行為終了時起逾七年亦同。"
    )
    result = checked(claim, [evidence], {evidence["doc_id"]: content})
    assert "決策或管理權限" not in result["text"]
    assert "其他實質影響力者" not in result["text"]
    assert "期限按被申訴人是否具權勢地位區分" in result["text"]
    assert "工作關係終了" not in result["text"]
    assert "- 依性別平等工作法第32條之1，被申訴人非具權勢地位時" in result["text"]
    assert "自該行為終了時起逾五年" in result["text"]
    assert "- 依性別平等工作法第32條之1，被申訴人具權勢地位時" in result["text"]
    assert "自該行為終了時起逾七年" in result["text"]
    assert result["source_ids"] == [evidence["doc_id"]]


def test_reviewed_workplace_power_gloss_guard_keeps_case_fact_wording():
    evidence = reviewed_workplace_source()
    claim = "對方是有管理權限的主管，我擔心遭報復；仍須核對被申訴人是否具權勢地位。"
    diagnostics = {}
    result = checked(claim, [evidence], diagnostics=diagnostics)
    assert result["text"] == claim
    assert not diagnostics.get("rewrites")


def test_reviewed_workplace_full_markdown_definition_block_is_bounded():
    evidence = reviewed_workplace_source()
    claim = (
        "### 「權勢地位」的定義\n"
        "在本條文中，具「權勢地位」者指在工作場所具決策或管理權限的對象，包括但不限於：\n"
        "- 最高負責人或雇主本人\n"
        "- 直接主管或管理者\n"
        "- 其他對工作條件有實質影響力者\n"
        "\n"
        "### 申訴期限\n"
        "- **被申訴人未具權勢地位**：自知悉之日起 2 年內，"
        "或自工作關係終了之日起 5 年內。\n"
        "- **被申訴人具權勢地位**：自知悉之日起 3 年內，"
        "或自工作關係終了之日起 7 年內。\n"
        "\n"
        "主管有管理權限，我擔心報復。"
    )
    content = (
        "被申訴人非具權勢地位，自知悉時起逾二年、自該行為終了時起逾五年不予受理。"
        "被申訴人具權勢地位，自知悉時起逾三年、自該行為終了時起逾七年不予受理。"
    )
    diagnostics = {}
    result = checked(claim, [evidence], {evidence["doc_id"]: content}, diagnostics=diagnostics)
    assert "包括但不限於" not in result["text"]
    assert "直接主管或管理者" not in result["text"]
    assert "其他對工作條件有實質影響力者" not in result["text"]
    assert result["text"].count("具體認定須結合相關法條與事件事實核對") == 1
    assert "### 申訴期限" in result["text"]
    assert "自該行為終了時起逾五年" in result["text"]
    assert "自該行為終了時起逾七年" in result["text"]
    assert "主管有管理權限，我擔心報復。" in result["text"]
    assert result["source_ids"] == [evidence["doc_id"]]
    assert "workplace_power_status_unsupported_definition_block" in {
        reason for row in diagnostics["rewrites"] for reason in row["reasons"]
    }


def test_reviewed_workplace_false_power_equivalence_and_first_route_are_repaired():
    evidence = reviewed_workplace_source()
    claim = (
        "**「具權勢地位」的意涵**\n在同條文中，具權勢地位指的是"
        "**被申訴人為最高負責人或僱用人**（即公司最高主管或直接雇用受害人的單位負責人）。"
        "首先應向雇主提出申訴。"
    )
    diagnostics = {}
    result = checked(claim, [evidence], diagnostics=diagnostics)
    assert "具權勢地位指的是被申訴人為最高負責人" not in result["text"]
    assert "即公司最高主管" not in result["text"]
    assert "另是得逕向地方主管機關申訴及適用特別期限的條件" in result["text"]
    assert "首先應向雇主" not in result["text"]
    assert "得逕向地方主管機關提起申訴" in result["text"]
    assert {reason for row in diagnostics["rewrites"] for reason in row["reasons"]} == {
        "workplace_power_status_false_equivalence",
        "workplace_employer_first_overstated",
    }


def test_direct_public_workplace_question_uses_one_reviewed_canonical_answer():
    evidence = reviewed_workplace_source()
    message = (
        "請依現行《性別平等工作法》第32-1條，說明向地方主管機關申訴的情形、"
        "權勢地位是指誰，以及較長申訴期限適用哪些例外。請附官方法源。"
    )
    content = "被申訴人非具權勢地位，自該行為終了時起逾五年；被申訴人具權勢地位，逾七年。"
    seen_reasons = set()
    first = check_answer_units(
        [SimpleNamespace(text="首先應向雇主申訴。", source_ids=[])],
        [evidence],
        message,
        documents=[{"source": evidence, "content": content}],
        seen_reasons=seen_reasons,
        allow_canonical_public_law=True,
    )
    second = check_answer_units(
        [SimpleNamespace(text="最高負責人就是具權勢地位。", source_ids=[])],
        [evidence],
        message,
        documents=[{"source": evidence, "content": content}],
        seen_reasons=seen_reasons,
        allow_canonical_public_law=True,
    )
    assert len(first) == 1 and second == []
    answer = first[0]
    assert "應先" not in answer["text"] and "首先" not in answer["text"]
    assert "另是本條的直接申訴及特別期限條件" in answer["text"]
    assert "自該行為終了時起逾五年" in answer["text"]
    assert "自該行為終了時起逾七年" in answer["text"]
    assert "若依前述一般規定有較長申訴期限" in answer["text"]
    assert evidence["source_url"] in answer["text"]
    assert answer["source_ids"] == [evidence["doc_id"]]


def test_case_specific_workplace_message_skips_canonical_answer():
    evidence = reviewed_workplace_source()
    message = (
        "我被主管騷擾，請依《性別平等工作法》第32-1條說明向地方主管機關申訴、權勢地位與較長期限。"
    )
    claim = "對方是我的主管，我擔心報復。"
    result = check_answer_units(
        [SimpleNamespace(text=claim, source_ids=[evidence["doc_id"]])],
        [evidence],
        message,
        documents=[{"source": evidence, "content": "合成法源摘要。"}],
        allow_canonical_public_law=True,
    )[0]
    assert result["text"] == claim


def test_reviewed_workplace_official_link_appears_once_across_answer_units():
    evidence = reviewed_workplace_source()
    units = [
        SimpleNamespace(text="依性別平等工作法第32條之1可評估。", source_ids=[evidence["doc_id"]]),
        SimpleNamespace(text="可向地方主管機關詢問。", source_ids=[evidence["doc_id"]]),
    ]
    results = check_answer_units(
        units,
        [evidence],
        "請附官方法源。",
        documents=[{"source": evidence, "content": "合成法源摘要。"}],
        seen_reasons=set(),
    )
    assert sum(evidence["source_url"] in result["text"] for result in results) == 1
