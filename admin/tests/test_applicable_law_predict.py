"""Prediction-only transport, privacy, resume, and OOXML preservation tests."""

from __future__ import annotations

import hashlib
import json
import zipfile

import httpx
import pytest

from admin.tests.reliability import applicable_law_predict as predict
from admin.tests.reliability.applicable_law_api_eval import EvaluationInputError, read_xlsx
from admin.tests.reliability.applicable_law_workbook import PREDICTION_HEADER, write_prediction_copy
from shared.tests.fixtures.workbooks import write_scenario_workbook as _xlsx

TEST_CONTRACT_SHA256 = "ab" * 32


@pytest.fixture
def config():
    return {
        "openrouter_provider_sort": "auto",
        "rag_rerank_enabled": False,
        "rag_rerank_model": "cohere/rerank-4-fast",
        "rag_rerank_candidate_k": 20,
        "rag_rerank_timeout_seconds": 1.0,
        "rag_rerank_max_chars_per_doc": 2000,
        "openrouter_model": "synthetic/test",
        "temperature": 0.2,
        "top_p": 1.0,
        "max_tokens": 2000,
        "reasoning_effort": "none",
        "agent_prompt_sections": {"core_mission": "synthetic prompt"},
        "rag_retrieval_top_k": 3,
        "rag_distance_threshold": None,
        "enable_anonymization": True,
        "rag_collections": {
            "law": "synthetic-law",
            "judgment": "synthetic-judgment",
            "remedy": "synthetic-remedy",
        },
        "maintenance_message": None,
        "enable_image_upload": True,
        "enable_client_privacy_review": True,
        "development_mode": False,
        "pipeline": {"enable_analysis": True},
    }


class _Bridge:
    def review(self, request, hidden_terms=()):
        assert set(request) == {"message", "history", "use_rag", "contract_version"}
        assert request["history"] == []
        assert hidden_terms == ()
        return {
            "request": {
                **request,
                "message": request["message"].replace("test@example.test", "[電子郵件]"),
            },
            "findings": [],
        }


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    chatgpt = tmp_path / "chatgpt_scene.xlsx"
    gemini = tmp_path / "gemini_scene.xlsx"
    _xlsx(chatgpt, "Sheet1")
    _xlsx(gemini, "工作表1")
    monkeypatch.setattr(
        predict,
        "PROFILES",
        {
            "chatgpt_scene.xlsx": predict.WorkbookProfile("Sheet1", 3, "F"),
            "gemini_scene.xlsx": predict.WorkbookProfile("工作表1", 3, "G"),
        },
    )
    return chatgpt, gemini


def _response(config, laws=None, status="predicted", retrieval_status="ok"):
    return {
        "output_contract": predict.OUTPUT_CONTRACT,
        "prediction": {
            "predicted_laws": laws if laws is not None else ["性騷法", "性工法"],
            "decision_status": status,
            "decision_reason": None if status == "predicted" else "未提供當事人身分與關係。",
        },
        "diagnostics": {
            predict.CONTRACT_FINGERPRINT_KEY: TEST_CONTRACT_SHA256,
            "effective_config": {**config, "source": "request_override"},
            "retrieval_status": retrieval_status,
            "retrieved_count": 0 if retrieval_status == "empty" else 3,
            "execution": {"model_calls": 2, "model_ms": 3.4, "raw_source": "DO_NOT_STORE"},
            "provider_reasoning": "DO_NOT_STORE",
            "pii_input": "DO_NOT_STORE",
        },
    }


def test_synthetic_workbooks_counts_and_smoke_selection(synthetic_workbooks):
    cases = predict.load_cases(synthetic_workbooks)
    assert len(cases) == 634
    assert sum(case.source.name == "chatgpt_scene.xlsx" for case in cases) == 335
    assert sum(case.source.name == "gemini_scene.xlsx" for case in cases) == 299
    smoke = predict.select_cases(cases, smoke=True)
    assert [(case.source.name, case.excel_row) for case in smoke] == [
        ("chatgpt_scene.xlsx", 2),
        ("chatgpt_scene.xlsx", 169),
        ("chatgpt_scene.xlsx", 336),
        ("gemini_scene.xlsx", 2),
        ("gemini_scene.xlsx", 151),
        ("gemini_scene.xlsx", 300),
    ]


def test_prepare_has_only_scenario_and_complete_approved_envelope(inputs, config):
    cases = predict.load_cases(inputs)
    manifest, previews = predict.prepare_run(
        cases,
        config,
        _Bridge(),
        api_url=predict.DEFAULT_API_URL,
        contract_sha256=TEST_CONTRACT_SHA256,
    )
    serialized = json.dumps(previews, ensure_ascii=False)
    assert "GOLD_SECRET" not in serialized
    assert "NOTE_SECRET" not in serialized
    assert "DUPLICATE_SECRET" not in serialized
    assert "test@example.test" not in serialized
    assert "[電子郵件]" in serialized
    assert len(previews) == manifest["scenario_count"] == 6
    assert all(item["body"]["overrides"] == config for item in previews)
    assert all(
        item["body"][predict.EXPECTED_CONTRACT_FINGERPRINT_KEY] == TEST_CONTRACT_SHA256
        for item in previews
    )
    assert predict.EXPECTED_CONTRACT_FINGERPRINT_KEY not in config
    assert all(item["body"]["request"]["history"] == [] for item in previews)
    assert "情境" not in json.dumps(manifest, ensure_ascii=False)


def test_node_bridge_reuses_local_redaction_and_persistent_approval():
    with predict.PrivacyBridge() as bridge:
        for message in ("聯絡 test@example.test 0912345678", "合成姓名的案件"):
            result = bridge.review(
                {"message": message, "history": [], "use_rag": True, "contract_version": 4},
                ["合成姓名"],
            )
            assert "test@example.test" not in result["request"]["message"]
            assert "0912345678" not in result["request"]["message"]
            assert "合成姓名" not in result["request"]["message"]
        with pytest.raises(predict.PredictionRunError, match="privacy_review_failed"):
            bridge.review({"message": " ", "history": [], "use_rag": True, "contract_version": 4})


def test_expected_contract_is_bound_to_exact_preview_request_hash(inputs, config):
    cases = predict.load_cases(inputs)
    first, original = predict.prepare_run(
        cases,
        config,
        _Bridge(),
        api_url=predict.DEFAULT_API_URL,
        contract_sha256=TEST_CONTRACT_SHA256,
    )
    second, changed = predict.prepare_run(
        cases, config, _Bridge(), api_url=predict.DEFAULT_API_URL, contract_sha256="cd" * 32
    )
    assert first["config_sha256"] == second["config_sha256"]
    assert first["preview_sha256"] != second["preview_sha256"]
    assert all(
        old["request_sha256"] != new["request_sha256"]
        for old, new in zip(original, changed, strict=True)
    )
    for preview in original:
        actual_body = json.dumps(
            preview["body"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        assert hashlib.sha256(actual_body).hexdigest() == preview["request_sha256"]
        assert preview["body"][predict.EXPECTED_CONTRACT_FINGERPRINT_KEY] == TEST_CONTRACT_SHA256


@pytest.mark.parametrize(
    "value",
    [
        {"predicted_laws": ["刑法"], "decision_status": "predicted", "decision_reason": None},
        {
            "predicted_laws": ["性工法", "性工法"],
            "decision_status": "predicted",
            "decision_reason": None,
        },
        {"predicted_laws": [], "decision_status": "predicted", "decision_reason": None},
        {
            "predicted_laws": ["性工法"],
            "decision_status": "insufficient_facts",
            "decision_reason": "未提供當事人身分與關係。",
        },
        {"predicted_laws": [], "decision_status": "clarify"},
        {
            "predicted_laws": [],
            "decision_status": "no_target_law",
            "decision_reason": "未提供當事人身分與關係。",
            "reply": "額外聊天",
        },
        {"predicted_laws": "性工法", "decision_status": "predicted", "decision_reason": None},
    ],
)
def test_prediction_contract_rejects_invalid_values(value):
    with pytest.raises(predict.PredictionFormatError):
        predict.validate_prediction(value)


def test_response_preserves_order_empty_status_and_omits_sensitive_diagnostics(config):
    result, diagnostics = predict.validate_response(_response(config), config, TEST_CONTRACT_SHA256)
    assert result["predicted_laws"] == ["性騷法", "性工法"]
    assert diagnostics == {
        "retrieval_status": "ok",
        "retrieved_count": 3,
        "execution": {"model_calls": 2, "model_ms": 3.4},
    }
    for status in ("insufficient_facts", "no_target_law"):
        result, diagnostic = predict.validate_response(
            _response(config, [], status, "empty"), config, TEST_CONTRACT_SHA256
        )
        assert result == {
            "predicted_laws": [],
            "decision_status": status,
            "decision_reason": "未提供當事人身分與關係。",
        }
        assert diagnostic["retrieval_status"] == "empty"
    with pytest.raises(predict.PredictionFormatError, match="invalid_response_fields"):
        predict.validate_response(
            {**_response(config), "reply": "額外聊天"}, config, TEST_CONTRACT_SHA256
        )
    with pytest.raises(predict.PredictionFormatError, match="invalid_retrieval_status"):
        predict.validate_response(
            _response(config, retrieval_status="failed"), config, TEST_CONTRACT_SHA256
        )
    with pytest.raises(predict.PredictionFormatError, match="effective_config_mismatch"):
        predict.validate_response(
            _response({**config, "temperature": 0.9}), config, TEST_CONTRACT_SHA256
        )
    with pytest.raises(predict.PredictionFormatError, match="contract_fingerprint_mismatch"):
        predict.validate_response(_response(config), config, "cd" * 32)


def test_admin_config_fingerprint_is_read_only_and_required(config):
    snapshots = [
        {**config, predict.CONTRACT_FINGERPRINT_KEY: TEST_CONTRACT_SHA256},
        config,
        {**config, predict.CONTRACT_FINGERPRINT_KEY: "invalid"},
    ]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=snapshots.pop(0)))
    ) as client:
        frozen, fingerprint = predict.get_config(client, predict.DEFAULT_API_URL)
        assert frozen == config and fingerprint == TEST_CONTRACT_SHA256
        assert predict.CONTRACT_FINGERPRINT_KEY not in frozen
        for _ in range(2):
            with pytest.raises(predict.PredictionRunError, match="invalid_contract_fingerprint"):
                predict.get_config(client, predict.DEFAULT_API_URL)


def test_password_login_keeps_cookie_in_memory_and_never_uses_legacy_header():
    requests = []

    def handler(request):
        requests.append(request)
        assert "X-Admin-Token" not in request.headers
        assert request.headers["Origin"] == "https://admin.example.test"
        if request.url.path.endswith("/auth/login"):
            assert json.loads(request.content) == {"password": "synthetic-password-only"}
            return httpx.Response(
                200,
                json={"csrf_token": "synthetic-csrf"},
                headers={
                    "Set-Cookie": "__session=synthetic-cookie; HttpOnly; Secure; SameSite=Strict; Path=/"
                },
            )
        assert request.headers["Cookie"] == "__session=synthetic-cookie"
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        csrf = predict.login(
            client, "https://admin.example.test/api/v1/admin/test-jobs", "synthetic-password-only"
        )
        assert csrf == "synthetic-csrf"
        client.get("https://admin.example.test/api/v1/admin/auth/session")
    assert len(requests) == 2


def test_job_polling_and_retry_after_never_resubmit_a_model_request(config):
    requests, waits = [], []
    replies = [
        httpx.Response(202, json={"job_id": "synthetic-id"}),
        httpx.Response(200, json={"job_id": "synthetic-id", "status": "running"}),
        httpx.Response(429, headers={"Retry-After": "3"}, json={"detail": "Rate limited"}),
        httpx.Response(
            200, json={"job_id": "synthetic-id", "status": "succeeded", "result": _response(config)}
        ),
    ]

    def handler(request):
        requests.append(request)
        assert "X-Admin-Token" not in request.headers
        if request.method == "POST":
            assert request.headers["X-CSRF-Token"] == "synthetic-csrf"
            assert request.headers["Idempotency-Key"]
        else:
            assert request.url.path.endswith("/test-jobs/synthetic-id")
        return replies.pop(0)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = predict.call_prediction(
            client,
            predict.DEFAULT_API_URL,
            "synthetic-csrf",
            {"body": {}},
            config,
            TEST_CONTRACT_SHA256,
            sleep=waits.append,
        )
    assert result["state"] == "completed"
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET"]
    assert waits == [1.0, 3]


def test_http_errors_and_malformed_results_are_not_valid_empty_predictions(config):
    bodies = [
        httpx.Response(503, json={"code": "rag_unavailable", "detail": "PRIVATE_DO_NOT_LOG"}),
        httpx.Response(202, json={"job_id": "synthetic-job-1"}),
        httpx.Response(
            200,
            json={
                "status": "succeeded",
                "result": {**_response(config), "reply": "PRIVATE_DO_NOT_LOG"},
            },
        ),
        httpx.Response(202, json={"job_id": "synthetic-job-2"}),
        httpx.Response(200, content=b"PRIVATE_DO_NOT_LOG"),
    ]
    with httpx.Client(transport=httpx.MockTransport(lambda _request: bodies.pop(0))) as client:
        results = [
            predict.call_prediction(
                client,
                predict.DEFAULT_API_URL,
                "SECRET",
                {"body": {}},
                config,
                TEST_CONTRACT_SHA256,
            )
            for _ in range(3)
        ]
    assert [result["error_code"] for result in results] == [
        "rag_unavailable",
        "invalid_response_fields",
        "invalid_job_response",
    ]
    assert all(result["state"] == "error" and "prediction" not in result for result in results)
    assert "PRIVATE_DO_NOT_LOG" not in json.dumps(results)


def test_writer_preserves_original_cells_namespaces_zip_metadata_and_split_sheet(inputs, tmp_path):
    source = inputs[1]
    destination = tmp_path / "predicted.xlsx"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    write_prediction_copy(
        source, destination, "工作表1", "G", {2: "性騷法、性工法", 3: "", 4: "性平法"}
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination) as copied:
        assert copied.comment == original.comment
        assert copied.namelist() == original.namelist()
        for entry in original.infolist():
            actual = copied.getinfo(entry.filename)
            assert (
                actual.date_time,
                actual.external_attr,
                actual.comment,
                actual.extra,
                actual.compress_type,
            ) == (
                entry.date_time,
                entry.external_attr,
                entry.comment,
                entry.extra,
                entry.compress_type,
            )
            if entry.filename != "xl/worksheets/sheet1.xml":
                assert copied.read(entry.filename) == original.read(entry.filename)
        old_xml, new_xml = (
            original.read("xl/worksheets/sheet1.xml"),
            copied.read("xl/worksheets/sheet1.xml"),
        )
        assert old_xml[: old_xml.index(b"<dimension")] == new_xml[: new_xml.index(b"<dimension")]
        for fragment in (
            b'<c r="A2" s="2" t="inlineStr"><is><t>GOLD_SECRET</t></is></c>',
            b'<c r="B2" s="1" t="inlineStr"><is><t>\xe6\x83\x85\xe5\xa2\x832 test@example.test</t></is></c>',
            b'<extLst><ext uri="preserved"><x14ac:synthetic value="retain"/></ext></extLst>',
        ):
            assert fragment in old_xml and fragment in new_xml
        assert b'min="7" max="7" width="40" customWidth="1"' in new_xml
    copied_rows = dict(read_xlsx(destination)[0].rows)
    assert copied_rows[1][6] == PREDICTION_HEADER
    assert copied_rows[2][6] == "性騷法、性工法"
    assert copied_rows[3][6] == ""


def test_synthetic_workbook_export_preserves_original_hashes_and_other_members(
    tmp_path, synthetic_workbooks
):
    cases = predict.load_cases(synthetic_workbooks)
    for source in synthetic_workbooks:
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        group = [case for case in cases if case.source == source]
        profile = predict.PROFILES[source.name]
        output = tmp_path / f"{source.stem}.predicted.xlsx"
        write_prediction_copy(
            source,
            output,
            profile.sheet,
            profile.prediction_column,
            {
                case.excel_row: "性工法、性騷法、性平法" if case == group[0] else ""
                for case in group
            },
        )
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        with zipfile.ZipFile(source) as old, zipfile.ZipFile(output) as new:
            from admin.tests.reliability.applicable_law_workbook import worksheet_member

            target = worksheet_member(old, profile.sheet)
            assert all(
                old.read(name) == new.read(name) for name in old.namelist() if name != target
            )


def test_failed_writer_validation_keeps_existing_destination(inputs, tmp_path, monkeypatch):
    from admin.tests.reliability import applicable_law_workbook as writer

    output = tmp_path / "existing.xlsx"
    output.write_bytes(b"old output must survive")
    monkeypatch.setattr(writer, "_patch_xml", lambda *_args: b"<broken>")
    with pytest.raises(EvaluationInputError):
        write_prediction_copy(inputs[0], output, "Sheet1", "F", {2: "性工法"})
    assert output.read_bytes() == b"old output must survive"
    assert list(tmp_path.glob("tmp*.xlsx")) == []


def test_smoke_resume_does_not_retry_errors_and_explicit_retry_only_failed(
    inputs, config, tmp_path
):
    cases = predict.load_cases(inputs)
    manifest, previews = predict.prepare_run(
        cases,
        config,
        _Bridge(),
        api_url=predict.DEFAULT_API_URL,
        contract_sha256=TEST_CONTRACT_SHA256,
    )
    run_dir = tmp_path / "run"
    records = predict.initialize_or_resume(run_dir, manifest, previews, resume=False)
    calls = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "status": "succeeded",
                    "result": _response(config, [], "insufficient_facts", "empty"),
                },
            )
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["X-CSRF-Token"] == "TOKEN_DO_NOT_PERSIST"
        assert body["overrides"] == config
        assert body[predict.EXPECTED_CONTRACT_FINGERPRINT_KEY] == TEST_CONTRACT_SHA256
        if len(calls) == 1:
            return httpx.Response(503, json={"code": "rag_unavailable", "detail": "PRIVATE_ERROR"})
        return httpx.Response(202, json={"job_id": "synthetic-job"})

    waits = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = predict.run_batch(
            cases,
            cases[:2],
            previews,
            manifest,
            records,
            run_dir=run_dir,
            client=client,
            csrf_token="TOKEN_DO_NOT_PERSIST",
            delay=2.1,
            retry_failed=False,
            sleep=waits.append,
        )
        assert summary["operational_failures"] == 1 and summary["insufficient_facts"] == 1
        assert waits == [2.1]
        records = predict.initialize_or_resume(run_dir, manifest, previews, resume=True)
        predict.run_batch(
            cases,
            cases[:2],
            previews,
            manifest,
            records,
            run_dir=run_dir,
            client=client,
            csrf_token="TOKEN_DO_NOT_PERSIST",
            delay=2.1,
            retry_failed=False,
            sleep=waits.append,
        )
        assert len(calls) == 2
        summary = predict.run_batch(
            cases,
            cases,
            previews,
            manifest,
            records,
            run_dir=run_dir,
            client=client,
            csrf_token="TOKEN_DO_NOT_PERSIST",
            delay=2.1,
            retry_failed=True,
            sleep=waits.append,
        )
        assert len(calls) == 3
        assert summary["operational_failures"] == 0 and summary["valid_completions"] == 2
        assert summary["unattempted_scenarios"] == 4
    sidecar = (run_dir / "predictions.jsonl").read_text()
    assert all(
        term not in sidecar
        for term in (
            "test@example.test",
            "情境",
            "TOKEN_DO_NOT_PERSIST",
            "PRIVATE_ERROR",
            "DO_NOT_STORE",
        )
    )
    assert "accuracy" not in {key for key in summary}


def test_resume_refuses_source_settings_request_or_preview_changes(inputs, config, tmp_path):
    cases = predict.load_cases(inputs)
    manifest, previews = predict.prepare_run(
        cases,
        config,
        _Bridge(),
        api_url=predict.DEFAULT_API_URL,
        contract_sha256=TEST_CONTRACT_SHA256,
    )
    run_dir = tmp_path / "run"
    predict.initialize_or_resume(run_dir, manifest, previews, resume=False)
    assert predict.initialize_or_resume(run_dir, manifest, previews, resume=True) == {}
    for changed in (
        {**manifest, "config_sha256": "changed"},
        {**manifest, predict.CONTRACT_FINGERPRINT_KEY: "cd" * 32},
        {**manifest, "cases": []},
        {**manifest, "sources": []},
    ):
        with pytest.raises(predict.PredictionRunError, match="resume_manifest_mismatch"):
            predict.initialize_or_resume(run_dir, changed, previews, resume=True)
    preview_path = run_dir / "outbound_preview.jsonl"
    preview_path.chmod(0o644)
    preview_path.write_bytes(b"changed")
    with pytest.raises(predict.PredictionRunError, match="resume_preview_mismatch"):
        predict.initialize_or_resume(run_dir, manifest, previews, resume=True)


def test_interrupted_request_is_terminal_until_explicit_retry(inputs, config, tmp_path):
    cases = predict.load_cases(inputs)
    manifest, previews = predict.prepare_run(
        cases,
        config,
        _Bridge(),
        api_url=predict.DEFAULT_API_URL,
        contract_sha256=TEST_CONTRACT_SHA256,
    )
    run_dir = tmp_path / "run"
    predict.initialize_or_resume(run_dir, manifest, previews, resume=False)
    identity = manifest["cases"][0]
    predict.append_record(
        run_dir / "predictions.jsonl",
        {
            **identity,
            "config_sha256": manifest["config_sha256"],
            "state": "started",
            predict.CONTRACT_FINGERPRINT_KEY: TEST_CONTRACT_SHA256,
        },
    )
    records = predict.load_records(run_dir / "predictions.jsonl", manifest)
    assert records[cases[0].case_id]["error_code"] == "interrupted_request"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("No automatic retry"))
    ) as client:
        summary = predict.run_batch(
            cases,
            cases[:1],
            previews,
            manifest,
            records,
            run_dir=run_dir,
            client=client,
            csrf_token="synthetic",
            delay=0,
            retry_failed=False,
        )
    assert summary["operational_failures"] == 1


def test_contract_mismatch_is_journaled_and_stops_remaining_network_calls(inputs, config, tmp_path):
    cases = predict.load_cases(inputs)
    manifest, previews = predict.prepare_run(
        cases,
        config,
        _Bridge(),
        api_url=predict.DEFAULT_API_URL,
        contract_sha256=TEST_CONTRACT_SHA256,
    )
    run_dir = tmp_path / "run"
    records = predict.initialize_or_resume(run_dir, manifest, previews, resume=False)
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body[predict.EXPECTED_CONTRACT_FINGERPRINT_KEY] == TEST_CONTRACT_SHA256
        return httpx.Response(
            409,
            json={
                "code": "applicable_laws_contract_mismatch",
                "retryable": False,
                "detail": "PRIVATE_ERROR_DO_NOT_PERSIST",
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(predict.PredictionRunError, match="applicable_laws_contract_mismatch"),
    ):
        predict.run_batch(
            cases,
            cases,
            previews,
            manifest,
            records,
            run_dir=run_dir,
            client=client,
            csrf_token="synthetic",
            delay=0,
            retry_failed=False,
        )
    assert len(calls) == 1
    assert records[cases[0].case_id]["state"] == "error"
    assert "prediction" not in records[cases[0].case_id]
    sidecar = (run_dir / "predictions.jsonl").read_text()
    assert (
        "applicable_laws_contract_mismatch" in sidecar
        and "PRIVATE_ERROR_DO_NOT_PERSIST" not in sidecar
    )
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["operational_failures"] == 1 and summary["valid_completions"] == 0
    assert summary["unattempted_scenarios"] == len(cases) - 1
    changed = {**manifest, predict.CONTRACT_FINGERPRINT_KEY: "cd" * 32}
    with pytest.raises(predict.PredictionRunError, match="resume_manifest_mismatch"):
        predict.initialize_or_resume(run_dir, changed, previews, resume=True)


def test_export_rejects_changed_second_source_before_any_output_write(
    inputs,
    config,
    tmp_path,
    monkeypatch,
):
    cases = predict.load_cases(inputs)
    manifest, previews = predict.prepare_run(
        cases,
        config,
        _Bridge(),
        api_url=predict.DEFAULT_API_URL,
        contract_sha256=TEST_CONTRACT_SHA256,
    )
    run_dir = tmp_path / "run"
    predict.initialize_or_resume(run_dir, manifest, previews, resume=False)
    record = {
        **manifest["cases"][0],
        "state": "completed",
        "config_sha256": manifest["config_sha256"],
        predict.CONTRACT_FINGERPRINT_KEY: TEST_CONTRACT_SHA256,
        "prediction": {
            "predicted_laws": ["性工法"],
            "decision_status": "predicted",
            "decision_reason": None,
        },
    }
    records = {record["case_id"]: record}
    predict.append_record(run_dir / "predictions.jsonl", record)
    predict.export_run(cases, records, run_dir, manifest)
    original_outputs = {path.name: path.read_bytes() for path in run_dir.iterdir()}

    # Edit B in the second source only; the first source is still valid. This
    # must reject the complete export before replacing the first result copy.
    with zipfile.ZipFile(inputs[1]) as original:
        entries = [(entry, original.read(entry.filename)) for entry in original.infolist()]
        comment = original.comment
    with zipfile.ZipFile(inputs[1], "w") as edited:
        edited.comment = comment
        for entry, data in entries:
            if entry.filename == "xl/worksheets/sheet1.xml":
                data = data.replace("情境2 test@example.test".encode(), "已修改的情境".encode())
            edited.writestr(entry, data)
    assert dict(read_xlsx(inputs[1])[0].rows)[2][1] == "已修改的情境"
    monkeypatch.setattr(
        predict, "write_prediction_copy", lambda *_args: pytest.fail("No partial export")
    )
    with pytest.raises(predict.PredictionRunError, match="export_source_hash_mismatch"):
        predict.export_run(cases, records, run_dir, manifest)
    assert {path.name: path.read_bytes() for path in run_dir.iterdir()} == original_outputs


def test_dry_run_has_no_network_outputs_or_token_requirement(inputs, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("RAG_EVAL_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(
        predict.httpx, "Client", lambda *_args, **_kw: pytest.fail("No dry-run network")
    )
    run_dir = tmp_path / "unused"
    assert (
        predict.main(
            [
                "--xlsx",
                str(inputs[0]),
                "--xlsx",
                str(inputs[1]),
                "--run-dir",
                str(run_dir),
                "--dry-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["scenario_count"] == 6
    assert not run_dir.exists()


@pytest.mark.parametrize("reason", [None, "", " \n", "字" * 301, 123])
def test_cli_rejects_abstentions_without_a_valid_reason(reason):
    with pytest.raises(predict.PredictionFormatError):
        predict.validate_prediction(
            {
                "predicted_laws": [],
                "decision_status": "insufficient_facts",
                "decision_reason": reason,
            }
        )
