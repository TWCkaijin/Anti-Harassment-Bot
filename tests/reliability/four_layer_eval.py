#!/usr/bin/env python3
"""Offline-first evaluation infrastructure; synthetic passes are not legal accuracy.

This module does not import the application, initialize Firebase, or call a model
unless the operator supplies both --live and an explicit --api-url. Workbook
labels are exported as unreviewed candidates, never promoted to expert gold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

# Also supports `python tests/reliability/four_layer_eval.py` from the repo root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.reliability.applicable_law_api_eval import (  # noqa: E402
    DEFAULT_INPUTS,
    EvaluationCase,
    cases_from_workbook,
    compact_text,
    evaluate_reply,
)

SCHEMA_VERSION = 1
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "four_layer.synthetic.jsonl"
TRUSTED_ANNOTATIONS = {"expert_reviewed", "synthetic_fixture"}
NOT_EVALUATED = "not_evaluated"
EVIDENCE_BOUNDARY = (
    "Offline and synthetic results verify evaluator behavior only; they are not "
    "measured model accuracy, legal correctness, deployed retrieval quality, or latency. "
    "Unreviewed candidate labels are excluded from quality metrics."
)


def digest(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def response_digest(response: dict) -> str:
    """Bind human review to the actual answer and evidence, excluding timing noise."""
    return digest(
        {
            "reply": response.get("reply"),
            "answer_sections": response.get("answer_sections"),
            "sources": response_sources(response),
        }
    )


def response_sources(response: dict) -> list[dict]:
    """The public API keeps evidence under rag_used.sources; fixtures may flatten it."""
    sources = response.get("sources")
    if not isinstance(sources, list):
        rag_used = response.get("rag_used")
        sources = rag_used.get("sources", []) if isinstance(rag_used, dict) else []
    return (
        [source for source in sources if isinstance(source, dict)]
        if isinstance(sources, list)
        else []
    )


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ValueError(f"{path}:{line_number}: expected an object with case_id")
        rows.append(row)
    if len({row["case_id"] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate case_id in {path}")
    return rows


def write_json(path: Path, value: Any, *, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise ValueError(f"Output exists: {path}; use --overwrite explicitly")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _family_groups(cases: list[dict]) -> list[str]:
    """Provisional character-trigram clusters, explicitly requiring human review.

    Clustering runs before splitting, including across source workbooks. It is a
    leakage check, not a guarantee that paraphrases belong to different families.
    """
    texts = [compact_text(case["scenario"]) for case in cases]
    grams = [{text[i : i + 3] for i in range(max(1, len(text) - 2))} for text in texts]
    parents = list(range(len(cases)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for right, right_grams in enumerate(grams):
        for left in range(right):
            left_grams = grams[left]
            if min(len(left_grams), len(right_grams)) < 0.72 * max(
                len(left_grams), len(right_grams), 1
            ):
                continue
            union = left_grams | right_grams
            similarity = len(left_grams & right_grams) / len(union) if union else 1.0
            if similarity >= 0.72:
                parents[root(right)] = root(left)
    groups: dict[int, list[str]] = {}
    for index, text in enumerate(texts):
        groups.setdefault(root(index), []).append(text)
    identities = {key: "family-" + digest(min(values))[:16] for key, values in groups.items()}
    return [identities[root(index)] for index in range(len(cases))]


def export_candidates(paths: list[Path], output_dir: Path, *, overwrite: bool = False) -> dict:
    """Read original XLSX files without rewriting or endorsing their labels."""
    candidate_path = output_dir / "cases.candidate.jsonl"
    manifest_path = output_dir / "manifest.json"
    if not overwrite and (candidate_path.exists() or manifest_path.exists()):
        raise ValueError("Candidate output exists; use --overwrite explicitly")
    cases = []
    source_manifest = []
    for path in paths:
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        source_cases = cases_from_workbook(path)
        source_manifest.append({"path": str(path), "sha256": file_hash, "count": len(source_cases)})
        for case in source_cases:
            cases.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "case_id": case.case_id,
                    "scenario": case.scenario,
                    "scenario_sha256": case.scenario_sha256,
                    "source": {
                        "file": str(path),
                        "file_sha256": file_hash,
                        "sheet": case.sheet,
                        "row": case.excel_row,
                    },
                    "review_status": "unreviewed",
                    "eligible_for_quality_metrics": False,
                    "candidate_labels": {
                        "primary_raw": case.primary_label_raw,
                        "secondary_raw": list(case.secondary_label_raw),
                        "law_names": list(case.required_laws),
                        "compound": case.compound,
                        "conditional": case.conditional,
                    },
                    "warnings": list(case.gold_warnings),
                    "annotations": {},
                    "turns": [{"message": case.scenario}],
                }
            )
    families = _family_groups(cases)
    for case, family in zip(cases, families, strict=True):
        bucket = int(hashlib.sha256(family.encode()).hexdigest()[:8], 16) % 10
        case.update(
            family_id=family,
            family_review_status="heuristic_needs_review",
            split="development" if bucket < 6 else "validation" if bucket < 8 else "holdout",
        )
    serialized = "".join(
        json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in cases
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "sources": source_manifest,
        "candidate_count": len(cases),
        "reviewed_gold_count": 0,
        "candidate_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        "family_count": len(set(families)),
        "family_method": "normalized character trigram Jaccard >= 0.72; transitive groups",
        "split_method": "family SHA256 modulo 10: development 0-5, validation 6-7, holdout 8-9",
        "split_status": "provisional; expert and semantic duplicate review required before benchmark use",
        "split_counts": dict(sorted(Counter(case["split"] for case in cases).items())),
        "warnings": dict(sorted(Counter(w for case in cases for w in case["warnings"]).items())),
        "generation_provenance": "Workbook filenames only; generating model, prompt, and human review are not established.",
        "evidence_boundary": EVIDENCE_BOUNDARY,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(serialized, encoding="utf-8")
    write_json(manifest_path, manifest, overwrite=overwrite)
    return manifest


def not_evaluated(reason: str) -> dict:
    return {"status": NOT_EVALUATED, "reason": reason}


def annotation(case: dict, layer: str) -> dict | None:
    if case.get("review_status") not in TRUSTED_ANNOTATIONS:
        return None
    if case.get("review_status") == "expert_reviewed":
        review = case.get("review", {})
        if not review.get("reviewer_id") or not review.get("reviewed_at"):
            return None
    value = case.get("annotations", {}).get(layer)
    return value if isinstance(value, dict) else None


def evaluate_laws(case: dict, response: dict) -> dict:
    gold = annotation(case, "law_routing")
    if gold is None:
        return not_evaluated("No reviewed law-routing annotation; candidate names are not gold")
    required = tuple(gold.get("required_laws", []))
    primary = tuple(gold.get("primary_laws", required[:1]))
    surrogate = EvaluationCase(
        case_id=case["case_id"],
        source_file=Path("reviewed_annotation"),
        sheet="",
        excel_row=0,
        scenario=case.get("scenario", ""),
        scenario_columns=(),
        scenario_sha256="",
        primary_label_raw="",
        secondary_label_raw=(),
        primary_laws=primary,
        secondary_laws=(),
        required_laws=required,
        compound=len(required) > 1,
        contains_yishe=False,
        conditional=False,
        gold_warnings=(),
    )
    result = evaluate_reply(
        surrogate,
        str(response.get("reply", "")),
        interaction_mode=response.get("interaction_mode", "answer"),
    )
    predicted = set(result.predicted_laws)
    expected = set(required)
    allowed = set(gold.get("conditional_laws", []))
    false_positives = predicted - expected - allowed
    true_positives = predicted & expected
    missing = expected - predicted
    return {
        "status": "evaluated",
        "scope": "law-name routing only; not article, factual, or legal correctness",
        "expected": sorted(expected),
        "predicted": sorted(predicted),
        "conditional_laws": sorted(allowed),
        "tp": len(true_positives),
        "fp": len(false_positives),
        "fn": len(missing),
        "missing": sorted(missing),
        "extra": sorted(false_positives),
        "polarity_conflicts": list(result.polarity_conflicts),
        "coverage_pass": result.coverage_pass,
        "exact_pass": result.coverage_pass and not false_positives,
        "primary_expected": primary[0] if primary else "none",
        "primary_predicted": response.get("primary_law") or "not_provided",
    }


def _source_map(response: dict) -> dict[str, dict]:
    result = {}
    for source in response_sources(response):
        source_id = source.get("doc_id") or source.get("source_id")
        if isinstance(source_id, str) and source_id:
            result[source_id] = source
            collection = source.get("collection")
            if collection and not source_id.startswith(str(collection) + "/"):
                result[f"{collection}/{source_id}"] = source
    return result


def evaluate_retrieval(case: dict, response: dict, k: int) -> dict:
    gold = annotation(case, "retrieval")
    # Explicit retrieval traces can be supplied in recorded observations. Sources
    # are only a proxy when the API omits the complete ranked retrieval list.
    explicit = response.get("retrieved_documents")
    docs = explicit if isinstance(explicit, list) else response_sources(response)
    docs = [doc for doc in docs if isinstance(doc, dict)]
    ids = list(
        dict.fromkeys(
            doc.get("doc_id") or doc.get("source_id")
            for doc in docs
            if doc.get("doc_id") or doc.get("source_id")
        )
    )
    versions = {
        doc.get("doc_id") or doc.get("source_id"): doc.get("version") or doc.get("corpus_version")
        for doc in docs
    }
    observed = {
        "retrieved_count": len(ids),
        "version_unknown_count": sum(not versions.get(key) for key in ids),
        "ranking_source": "retrieved_documents" if explicit is not None else "sources_proxy",
    }
    if gold is None or not gold.get("relevant_ids"):
        return {**not_evaluated("No reviewed relevant-document set"), **observed}
    relevant = set(gold["relevant_ids"])
    first_rank = next((rank for rank, key in enumerate(ids, 1) if key in relevant), None)
    expected_versions = gold.get("expected_versions", {})
    wrong_versions = [
        key
        for key in ids
        if key in expected_versions
        and versions.get(key)
        and versions[key] != expected_versions[key]
    ]
    unverified_versions = [key for key in ids if key not in expected_versions]
    return {
        "status": "evaluated",
        **observed,
        "k": k,
        "recall_at_k": len(set(ids[:k]) & relevant) / len(relevant),
        "reciprocal_rank": 1 / first_rank if first_rank else 0.0,
        "wrong_version_ids": wrong_versions,
        "unverified_version_ids": unverified_versions,
        "version_check_status": "fail"
        if wrong_versions
        else "unknown"
        if any(not versions.get(key) for key in ids) or unverified_versions
        else "pass"
        if ids
        else "not_evaluated",
    }


def evaluate_answer(case: dict, response: dict) -> dict:
    sources = _source_map(response)
    sections = [
        section for section in response.get("answer_sections", []) if isinstance(section, dict)
    ]
    citations = [
        source_id
        for section in sections
        for source_id in section.get("source_ids", [])
        if isinstance(source_id, str)
    ]
    unknown = sorted(set(citations) - set(sources))
    gold = annotation(case, "answer")
    hits = [
        pattern
        for pattern in (gold or {}).get("forbidden_patterns", [])
        if re.search(pattern, str(response.get("reply", "")))
    ]
    reviewed = response.get("expert_review", {})
    bound_review = (
        isinstance(reviewed, dict)
        and reviewed.get("reviewer_id")
        and reviewed.get("reviewed_at")
        and reviewed.get("response_sha256") == response_digest(response)
        and reviewed.get("verdict") in {"pass", "fail"}
    )
    legal_status = reviewed["verdict"] if bound_review else "pending_expert_review"
    requires_citations = (gold or {}).get("requires_citations", False)
    return {
        "status": "structural_checks_only",
        "citation_count": len(citations),
        "unknown_citation_ids": unknown,
        "citation_validity": "fail"
        if unknown or (requires_citations and not citations)
        else "pass"
        if citations
        else "not_evaluated",
        "citation_support": legal_status,
        "legal_correctness": legal_status,
        "response_sha256": response_digest(response),
        "known_error_pattern_hits": hits,
        "known_error_check": "fail"
        if hits
        else "no_configured_error_detected"
        if gold
        else NOT_EVALUATED,
        "note": "A valid source ID proves provenance only, not entailment; pattern checks cannot certify an answer.",
    }


def evaluate_guidance(case: dict, responses: list[dict]) -> dict:
    gold = annotation(case, "guidance")
    if gold is None:
        return not_evaluated("No reviewed multi-turn guidance annotation")
    expectations = gold.get("turns", [])
    if len(expectations) != len(responses):
        return {
            "status": "evaluated",
            "pass": False,
            "reason": "Missing or extra conversation turns",
            "turns": [],
        }
    checks = []
    for expected, response in zip(expectations, responses, strict=True):
        clarification = response.get("clarification") or {}
        actual_mode = response.get("interaction_mode", "answer")
        # Abstention must be explicit; do not infer it from a polite disclaimer.
        if response.get("abstained") is True or response.get("execution", {}).get("route") in {
            "abstain",
            "insufficient_evidence",
        }:
            actual_mode = "abstain"
        field = clarification.get("fact_key") if isinstance(clarification, dict) else None
        mode_ok = actual_mode == expected.get("mode")
        field_ok = not expected.get("fact_key") or field == expected["fact_key"]
        repeated = field is not None and field in expected.get("must_not_ask", [])
        revision_ok = (
            "facts_revision" not in expected
            or response.get("facts_revision") == expected["facts_revision"]
        )
        old_fact_repeated = any(
            re.search(pattern, response.get("reply", ""))
            for pattern in expected.get("forbidden_reply_patterns", [])
        )
        checks.append(
            {
                "expected_mode": expected.get("mode"),
                "actual_mode": actual_mode,
                "mode_ok": mode_ok,
                "field_ok": field_ok,
                "repeated_known_question": repeated,
                "revision_ok": revision_ok,
                "contradictory_reply_pattern": old_fact_repeated,
                "pass": mode_ok
                and field_ok
                and not repeated
                and revision_ok
                and not old_fact_repeated,
            }
        )
    return {
        "status": "evaluated",
        "scope": "contract and annotated pattern checks; semantic appropriateness still requires expert review",
        "pass": all(item["pass"] for item in checks),
        "turns": checks,
    }


def evaluate_case(case: dict, responses: list[dict], *, k: int = 3) -> dict:
    if not responses or any(response.get("_api_error") for response in responses):
        return {
            "case_id": case["case_id"],
            "review_status": case.get("review_status", "unreviewed"),
            "api_ok": False if responses else None,
            "layers": {
                key: not_evaluated(
                    "API request failed"
                    if responses
                    else "No recorded observation; no API request was made"
                )
                for key in ("law_routing", "retrieval", "answer", "guidance")
            },
        }
    if len(responses) != len(case.get("turns", [])):
        return {
            "case_id": case["case_id"],
            "review_status": case.get("review_status", "unreviewed"),
            "api_ok": True,
            "observation_complete": False,
            "layers": {
                **{
                    key: not_evaluated("Incomplete recorded conversation is not a final answer")
                    for key in ("law_routing", "retrieval", "answer")
                },
                "guidance": evaluate_guidance(case, responses),
            },
        }
    final = responses[-1]
    return {
        "case_id": case["case_id"],
        "family_id": case.get("family_id"),
        "split": case.get("split"),
        "review_status": case.get("review_status", "unreviewed"),
        "api_ok": True,
        "turn_count": len(responses),
        "layers": {
            "law_routing": evaluate_laws(case, final),
            "retrieval": evaluate_retrieval(case, final, k),
            "answer": evaluate_answer(case, final),
            "guidance": evaluate_guidance(case, responses),
        },
    }


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def summarize(records: list[dict]) -> dict:
    """Do not mix synthetic fixture performance with expert-reviewed performance."""
    groups = {}
    for status in sorted({record["review_status"] for record in records}):
        selected = [record for record in records if record["review_status"] == status]
        laws = [
            record["layers"]["law_routing"]
            for record in selected
            if record["layers"]["law_routing"]["status"] == "evaluated"
        ]
        tp, fp, fn = (sum(item[field] for item in laws) for field in ("tp", "fp", "fn"))
        # Multi-label one-vs-rest confusion matrices avoid inventing a single
        # primary prediction from arbitrary prose or alphabetically sorted labels.
        labels = sorted({label for item in laws for label in item["expected"] + item["predicted"]})
        matrices = {}
        for label in labels:
            eligible = [item for item in laws if label not in item["conditional_laws"]]
            matrices[label] = {
                "tp": sum(
                    label in item["expected"] and label in item["predicted"] for item in eligible
                ),
                "fp": sum(
                    label not in item["expected"] and label in item["predicted"]
                    for item in eligible
                ),
                "fn": sum(
                    label in item["expected"] and label not in item["predicted"]
                    for item in eligible
                ),
                "tn": sum(
                    label not in item["expected"] and label not in item["predicted"]
                    for item in eligible
                ),
            }
        retrieval = [
            record["layers"]["retrieval"]
            for record in selected
            if record["layers"]["retrieval"]["status"] == "evaluated"
        ]
        guidance = [
            record["layers"]["guidance"]
            for record in selected
            if record["layers"]["guidance"]["status"] == "evaluated"
        ]
        turns = [turn for item in guidance for turn in item["turns"]]
        necessary = [turn for turn in turns if turn["expected_mode"] == "clarify"]
        unnecessary = [turn for turn in turns if turn["expected_mode"] != "clarify"]
        answers = [
            record["layers"]["answer"]
            for record in selected
            if record["layers"]["answer"]["status"] == "structural_checks_only"
        ]
        groups[status] = {
            "case_count": len(selected),
            "api_failure_count": sum(record["api_ok"] is False for record in selected),
            "not_observed_count": sum(record["api_ok"] is None for record in selected),
            "law_routing": {
                "evaluated_count": len(laws),
                "precision": _ratio(tp, tp + fp),
                "recall": _ratio(tp, tp + fn),
                "f1": _ratio(2 * tp, 2 * tp + fp + fn),
                "exact_match_rate": _ratio(sum(item["exact_pass"] for item in laws), len(laws)),
                "per_law_confusion_matrix": matrices,
            },
            "retrieval": {
                "evaluated_count": len(retrieval),
                "recall_at_k": _ratio(
                    sum(item["recall_at_k"] for item in retrieval), len(retrieval)
                ),
                "mrr": _ratio(sum(item["reciprocal_rank"] for item in retrieval), len(retrieval)),
                "unknown_version_count": sum(
                    record["layers"]["retrieval"].get("version_unknown_count", 0)
                    for record in selected
                ),
            },
            "answer": {
                "structural_check_count": len(answers),
                "citation_failure_count": sum(
                    item["citation_validity"] == "fail" for item in answers
                ),
                "known_error_case_count": sum(
                    bool(item["known_error_pattern_hits"]) for item in answers
                ),
                "pending_expert_review_count": sum(
                    item["legal_correctness"] == "pending_expert_review" for item in answers
                ),
                "expert_pass_count": sum(item["legal_correctness"] == "pass" for item in answers),
                "expert_fail_count": sum(item["legal_correctness"] == "fail" for item in answers),
            },
            "guidance": {
                "evaluated_count": len(guidance),
                "scenario_pass_rate": _ratio(sum(item["pass"] for item in guidance), len(guidance)),
                "necessary_clarification_rate": _ratio(
                    sum(
                        turn["actual_mode"] == "clarify" and turn["field_ok"] for turn in necessary
                    ),
                    len(necessary),
                ),
                "unnecessary_clarification_rate": _ratio(
                    sum(turn["actual_mode"] == "clarify" for turn in unnecessary), len(unnecessary)
                ),
                "repeated_known_question_count": sum(
                    turn["repeated_known_question"] for turn in turns
                ),
            },
        }
    return {
        "attempted_case_count": len(records),
        "groups": groups,
        "evidence_boundary": EVIDENCE_BOUNDARY,
    }


def merge_explicit_updates(context: dict, response: dict) -> dict:
    """Accept non-conflicting explicit facts; confirmation is never user consent."""
    updated = json.loads(json.dumps(context))
    if response.get("facts_revision") != context["revision"]:
        return updated
    changed = False
    for update in response.get("fact_updates", []):
        if not isinstance(update, dict) or update.get("kind") != "explicit":
            continue
        key, status = update.get("fact_key"), update.get("status")
        if not isinstance(key, str) or status not in {"provided", "unknown", "declined"}:
            continue
        if status == "provided" and not isinstance(update.get("value"), str):
            continue
        current = updated["facts"].get(key)
        target = {"status": status}
        if status == "provided":
            target["value"] = update["value"]
        if current and (
            current.get("status") == "declined"
            or (current.get("status") == "provided" and current != target)
        ):
            continue
        if current != target:
            updated["facts"][key] = target
            changed = True
    if changed:
        updated["revision"] += 1
    return updated


def live_responses(
    case: dict, client: httpx.Client, api_url: str, contract_version: int
) -> list[dict]:
    history = []
    responses = []
    context = case.get("case_context", {"schema_version": 1, "revision": 0, "facts": {}})
    context = json.loads(json.dumps(context))
    for turn in case.get("turns", [{"message": case["scenario"]}]):
        # Fixture context updates represent a user's explicit confirmed correction,
        # unlike model-emitted confirmation suggestions, which are never merged.
        if turn.get("context_updates"):
            context["facts"].update(turn["context_updates"])
            context["revision"] += 1
        payload = {"message": turn["message"], "history": history, "use_rag": True, "stream": False}
        if contract_version == 2:
            payload.update(contract_version=2, case_context=context)
        headers = {"User-Agent": "rag-harass-bot-four-layer-eval/1"}
        token = os.getenv("RAG_EVAL_APP_CHECK_TOKEN")
        if token:
            headers["X-Firebase-AppCheck"] = token
        started = time.perf_counter()
        try:
            result = client.post(api_url, json=payload, headers=headers)
            result.raise_for_status()
            response = result.json()
            if not isinstance(response, dict) or not isinstance(response.get("reply"), str):
                raise ValueError("Response must be an object with string reply")
        except (httpx.HTTPError, ValueError) as exc:
            # Do not write exception strings, URLs, credentials, or request bodies.
            responses.append(
                {
                    "_api_error": type(exc).__name__,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
            break
        response["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        # The serving model/API cannot certify its own output as expert reviewed.
        # Reviews are accepted only when supplied by the operator in offline records.
        response.pop("expert_review", None)
        responses.append(response)
        history.extend(
            [
                {"role": "user", "content": turn["message"]},
                {"role": "assistant", "content": response["reply"]},
            ]
        )
        if contract_version == 2:
            context = merge_explicit_updates(context, response)
    return responses


def run_evaluation(
    cases: list[dict],
    *,
    recorded: dict[str, list[dict]] | None = None,
    metadata: dict | None = None,
    live: bool = False,
    api_url: str | None = None,
    contract_version: int = 2,
    k: int = 3,
    client: httpx.Client | None = None,
) -> dict:
    if live and not api_url:
        raise ValueError("Live evaluation requires an explicit --api-url")
    if api_url and not live:
        raise ValueError("An API endpoint cannot be used without explicit --live")
    if live:
        parsed = urlsplit(api_url or "")
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("API URL must be HTTP(S), without embedded credentials")
    records = []
    observations = []
    owned_client = (
        httpx.Client(timeout=120, follow_redirects=False) if live and client is None else None
    )
    try:
        for case in cases:
            if live:
                responses = live_responses(case, client or owned_client, api_url, contract_version)
            elif recorded is not None:
                responses = recorded.get(case["case_id"], [])
            else:
                responses = [
                    turn["observation"] for turn in case.get("turns", []) if "observation" in turn
                ]
            records.append(evaluate_case(case, responses, k=k))
            observations.append({"case_id": case["case_id"], "responses": responses})
    finally:
        if owned_client is not None:
            owned_client.close()
    supplied = metadata or {}
    metadata_fields = ("model", "prompt_sha256", "config_sha256", "corpus_version")
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "live"
        if live
        else "offline_recorded"
        if recorded is not None
        else "offline_fixture",
        "metadata": {
            **{key: supplied.get(key, "unknown") for key in metadata_fields},
            "case_sha256": digest(cases),
            "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "contract_version": contract_version,
            "retrieval_k": k,
        },
        "metadata_warnings": [
            f"{key}: unknown; comparison is not fully reproducible"
            for key in metadata_fields
            if not supplied.get(key)
        ],
        "summary": summarize(records),
        "records": records,
        "observations": observations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("evaluate", "export-candidates"), nargs="?", default="evaluate"
    )
    parser.add_argument("--xlsx", type=Path, action="append")
    parser.add_argument("--output-dir", type=Path, default=HERE / "candidates")
    parser.add_argument("--cases", type=Path, default=FIXTURES)
    parser.add_argument("--responses", type=Path, help="Recorded JSONL: case_id and responses[]")
    parser.add_argument(
        "--metadata",
        type=Path,
        help="JSON with model, prompt_sha256, config_sha256, corpus_version",
    )
    parser.add_argument("--output", type=Path, default=HERE / "results" / "four-layer.offline.json")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly permit HTTP requests, which may incur model charges",
    )
    parser.add_argument("--api-url", help="Explicit staging chat endpoint; no default endpoint")
    parser.add_argument("--contract-version", type=int, choices=(1, 2), default=2)
    parser.add_argument("--limit", type=int, help="Maximum cases; live defaults to 10 cases")
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if args.limit is not None and args.limit < 1 or args.k < 1:
            raise ValueError("--limit and --k must be positive")
        if args.command == "export-candidates":
            if args.live or args.api_url:
                raise ValueError("Candidate export is offline; live flags are not accepted")
            manifest = export_candidates(
                args.xlsx or list(DEFAULT_INPUTS), args.output_dir, overwrite=args.overwrite
            )
            print(
                json.dumps(
                    {
                        "candidate_count": manifest["candidate_count"],
                        "reviewed_gold_count": 0,
                        "family_count": manifest["family_count"],
                        "output_dir": str(args.output_dir),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.live and args.responses:
            raise ValueError("Choose live requests or recorded responses, not both")
        if args.output.exists() and not args.overwrite:
            raise ValueError("Output exists; use --overwrite explicitly")
        cases = read_jsonl(args.cases)
        limit = args.limit if args.limit is not None else 10 if args.live else len(cases)
        recorded = (
            {row["case_id"]: row["responses"] for row in read_jsonl(args.responses)}
            if args.responses
            else None
        )
        metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else {}
        report = run_evaluation(
            cases[:limit],
            recorded=recorded,
            metadata=metadata,
            live=args.live,
            api_url=args.api_url,
            contract_version=args.contract_version,
            k=args.k,
        )
        report["metadata"]["source_cases_sha256"] = hashlib.sha256(
            args.cases.read_bytes()
        ).hexdigest()
        write_json(args.output, report, overwrite=args.overwrite)
        print(
            json.dumps(
                {
                    "mode": report["mode"],
                    "case_count": len(report["records"]),
                    "output": str(args.output),
                    "evidence_boundary": EVIDENCE_BOUNDARY,
                },
                ensure_ascii=False,
            )
        )
        return 3 if any(record["api_ok"] is False for record in report["records"]) else 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"Evaluation input/output error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
