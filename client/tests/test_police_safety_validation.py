"""Local corrections for unsafe police-channel and hiding advice."""

from types import SimpleNamespace

from rag_core.agents.guided_chat import check_answer_units


def checked(text, *, source_ids=None):
    source_ids = source_ids or []
    sources = [{"doc_id": identifier, "label": "合成來源"} for identifier in source_ids]
    diagnostics = {}
    result = check_answer_units(
        [SimpleNamespace(text=text, source_ids=source_ids)],
        sources,
        "合成測試問題",
        documents=[],
        diagnostics=diagnostics,
    )[0]
    return result, diagnostics


def test_police_cannot_be_promised_never_to_mock_or_drop_a_report():
    result, diagnostics = checked(
        "警方不會因為您是首次報案而嘲笑您，亦不會因為證據不足而直接棄案。"
        "若當時缺乏具體證據，警方會協助您保留相關證據，並視情況持續追蹤。",
        source_ids=["fixture/police"],
    )
    assert "警方不會" not in result["text"]
    assert "警方會協助您保留" not in result["text"]
    assert "無法預先保證個別員警的回應" in result["text"]
    assert "警政署民眾服務中心" in result["text"]
    assert "不必為了蒐集證據而增加自身風險" in result["text"]
    assert result["source_ids"] == []
    assert {reason for row in diagnostics["rewrites"] for reason in row["reasons"]} == {
        "police_response_guarantee",
        "police_evidence_guarantee",
    }


def test_110_is_not_described_as_nonemergency_officer_complaint_line():
    result, diagnostics = checked(
        "若對投訴程序不熟悉，可先撥打 **110**（非緊急）詢問投訴窗口的聯絡方式與流程。"
    )
    assert "110（非緊急）" not in result["text"]
    assert "警政署民眾服務中心" in result["text"]
    assert "110是報案或需要警方協助時的電話" in result["text"]
    assert diagnostics["rewrites"][0]["reasons"] == ["police_110_miscast_as_complaint_line"]


def test_hiding_advice_does_not_reveal_position_to_pursuer():
    result, diagnostics = checked(
        "若有其他區域，請大聲呼叫或敲門，讓對方知道你在那裡，並請求協助。"
    )
    assert "讓對方知道你在那裡" not in result["text"]
    assert "不必為了求助而暴露位置" in result["text"]
    assert "向協助者說明所在位置" in result["text"]
    assert diagnostics["rewrites"][0]["reasons"] == ["unsafe_hiding_location_exposure"]


def test_safe_police_reporting_and_helper_location_advice_stay_intact():
    text = (
        "如果感到危險，可撥打110報案，並向場所工作人員說明你的所在位置。"
        "不要讓對方知道你的位置。"
        "撥打110報案後可詢問警察性騷擾申訴窗口。"
    )
    result, diagnostics = checked(text)
    assert result["text"] == text
    assert not diagnostics.get("rewrites")
