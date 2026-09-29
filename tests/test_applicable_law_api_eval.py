"""Tests for the standalone applicable-law API reliability evaluator."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx

from tests.reliability.applicable_law_api_eval import (
    ChatApiError,
    EvaluationCase,
    build_question,
    call_chat_api,
    cases_from_workbook,
    evaluate_reply,
    extract_label_laws,
    extract_laws,
    summarize,
)


def _case(*, required: tuple[str, ...], primary: tuple[str, ...] | None = None):
    primary_laws = primary or required[:1]
    secondary_laws = tuple(law for law in required if law not in primary_laws)
    return EvaluationCase(
        case_id="gemini:工作表1:4",
        source_file=Path("resource/gemini_scene.xlsx"),
        sheet="工作表1",
        excel_row=4,
        scenario="場景：測試情境，不含任何答案標籤。",
        scenario_columns=("場景",),
        scenario_sha256="abc123",
        primary_label_raw="性工法",
        secondary_label_raw=("亦涉刑法",) if secondary_laws else (),
        primary_laws=primary_laws,
        secondary_laws=secondary_laws,
        required_laws=required,
        compound=len(required) > 1,
        contains_yishe=bool(secondary_laws),
        conditional=False,
        gold_warnings=(),
    )


def _write_minimal_gemini_xlsx(path: Path) -> None:
    shared_strings = ["適用法規", "場景", "性工法", "這是一筆測試場景", "亦涉刑法"]
    shared_xml = "".join(f"<si><t>{value}</t></si>" for value in shared_strings)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="工作表1" sheetId="1" r:id="rId1"/></sheets>
            </workbook>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="worksheets/sheet1.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              count="5" uniqueCount="5">{shared_xml}</sst>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>
                </row>
                <row r="2">
                  <c r="A2" t="s"><v>2</v></c><c r="B2" t="s"><v>3</v></c>
                  <c r="E2" t="s"><v>4</v></c>
                </row>
              </sheetData>
            </worksheet>""",
        )


def test_extract_laws_normalizes_short_names_fullwidth_and_missing_separator():
    assert extract_laws("性工法", "亦涉刑法性影像") == ("性別平等工作法", "刑法")
    assert extract_laws("性平法（亦涉跟騷、刑法）") == (
        "性別平等教育法",
        "跟蹤騷擾防制法",
        "刑法",
    )
    assert extract_laws("亦涉個人資料保護法") == ("個人資料保護法",)


def test_gold_parser_handles_truncated_tokens_without_weakening_reply_matching():
    assert extract_label_laws("適用性騷，亦涉刑法、性工雇主義務") == (
        "刑法",
        "性別平等工作法",
        "性騷擾防治法",
    )
    assert extract_laws("這是性騷行為") == ()


def test_read_profile_combines_primary_and_unlabeled_secondary_column(tmp_path):
    workbook = tmp_path / "gemini_scene.xlsx"
    _write_minimal_gemini_xlsx(workbook)

    cases = cases_from_workbook(workbook)

    assert len(cases) == 1
    assert cases[0].case_id == "gemini:工作表1:2"
    assert cases[0].primary_laws == ("性別平等工作法",)
    assert cases[0].secondary_label_raw == ("亦涉刑法",)
    assert cases[0].required_laws == ("性別平等工作法", "刑法")
    assert cases[0].compound is True


def test_question_contains_scenario_but_never_gold_labels():
    case = _case(required=("性別平等工作法", "刑法"))

    question = build_question(case)

    assert case.scenario in question
    assert "性工法" not in question
    assert "亦涉刑法" not in question
    assert "刑法" not in question
    assert "所有可能涉及的法規" in question


def test_compound_case_requires_every_law_and_rejects_negated_mention():
    case = _case(required=("性別平等工作法", "刑法"))

    evaluation = evaluate_reply(case, "本案適用性別平等工作法，但不涉及刑法。")

    assert evaluation.primary_pass is True
    assert evaluation.matched_laws == ("性別平等工作法",)
    assert evaluation.missing_laws == ("刑法",)
    assert evaluation.negated_laws == ("刑法",)
    assert evaluation.compound_pass is False
    assert evaluation.coverage_pass is False


def test_legacy_alias_passes_but_extra_law_only_breaks_strict_exact():
    case = _case(required=("性別平等工作法", "刑法"))

    evaluation = evaluate_reply(
        case,
        "適用舊稱性別工作平等法，並涉及中華民國刑法；另可能涉及性騷擾防治法。",
    )

    assert evaluation.matched_laws == ("性別平等工作法", "刑法")
    assert evaluation.extra_laws == ("性騷擾防治法",)
    assert evaluation.coverage_pass is True
    assert evaluation.compound_pass is True
    assert evaluation.strict_exact_pass is False


def test_polarity_conflict_and_clarification_do_not_pass():
    case = _case(required=("刑法",), primary=("刑法",))

    conflict = evaluate_reply(case, "可能適用刑法，但也有意見認為不適用刑法。")
    clarification = evaluate_reply(case, "刑法", interaction_mode="clarify")

    assert conflict.polarity_conflicts == ("刑法",)
    assert conflict.coverage_pass is False
    assert clarification.coverage_pass is False


def test_chat_api_retries_429_and_preserves_attempt_trail():
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload == {"message": "測試問題", "history": [], "use_rag": True}
        assert request.headers["X-Firebase-AppCheck"] == "app-check-token"
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0.25"},
                json={"detail": "Too many chat requests", "retryable": True},
            )
        return httpx.Response(200, json={"reply": "適用性別平等工作法"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload, status, events, total_latency = call_chat_api(
            client,
            api_url="https://example.test/v1/chat/",
            question="測試問題",
            use_rag=True,
            app_check_token="app-check-token",
            retries=1,
            backoff_seconds=1,
            max_retry_wait=5,
            sleep=waits.append,
        )

    assert status == 200
    assert payload["reply"] == "適用性別平等工作法"
    assert [event["http_status"] for event in events] == [429, 200]
    assert events[0]["retry_wait_seconds"] == 0.25
    assert waits == [0.25]
    assert total_latency >= 0


def test_chat_api_does_not_retry_permanent_401():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid App Check token"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            call_chat_api(
                client,
                api_url="https://example.test/v1/chat/",
                question="測試問題",
                use_rag=True,
                app_check_token=None,
                retries=2,
                backoff_seconds=0,
                max_retry_wait=0,
            )
        except ChatApiError as exc:
            assert len(exc.events) == 1
            assert exc.events[0]["http_status"] == 401
        else:
            raise AssertionError("Expected ChatApiError")


def test_summary_counts_api_errors_in_end_to_end_denominator():
    records = [
        {
            "api": {"ok": True, "total_latency_ms": 100},
            "compound": True,
            "contains_yishe": True,
            "required_laws": ["性別平等工作法", "刑法"],
            "evaluation": {
                "coverage_pass": True,
                "strict_exact_pass": True,
                "primary_pass": True,
                "compound_pass": True,
                "matched_laws": ["性別平等工作法", "刑法"],
                "predicted_laws": ["性別平等工作法", "刑法"],
                "interaction_mode": "answer",
            },
        },
        {
            "api": {"ok": False},
            "compound": True,
            "contains_yishe": True,
            "required_laws": ["性別平等工作法", "刑法"],
            "evaluation": {
                "coverage_pass": False,
                "strict_exact_pass": False,
                "primary_pass": False,
                "compound_pass": False,
                "matched_laws": [],
                "predicted_laws": [],
                "interaction_mode": "api_error",
            },
        },
    ]

    summary = summarize(records, selected_case_count=2)

    assert summary["api_success_rate"] == 0.5
    assert summary["all_required_laws_pass_rate"] == 0.5
    assert summary["compound_all_covered_rate"] == 0.5
    assert summary["law_micro_recall"] == 0.5
