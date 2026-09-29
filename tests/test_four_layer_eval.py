"""Behavioral tests for the offline evaluator, not evidence of legal accuracy."""

import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tests.reliability.four_layer_eval import (
    FIXTURES,
    _family_groups,
    evaluate_answer,
    evaluate_case,
    evaluate_guidance,
    evaluate_retrieval,
    export_candidates,
    live_responses,
    main,
    merge_explicit_updates,
    read_jsonl,
    response_digest,
    run_evaluation,
)


@pytest.fixture
def fixtures():
    return {case["case_id"]: case for case in read_jsonl(FIXTURES)}


def responses(case):
    return [turn["observation"] for turn in case["turns"]]


def test_offline_default_never_constructs_an_http_client(monkeypatch, fixtures):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline evaluation must not initialize a network client")

    monkeypatch.setattr(httpx, "Client", forbidden)
    report = run_evaluation(list(fixtures.values()))
    assert report["mode"] == "offline_fixture"
    assert report["summary"]["attempted_case_count"] == 8
    assert report["metadata"]["model"] == "unknown"
    assert len(report["metadata_warnings"]) == 4


def test_outdated_answer_can_pass_law_names_but_cannot_pass_legal_correctness(fixtures):
    case = fixtures["synthetic:outdated-one-year"]
    layers = evaluate_case(case, responses(case))["layers"]
    assert layers["law_routing"]["coverage_pass"] is True
    assert layers["law_routing"]["exact_pass"] is True
    assert layers["answer"]["known_error_check"] == "fail"
    assert layers["answer"]["legal_correctness"] == "pending_expert_review"
    assert layers["retrieval"]["recall_at_k"] == 0


def test_unreviewed_candidate_is_excluded_even_if_it_contains_annotations(fixtures):
    case = copy.deepcopy(fixtures["synthetic:unreviewed-not-gold"])
    case["annotations"] = {"law_routing": {"required_laws": ["性騷擾防治法"]}}
    report = run_evaluation([case])
    group = report["summary"]["groups"]["unreviewed"]
    assert group["law_routing"]["evaluated_count"] == 0
    assert group["law_routing"]["precision"] is None
    assert group["law_routing"]["recall"] is None


def test_reviewed_flag_without_reviewer_and_date_does_not_create_gold(fixtures):
    case = copy.deepcopy(fixtures["synthetic:outdated-one-year"])
    case["review_status"] = "expert_reviewed"
    assert (
        evaluate_case(case, responses(case))["layers"]["law_routing"]["status"] == "not_evaluated"
    )


def test_missing_offline_observation_is_not_an_api_failure_or_a_pass(fixtures):
    case = copy.deepcopy(fixtures["synthetic:unreviewed-not-gold"])
    case["turns"] = [{"message": "僅離線候選題"}]
    report = run_evaluation([case])
    group = report["summary"]["groups"]["unreviewed"]
    assert group["api_failure_count"] == 0
    assert group["not_observed_count"] == 1
    assert report["records"][0]["api_ok"] is None


def test_law_routing_has_multilabel_confusion_matrices_and_negation(fixtures):
    report = run_evaluation(list(fixtures.values()))
    laws = report["summary"]["groups"]["synthetic_fixture"]["law_routing"]
    assert laws["evaluated_count"] == 3
    assert laws["per_law_confusion_matrix"]["刑法"]["fn"] == 1
    assert laws["per_law_confusion_matrix"]["性別平等教育法"]["fn"] == 1
    assert laws["per_law_confusion_matrix"]["性騷擾防治法"]["fp"] == 1


def test_retrieval_ranking_metrics_and_version_unknown(fixtures):
    case = copy.deepcopy(fixtures["synthetic:invalid-citation"])
    response = {
        "retrieved_documents": [
            {"doc_id": "law/unrelated", "version": "v1"},
            {"doc_id": "law/work-12"},
        ],
        "sources": [],
    }
    result = evaluate_retrieval(case, response, k=1)
    assert result["recall_at_k"] == 0
    assert result["reciprocal_rank"] == 0.5
    assert result["version_unknown_count"] == 1
    assert result["version_check_status"] == "unknown"
    assert result["ranking_source"] == "retrieved_documents"


def test_known_wrong_version_is_not_hidden_by_another_unknown_version(fixtures):
    case = fixtures["synthetic:wrong-version"]
    result = evaluate_retrieval(case, responses(case)[0], k=3)
    assert result["wrong_version_ids"] == ["law/education"]
    assert result["version_unknown_count"] == 1
    assert result["version_check_status"] == "fail"


def test_a_version_label_without_expected_version_is_not_certified_current(fixtures):
    case = copy.deepcopy(fixtures["synthetic:invalid-citation"])
    case["annotations"]["retrieval"].pop("expected_versions")
    result = evaluate_retrieval(
        case, {"sources": [{"doc_id": "law/work-12", "version": "unknown-provenance-tag"}]}, k=3
    )
    assert result["recall_at_k"] == 1
    assert result["version_check_status"] == "unknown"
    assert result["unverified_version_ids"] == ["law/work-12"]


def test_citation_id_validation_does_not_claim_entailment(fixtures):
    case = fixtures["synthetic:invalid-citation"]
    result = evaluate_answer(case, responses(case)[0])
    assert result["citation_validity"] == "fail"
    assert result["unknown_citation_ids"] == ["law/fabricated"]
    assert result["citation_support"] == "pending_expert_review"


def test_public_api_nested_sources_are_used_by_citation_and_retrieval_checks(fixtures):
    case = fixtures["synthetic:wrong-version"]
    response = copy.deepcopy(responses(case)[0])
    flat_hash = response_digest(response)
    response["rag_used"] = {"status": True, "sources": response.pop("sources")}
    assert response_digest(response) == flat_hash
    result = evaluate_case(case, [response])
    assert result["layers"]["answer"]["citation_validity"] == "pass"
    assert result["layers"]["retrieval"]["wrong_version_ids"] == ["law/education"]


def test_incomplete_recorded_conversation_cannot_score_as_a_final_answer(fixtures):
    case = copy.deepcopy(fixtures["synthetic:necessary-clarification"])
    case["annotations"]["law_routing"] = {"required_laws": []}
    result = evaluate_case(case, responses(case)[:1])
    assert result["observation_complete"] is False
    assert result["layers"]["law_routing"]["status"] == "not_evaluated"
    assert result["layers"]["guidance"]["pass"] is False


def test_expert_answer_review_is_bound_to_actual_response(fixtures):
    case = fixtures["synthetic:invalid-citation"]
    response = copy.deepcopy(responses(case)[0])
    response["expert_review"] = {
        "reviewer_id": "test-fixture-reviewer",
        "reviewed_at": "2026-01-01",
        "verdict": "fail",
        "response_sha256": response_digest(response),
    }
    assert evaluate_answer(case, response)["legal_correctness"] == "fail"
    response["reply"] = "A different answer cannot inherit the previous human review."
    assert evaluate_answer(case, response)["legal_correctness"] == "pending_expert_review"


@pytest.mark.parametrize(
    "case_id",
    [
        "synthetic:necessary-clarification",
        "synthetic:declined-and-abstain",
        "synthetic:confirmed-correction",
    ],
)
def test_expected_short_clarification_decline_and_correction_contracts(fixtures, case_id):
    case = fixtures[case_id]
    assert evaluate_guidance(case, responses(case))["pass"] is True


def test_unnecessary_repeat_and_stale_revision_fail_guidance(fixtures):
    case = fixtures["synthetic:unnecessary-repeat"]
    result = evaluate_guidance(case, responses(case))
    assert result["pass"] is False
    assert result["turns"][0]["repeated_known_question"] is True
    assert result["turns"][0]["revision_ok"] is False


def test_annotated_old_fact_pattern_rejects_a_wrong_correction(fixtures):
    case = copy.deepcopy(fixtures["synthetic:confirmed-correction"])
    case["annotations"]["guidance"]["turns"][0]["forbidden_reply_patterns"] = ["對方是同事"]
    response = copy.deepcopy(responses(case)[0])
    response["reply"] = "對方是同事。"
    assert evaluate_guidance(case, [response])["pass"] is False


def test_model_confirmation_cannot_overwrite_provided_or_declined_facts():
    context = {
        "schema_version": 1,
        "revision": 4,
        "facts": {
            "relationship": {"status": "provided", "value": "同事"},
            "event_time": {"status": "declined"},
        },
    }
    response = {
        "facts_revision": 4,
        "fact_updates": [
            {
                "fact_key": "relationship",
                "status": "provided",
                "value": "同學",
                "kind": "confirmation",
            },
            {"fact_key": "event_time", "status": "provided", "value": "今年", "kind": "explicit"},
            {"fact_key": "setting", "status": "provided", "value": "工作場所", "kind": "explicit"},
        ],
    }
    result = merge_explicit_updates(context, response)
    assert result["facts"]["relationship"] == context["facts"]["relationship"]
    assert result["facts"]["event_time"] == {"status": "declined"}
    assert result["facts"]["setting"]["value"] == "工作場所"
    assert result["revision"] == 5
    assert "setting" not in context["facts"]
    assert merge_explicit_updates(context, {**response, "facts_revision": 3}) == context


def test_live_mode_requires_both_opt_in_and_endpoint(fixtures):
    with pytest.raises(ValueError, match="explicit --api-url"):
        run_evaluation(list(fixtures.values()), live=True)
    with pytest.raises(ValueError, match="explicit --live"):
        run_evaluation(list(fixtures.values()), api_url="http://localhost/chat")
    with pytest.raises(ValueError, match="without embedded credentials"):
        run_evaluation(list(fixtures.values()), live=True, api_url="https://token@example.com/chat")


def test_v2_live_harness_with_mock_transport_preserves_context_and_history(fixtures):
    case = fixtures["synthetic:necessary-clarification"]
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json=responses(case)[len(requests) - 1])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = live_responses(case, client, "https://example.test/chat", 2)
    assert len(result) == 2
    assert requests[0]["contract_version"] == 2
    assert requests[1]["case_context"]["revision"] == 1
    assert requests[1]["case_context"]["facts"]["relationship"]["value"] == "同事"
    assert len(requests[1]["history"]) == 2
    assert all(payload["stream"] is False for payload in requests)


def test_api_failure_stops_multiturn_and_is_counted_without_error_body(fixtures):
    case = fixtures["synthetic:necessary-clarification"]
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, text="SECRET server detail")
        )
    ) as client:
        report = run_evaluation(
            [case], live=True, api_url="https://example.test/chat", client=client
        )
    group = report["summary"]["groups"]["synthetic_fixture"]
    assert group["api_failure_count"] == 1
    assert group["law_routing"]["evaluated_count"] == 0
    assert "SECRET" not in json.dumps(report)
    assert len(report["observations"][0]["responses"]) == 1


def test_live_api_cannot_certify_its_own_expert_review(fixtures):
    case = fixtures["synthetic:invalid-citation"]
    response = copy.deepcopy(responses(case)[0])
    response["expert_review"] = {
        "reviewer_id": "claimed-reviewer",
        "reviewed_at": "2026-01-01",
        "response_sha256": response_digest(response),
        "verdict": "pass",
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    ) as client:
        report = run_evaluation(
            [case], live=True, api_url="https://example.test/chat", client=client
        )
    assert report["records"][0]["layers"]["answer"]["legal_correctness"] == "pending_expert_review"
    assert "expert_review" not in report["observations"][0]["responses"][0]


def test_export_reads_634_cases_without_changing_workbooks_or_promoting_labels(tmp_path):
    paths = [Path("resource/chatgpt_scene.xlsx"), Path("resource/gemini_scene.xlsx")]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    manifest = export_candidates(paths, tmp_path)
    cases = read_jsonl(tmp_path / "cases.candidate.jsonl")
    assert manifest["candidate_count"] == len(cases) == 634
    assert manifest["reviewed_gold_count"] == 0
    assert all(
        case["review_status"] == "unreviewed" and not case["eligible_for_quality_metrics"]
        for case in cases
    )
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    family_splits = {}
    for case in cases:
        family_splits.setdefault(case["family_id"], set()).add(case["split"])
        assert case["source"]["row"] >= 2
    assert all(len(splits) == 1 for splits in family_splits.values())
    assert (
        manifest["candidate_sha256"]
        == hashlib.sha256((tmp_path / "cases.candidate.jsonl").read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="exists"):
        export_candidates(paths, tmp_path)


def test_family_clustering_groups_exact_and_close_paraphrases_before_split():
    cases = [
        {"scenario": text}
        for text in [
            "某名員工在工作場所遭到同事持續傳送不舒服的訊息",
            "某名員工在工作場所遭到同事持續傳送不舒服的訊息。",
            "公車上的陌生人",
        ]
    ]
    groups = _family_groups(cases)
    assert groups[0] == groups[1]
    assert groups[0] != groups[2]


def test_cli_defaults_to_offline_and_refuses_overwrite(tmp_path):
    target = tmp_path / "report.json"
    assert main(["--output", str(target)]) == 0
    report = json.loads(target.read_text())
    assert report["mode"] == "offline_fixture"
    assert main(["--output", str(target)]) == 2
    assert main(["--output", str(target), "--overwrite", "--limit", "0"]) == 2
