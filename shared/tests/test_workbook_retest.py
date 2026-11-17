"""The workbook replay refuses drift and cannot silently send live cases."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from scripts.quality import retest_workbook_cases as retest
from scripts.quality.retest_workbook_cases import (
    EXPECTED_CASES,
    EXPECTED_TURNS,
    RetestError,
    load_cases,
    replay,
)


def synthetic_workbook(path: Path, *, broken_turn: bool = False) -> None:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "案例與結果"
    dialogue = workbook.create_sheet("逐輪對話")
    summary.append([])
    dialogue.append([])
    for _ in range(3):
        summary.append([])
        dialogue.append([])
    summary.append(
        ["紀錄編號", "測試類別", "測試版本", "測試結果", "系統問題摘要", "工程師\n處理狀態"]
    )
    dialogue.append(["紀錄編號", "輪次", "實際提問"])
    for index, case_id in enumerate(sorted(EXPECTED_CASES)):
        summary.append(
            [case_id, "合成測試", "測試版", "部分通過", "", "待確認" if index < 5 else "待修正"]
        )
        for number in range(1, EXPECTED_TURNS[case_id] + 1):
            dialogue.append(
                [
                    case_id,
                    number + 1 if broken_turn and index == 0 and number == 1 else number,
                    "合成測試訊息",
                ]
            )
    workbook.save(path)


def test_pending_workbook_case_and_turn_contract(tmp_path):
    path = tmp_path / "synthetic.xlsx"
    synthetic_workbook(path)
    cases = load_cases(path)
    assert len(cases) == 15
    assert sum(len(case.turns) for case in cases) == 43
    assert sum(case.status == "待確認" for case in cases) == 5

    synthetic_workbook(path, broken_turn=True)
    with pytest.raises(RetestError, match="dialogue_turns_incomplete"):
        load_cases(path)


def test_live_replay_requires_interactive_review_before_network(tmp_path):
    with pytest.raises(RetestError, match="live_replay_requires_interactive_terminal"):
        replay(
            tmp_path / "missing.xlsx",
            (),
            tmp_path,
            api_url="https://example.test/api/v1/admin/test-jobs",
            password_file=tmp_path / "missing.password",
            origin=None,
            repetitions=1,
            selected_ids=set(),
        )


def test_gate_trace_accepts_post_denial_then_short_refusal_review():
    pre = {"stage": "pre", "decision": "allow"}
    denied = {"stage": "post", "decision": "deny", "reason_code": "technical_implementation"}
    refusal = {"stage": "post", "decision": "allow", "reason_code": "allowed"}
    assert retest._valid_gate_trace([pre, denied, refusal])
    assert retest._valid_gate_trace([pre, denied])
    assert not retest._valid_gate_trace([pre])
    assert not retest._valid_gate_trace([pre, refusal, refusal])


def test_synthetic_replay_uses_approved_turns_and_records_only_metadata(tmp_path, monkeypatch):
    class FakePrivacyBridge:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def review(self, request, *_):
            return {"request": request, "findings": []}

    monkeypatch.setattr(retest, "PrivacyBridge", FakePrivacyBridge)
    workbook = tmp_path / "synthetic.xlsx"
    synthetic_workbook(workbook)
    cases = load_cases(workbook)
    run_dir = tmp_path / "review"
    retest.prepare(workbook, cases, run_dir, ())
    review_path = run_dir / "outbound_review.json"
    review = json.loads(review_path.read_text())
    for case in review["cases"]:
        for turn in case["turns"]:
            turn["approved"] = True
    review_path.write_text(json.dumps(review))

    password_file = tmp_path / "password"
    password_file.write_text("synthetic-secret-only-for-test")
    password_file.chmod(0o600)
    monkeypatch.setattr(retest.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(retest, "login", lambda *_: "synthetic-csrf")
    monkeypatch.setattr(
        retest, "_fetch_config", lambda *_: ({"gate_enabled": True}, "revision-1", "a" * 64)
    )
    reviewed = []
    monkeypatch.setattr(
        retest, "_confirm_request", lambda _dir, _id, _rep, _turn, request: reviewed.append(request)
    )
    sent = []

    def fake_submit(_client, _url, _csrf, body, *, on_submitted=None):
        sent.append(body)
        on_submitted("a" * 32)
        return {
            "state": "completed",
            "job_id": "a" * 32,
            "data": {
                "response": {
                    "reply": "合成回答",
                    "interaction_mode": "answer",
                    "rag_used": {"sources": [{"doc_id": "official_laws/law-2"}]},
                },
                "diagnostics": {
                    "gate_reviews": [
                        {
                            "stage": "pre",
                            "decision": "allow",
                            "reason_code": "allowed",
                            "duration_ms": 1,
                        },
                        {
                            "stage": "post",
                            "decision": "allow",
                            "reason_code": "allowed",
                            "duration_ms": 2,
                        },
                    ]
                },
            },
        }

    monkeypatch.setattr(retest, "_submit_job", fake_submit)
    selected = cases[0]
    retest.replay(
        workbook,
        cases,
        run_dir,
        api_url="https://example.test/api/v1/admin/test-jobs",
        password_file=password_file,
        origin=None,
        repetitions=1,
        selected_ids={selected.case_id},
    )
    assert len(sent) == EXPECTED_TURNS[selected.case_id]
    assert [len(request["history"]) for request in reviewed] == list(range(0, len(sent) * 2, 2))
    results_text = (run_dir / "results.jsonl").read_text()
    assert "合成測試訊息" not in results_text
    records = [json.loads(line) for line in results_text.splitlines()]
    assert [record["state"] for record in records] == [
        state for _ in sent for state in ("started", "completed")
    ]
    assert all(
        record.get("source_ids") == ["official_laws/law-2"]
        for record in records
        if record["state"] == "completed"
    )
    receipts = [
        json.loads(line) for line in (run_dir / "job_receipts.jsonl").read_text().splitlines()
    ]
    assert len(receipts) == len(sent)
    assert all(row["job_id"] == "a" * 32 and "message" not in row for row in receipts)


def test_uncertain_external_outcome_stops_remaining_repetitions(tmp_path, monkeypatch):
    class FakePrivacyBridge:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def review(self, request, *_):
            return {"request": request, "findings": []}

    monkeypatch.setattr(retest, "PrivacyBridge", FakePrivacyBridge)
    workbook = tmp_path / "synthetic.xlsx"
    synthetic_workbook(workbook)
    cases = load_cases(workbook)
    run_dir = tmp_path / "review"
    retest.prepare(workbook, cases, run_dir, ())
    review_path = run_dir / "outbound_review.json"
    review = json.loads(review_path.read_text())
    for case in review["cases"]:
        for turn in case["turns"]:
            turn["approved"] = True
    review_path.write_text(json.dumps(review))
    password_file = tmp_path / "password"
    password_file.write_text("synthetic-secret-only-for-test")
    password_file.chmod(0o600)
    monkeypatch.setattr(retest.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(retest, "login", lambda *_: "synthetic-csrf")
    monkeypatch.setattr(
        retest, "_fetch_config", lambda *_: ({"gate_enabled": True}, "revision-1", "a" * 64)
    )
    monkeypatch.setattr(retest, "_confirm_request", lambda *_: None)
    sent = []

    def uncertain(*_, on_submitted=None):
        sent.append(True)
        on_submitted("b" * 32)
        return {"state": "error", "error_code": "external_outcome_unknown"}

    monkeypatch.setattr(retest, "_submit_job", uncertain)
    selected = cases[0]
    with pytest.raises(RetestError, match="live_replay_stopped_after_error"):
        retest.replay(
            workbook,
            cases,
            run_dir,
            api_url="https://example.test/api/v1/admin/test-jobs",
            password_file=password_file,
            origin=None,
            repetitions=2,
            selected_ids={selected.case_id},
        )
    assert len(sent) == 1
    assert not (run_dir / f"{selected.case_id}-2.started").exists()
    assert [
        json.loads(line)["state"] for line in (run_dir / "results.jsonl").read_text().splitlines()
    ] == [
        "started",
        "error",
    ]
    assert (
        json.loads((run_dir / "job_receipts.jsonl").read_text().splitlines()[0])["job_id"]
        == "b" * 32
    )
