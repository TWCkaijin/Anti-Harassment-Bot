"""Prepare and optionally replay the 15 pending workbook dialogues.

The default audit is offline and prints IDs/counts only. Preparation writes a
private, rule-masked draft for human review outside the repository. Live replay
requires explicit approval of every turn and a fresh terminal confirmation of
the complete outbound request before each Admin diagnostic job. Neither source
prompts nor model replies are written to result records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "shared/python") not in sys.path:
    sys.path.insert(0, str(ROOT / "shared/python"))

from admin.tests.reliability.applicable_law_predict import PrivacyBridge, login  # noqa: E402

PENDING = {"待確認", "待修正"}
EXPECTED_CASES = {
    "MY-261016-01",
    "YZ-261016-02",
    "YZ-261016-06",
    "YZ-261016-08",
    "YZ-261016-10",
    "YZ-20261022-02",
    "YZ-20261022-05",
    "YZ-20261022-09",
    "YZ-20261030-03",
    "YZ-20261030-04",
    "YZ-20261030-06",
    "YZ-20261030-07",
    "YZ-20261030-16",
    "YZ-20261109-01",
    "YZ-20261109-02",
}
EXPECTED_TURNS = {
    "MY-261016-01": 7,
    "YZ-261016-02": 2,
    "YZ-261016-06": 1,
    "YZ-261016-08": 1,
    "YZ-261016-10": 3,
    "YZ-20261022-02": 2,
    "YZ-20261022-05": 1,
    "YZ-20261022-09": 1,
    "YZ-20261030-03": 4,
    "YZ-20261030-04": 3,
    "YZ-20261030-06": 2,
    "YZ-20261030-07": 3,
    "YZ-20261030-16": 4,
    "YZ-20261109-01": 5,
    "YZ-20261109-02": 4,
}
RESULT_KEYS = {
    "stage",
    "decision",
    "reason_code",
    "duration_ms",
    "policy_sha256",
    "model",
    "failure_type",
    "local_replacement_reason",
}


class RetestError(RuntimeError):
    """A fixed, privacy-safe reason for refusing preparation or live replay."""


@dataclass(frozen=True)
class Case:
    case_id: str
    status: str
    category: str
    turns: tuple[str, ...]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_cases(path: Path) -> tuple[Case, ...]:
    """Read only the two target sheets; never return original answers or notes."""
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            summary = workbook["案例與結果"]
            dialogue = workbook["逐輪對話"]
            summary_rows = summary.iter_rows(values_only=True)
            dialogue_rows = dialogue.iter_rows(values_only=True)
            for _ in range(4):
                next(summary_rows)
                next(dialogue_rows)
            summary_header = next(summary_rows)
            dialogue_header = next(dialogue_rows)
            if summary_header[:6] != (
                "紀錄編號",
                "測試類別",
                "測試版本",
                "測試結果",
                "系統問題摘要",
                "工程師\n處理狀態",
            ) or dialogue_header[:3] != ("紀錄編號", "輪次", "實際提問"):
                raise RetestError("workbook_headers_changed")
            selected: dict[str, tuple[str, str]] = {}
            for row in summary_rows:
                if len(row) <= 5 or row[5] not in PENDING:
                    continue
                case_id = row[0]
                if not isinstance(case_id, str) or case_id in selected:
                    raise RetestError("duplicate_or_invalid_case_id")
                selected[case_id] = (row[5], str(row[1] or ""))
            if set(selected) != EXPECTED_CASES:
                raise RetestError("pending_case_set_changed")
            if sum(status == "待確認" for status, _ in selected.values()) != 5:
                raise RetestError("pending_status_count_changed")
            turns: dict[str, list[tuple[int, str]]] = defaultdict(list)
            for row in dialogue_rows:
                if not row or row[0] not in selected:
                    continue
                number, message = row[1:3]
                if (
                    not isinstance(number, (int, float))
                    or int(number) != number
                    or not isinstance(message, str)
                    or not message.strip()
                ):
                    raise RetestError("invalid_dialogue_turn")
                turns[row[0]].append((int(number), message.strip()))
            result = []
            for case_id, (status, category) in selected.items():
                ordered = sorted(turns[case_id])
                if (
                    not ordered
                    or [number for number, _ in ordered] != list(range(1, len(ordered) + 1))
                    or len(ordered) != EXPECTED_TURNS[case_id]
                ):
                    raise RetestError("dialogue_turns_incomplete")
                result.append(Case(case_id, status, category, tuple(text for _, text in ordered)))
            return tuple(result)
        finally:
            workbook.close()
    except Exception as exc:
        if isinstance(exc, RetestError):
            raise
        raise RetestError("workbook_unreadable") from None


def _outside_repo(run_dir: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    if resolved.is_relative_to(ROOT) or not resolved.is_absolute():
        raise RetestError("run_dir_must_be_outside_repository")
    return resolved


def _private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _private_append(path: Path, value: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab") as stream:
        stream.write(_canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _hidden_terms(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return ()
    if path.stat().st_mode & 0o077:
        raise RetestError("hidden_terms_file_must_be_owner_only")
    return tuple(line.strip() for line in path.read_text().splitlines() if line.strip())


def prepare(
    workbook: Path, cases: tuple[Case, ...], run_dir: Path, hidden_terms: tuple[str, ...]
) -> None:
    run_dir = _outside_repo(run_dir)
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if run_dir.stat().st_mode & 0o077:
        raise RetestError("run_dir_must_be_owner_only")
    if any(run_dir.iterdir()):
        raise RetestError("run_dir_not_empty")
    records = []
    try:
        with PrivacyBridge() as bridge:
            for case in cases:
                prepared_turns = []
                for number, message in enumerate(case.turns, 1):
                    review = bridge.review(
                        {"message": message, "history": [], "use_rag": True, "contract_version": 4},
                        hidden_terms,
                    )
                    prepared_turns.append(
                        {
                            "turn": number,
                            "message": review["request"]["message"],
                            "findings": review["findings"],
                            "approved": False,
                        }
                    )
                records.append(
                    {
                        "case_id": case.case_id,
                        "status": case.status,
                        "category": case.category,
                        "turns": prepared_turns,
                    }
                )
    except Exception:
        raise RetestError("privacy_preparation_failed") from None
    review_file = {
        "schema_version": 1,
        "source_name": workbook.name,
        "source_sha256": _sha(workbook.read_bytes()),
        "cases": records,
    }
    _private_file(
        run_dir / "outbound_review.json",
        json.dumps(review_file, ensure_ascii=False, indent=2).encode(),
    )
    print(
        f"Prepared {len(cases)} cases and {sum(len(c.turns) for c in cases)} turns at {run_dir / 'outbound_review.json'}"
    )
    print(
        "Review every message locally, edit sensitive text, then set approved=true per turn. No network call made."
    )


def _read_approved(workbook: Path, cases: tuple[Case, ...], run_dir: Path) -> dict[str, list[str]]:
    try:
        review_path = run_dir / "outbound_review.json"
        if review_path.stat().st_mode & 0o077:
            raise RetestError("review_file_must_be_owner_only")
        value = json.loads(review_path.read_text())
        if value.get("schema_version") != 1 or value.get("source_sha256") != _sha(
            workbook.read_bytes()
        ):
            raise RetestError("review_source_mismatch")
        records = value["cases"]
        if not isinstance(records, list) or [record.get("case_id") for record in records] != [
            case.case_id for case in cases
        ]:
            raise RetestError("review_case_set_mismatch")
        approved = {}
        for case, record in zip(cases, records, strict=True):
            if record.get("status") != case.status or record.get("category") != case.category:
                raise RetestError("review_case_metadata_changed")
            turns = record.get("turns")
            if not isinstance(turns, list) or len(turns) != len(case.turns):
                raise RetestError("review_turn_count_changed")
            messages = []
            for number, turn in enumerate(turns, 1):
                if (
                    not isinstance(turn, dict)
                    or turn.get("turn") != number
                    or turn.get("approved") is not True
                    or not isinstance(turn.get("message"), str)
                    or not turn["message"].strip()
                    or len(turn["message"]) > 10000
                ):
                    raise RetestError("review_turn_not_approved")
                messages.append(turn["message"].strip())
            approved[case.case_id] = messages
        return approved
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, RetestError):
            raise
        raise RetestError("review_file_invalid") from None


def _fetch_config(client: httpx.Client, api_url: str) -> tuple[dict[str, Any], str, str]:
    response = client.get(api_url.rsplit("/", 1)[0] + "/config")
    if response.status_code != 200:
        raise RetestError(f"config_http_{response.status_code}")
    try:
        config = response.json()
    except ValueError:
        raise RetestError("runtime_config_invalid") from None
    if not isinstance(config, dict) or config.get("gate_enabled") is not True:
        raise RetestError("gate_not_enabled_in_admin_config")
    fingerprint = config.get("chat_test_contract_sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise RetestError("chat_contract_fingerprint_unavailable")
    revision = config.get("revision")
    if not isinstance(revision, str) or not revision:
        raise RetestError("runtime_revision_unavailable")
    from rag_core.core.runtime_config import WRITABLE_FIELDS

    if not set(WRITABLE_FIELDS) <= config.keys():
        raise RetestError("runtime_config_incomplete")
    overrides = {key: config[key] for key in WRITABLE_FIELDS}
    return overrides, revision, fingerprint


def _submit_job(
    client: httpx.Client,
    api_url: str,
    csrf: str,
    body: dict[str, Any],
    *,
    on_submitted: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    response = client.post(
        api_url,
        json=body,
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": uuid.uuid4().hex,
        },
    )
    if response.status_code != 202:
        return {
            "state": "error",
            "http_status": response.status_code,
            "error_code": "submission_failed",
        }
    job_id = response.json().get("job_id")
    if not isinstance(job_id, str) or not 1 <= len(job_id) <= 128:
        raise RetestError("invalid_job_id")
    if on_submitted is not None:
        on_submitted(job_id)
    deadline = time.monotonic() + 600
    url = api_url.rstrip("/") + "/" + quote(job_id, safe="")
    while time.monotonic() < deadline:
        result = client.get(url)
        if result.status_code == 429:
            try:
                delay = max(1, min(60, int(result.headers.get("Retry-After", "5"))))
            except ValueError:
                delay = 5
            time.sleep(delay)
            continue
        if result.status_code != 200:
            return {
                "state": "error",
                "http_status": result.status_code,
                "error_code": "poll_failed",
                "job_id": job_id,
            }
        job = result.json()
        if not isinstance(job, dict):
            raise RetestError("invalid_job_response")
        status = job.get("status")
        if status in {"succeeded", "completed"}:
            data = job.get("result")
            if not isinstance(data, dict) or not isinstance(data.get("response"), dict):
                raise RetestError("invalid_chat_result")
            return {"state": "completed", "data": data, "job_id": job_id}
        if status in {"failed", "cancelled", "interrupted", "needs_review"}:
            error = job.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            return {
                "state": "error",
                "job_id": job_id,
                "error_code": code
                if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,64}", code)
                else "job_failed",
            }
        if status not in {"pending", "queued", "running"}:
            raise RetestError("invalid_job_status")
        time.sleep(1)
    raise RetestError("job_wait_timeout_manual_review")


def _safe_source_ids(sources: Any) -> list[str]:
    if not isinstance(sources, list):
        return []
    ids = {item.get("doc_id") for item in sources if isinstance(item, dict)}
    return sorted(
        value
        for value in ids
        # Source IDs are collection/document paths; the separator is required
        # to preserve normal Firestore provenance without storing titles/text.
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", value)
    )


def _safe_source_groups(reply: dict[str, Any]) -> dict[str, list[str]]:
    rag = reply.get("rag_used")
    if not isinstance(rag, dict):
        return {"cited_source_ids": [], "retrieved_source_ids": [], "source_ids": []}
    cited = _safe_source_ids(rag.get("sources"))
    retrieval = rag.get("retrieval")
    retrieved = _safe_source_ids(retrieval.get("sources")) if isinstance(retrieval, dict) else []
    return {
        "cited_source_ids": cited,
        "retrieved_source_ids": retrieved,
        "source_ids": sorted(set(cited) | set(retrieved)),
    }


def _safe_sources(reply: dict[str, Any]) -> list[str]:
    return _safe_source_groups(reply)["source_ids"]


def _safe_gate_reviews(diagnostics: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = diagnostics.get("gate_reviews")
    if not isinstance(reviews, list):
        return []
    result = []
    for review in reviews:
        if isinstance(review, dict):
            result.append({key: review[key] for key in RESULT_KEYS if key in review})
    return result


def _valid_gate_trace(reviews: list[dict[str, Any]]) -> bool:
    """Allow a second post review when Gate reviews its generated refusal."""
    pre = [item for item in reviews if item.get("stage") == "pre"]
    post = [item for item in reviews if item.get("stage") == "post"]
    if len(pre) != 1 or pre[0].get("decision") not in {"allow", "deny"}:
        return False
    if pre[0]["decision"] == "deny":
        return len(post) <= 1
    if len(post) == 1:
        return post[0].get("decision") in {"allow", "deny"}
    return (
        len(post) == 2
        and post[0].get("decision") == "deny"
        and post[1].get("decision") in {"allow", "deny", "error"}
    )


def _safe_retrieval_metadata(reply: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Keep only small retrieval status/count fields, never query or document text."""
    result: dict[str, Any] = {}
    rag = reply.get("rag_used")
    if not isinstance(rag, dict):
        rag = {}
    if isinstance(rag.get("status"), bool):
        result["rag_used"] = rag["status"]
    retrieval = rag.get("retrieval")
    if not isinstance(retrieval, dict):
        retrieval = {}
    status = diagnostics.get("retrieval_status", retrieval.get("status"))
    if isinstance(status, str) and status in {"disabled", "not_requested", "ok", "empty", "failed"}:
        result["retrieval_status"] = status
    for key, fallback in (
        ("retrieved_count", retrieval.get("sources")),
        ("cited_count", rag.get("sources")),
    ):
        count = diagnostics.get(key)
        if type(count) is not int and isinstance(fallback, list):
            count = len(fallback)
        if type(count) is int and 0 <= count <= 1000:
            result[key] = count
    return result


def _confirm_request(
    run_dir: Path, case_id: str, repetition: int, turn: int, request: dict[str, Any]
) -> None:
    if not sys.stdin.isatty():
        raise RetestError("live_replay_requires_interactive_terminal")
    path = run_dir / "outbound_current.json"
    _private_file(path, json.dumps(request, ensure_ascii=False, indent=2).encode())
    token = f"SEND {case_id} {repetition}.{turn}"
    try:
        answer = input(f"Inspect {path}; type '{token}' to send this exact request: ")
        if answer != token:
            raise RetestError("outbound_request_not_approved")
    finally:
        path.unlink(missing_ok=True)


def _mask_text(bridge: PrivacyBridge, text: str) -> str:
    result = bridge.review({"message": text, "history": [], "use_rag": True, "contract_version": 4})
    masked = result["request"]["message"]
    # The browser review covers formatted identifiers. Keep additional local
    # masking for names, private institutions and exact places that a model may
    # invent in a reply before that reply enters history or a scoring artifact.
    replacements = (
        (r"(?:姓名|名字|真名|地址)\s*[:：=]\s*[^\n，。；;、\[\]]+", "[身分資訊已隱藏]"),
        (r"[\u4e00-\u9fff]{2,12}(?:路|街|巷|弄)(?:\s*\d+\s*號)?", "[地點已隱藏]"),
        (r"[\u4e00-\u9fff]{2,8}(?:公司|企業|醫院|診所|工廠|餐廳|補習班|幼兒園)", "[某單位]"),
        (r"[\u4e00-\u9fff]{2,8}(?:大學|高中|國中|國小|學校)", "[某學校]"),
        (
            r"[\u4e00-\u9fff]{2,5}(?:先生|小姐|女士|同學|老師|教授|主任|經理|校長|醫師|醫生)",
            "[某人]",
        ),
        (r"(?:我|他|她|對方|當事人)叫[\u4e00-\u9fff]{2,4}", "[某人]"),
        (r"(?<!\d)\d{7,}(?!\d)", "[長編號已隱藏]"),
    )
    for pattern, replacement in replacements:
        # Existing bracketed masks are inert; never turn one into another.
        masked = "".join(
            part
            if part.startswith("[") and part.endswith("]")
            else re.sub(pattern, replacement, part)
            for part in re.split(r"(\[[^\]]+\])", masked)
        )
    return masked


def _update_context(
    context: dict[str, Any], reply: dict[str, Any], bridge: PrivacyBridge
) -> dict[str, Any]:
    update = reply.get("summary_update")
    if (
        not isinstance(update, dict)
        or update.get("base_revision") != context["revision"]
        or not isinstance(update.get("summary"), str)
    ):
        return context
    masked = _mask_text(bridge, update["summary"])
    if masked == context["summary"]:
        return context
    return {
        "schema_version": 3,
        "revision": context["revision"] + 1,
        "summary": masked,
        "summary_origin": "model",
        "facts": {},
    }


def replay(
    workbook: Path,
    cases: tuple[Case, ...],
    run_dir: Path,
    *,
    api_url: str,
    password_file: Path,
    origin: str | None,
    repetitions: int,
    selected_ids: set[str],
    store_masked_replies: bool = False,
) -> None:
    run_dir = _outside_repo(run_dir)
    if not sys.stdin.isatty():
        raise RetestError("live_replay_requires_interactive_terminal")
    if run_dir.stat().st_mode & 0o077:
        raise RetestError("run_dir_must_be_owner_only")
    approved = _read_approved(workbook, cases, run_dir)
    if password_file.stat().st_mode & 0o077:
        raise RetestError("password_file_must_be_owner_only")
    selected = [case for case in cases if not selected_ids or case.case_id in selected_ids]
    if selected_ids - {case.case_id for case in selected}:
        raise RetestError("unknown_selected_case")
    password = password_file.read_text().rstrip("\r\n")
    with httpx.Client(timeout=55, follow_redirects=False) as client:
        try:
            csrf = login(client, api_url, password, origin)
            overrides, revision, fingerprint = _fetch_config(client, api_url)
        except Exception:
            raise RetestError("admin_login_or_config_unavailable") from None
        config_sha = _sha(_canonical(overrides))
        with PrivacyBridge() as bridge:
            for case in selected:
                for repetition in range(1, repetitions + 1):
                    stop_case = False
                    marker = run_dir / f"{case.case_id}-{repetition}.started"
                    _private_file(
                        marker, b"Manual review or restart required before another paid attempt.\n"
                    )
                    history: list[dict[str, str]] = []
                    context: dict[str, Any] = {
                        "schema_version": 3,
                        "revision": 0,
                        "summary": "",
                        "summary_origin": "user",
                        "facts": {},
                    }
                    for number, message in enumerate(approved[case.case_id], 1):
                        request = {
                            "message": message,
                            "history": [dict(item) for item in history],
                            "use_rag": True,
                            "stream": False,
                            "contract_version": 4,
                            "case_context": context,
                        }
                        _confirm_request(run_dir, case.case_id, repetition, number, request)
                        body = {
                            "request": request,
                            "overrides": overrides,
                            "expected_chat_test_contract_sha256": fingerprint,
                        }
                        started = time.monotonic()
                        _private_append(
                            run_dir / "results.jsonl",
                            {
                                "case_id": case.case_id,
                                "repetition": repetition,
                                "turn": number,
                                "state": "started",
                                "config_revision": revision,
                                "config_sha256": config_sha,
                                "contract_sha256": fingerprint,
                                "request_sha256": _sha(_canonical(body)),
                            },
                        )
                        receipt_base = {
                            "case_id": case.case_id,
                            "repetition": repetition,
                            "turn": number,
                            "request_sha256": _sha(_canonical(body)),
                        }
                        try:
                            result = _submit_job(
                                client,
                                api_url,
                                csrf,
                                body,
                                on_submitted=lambda job_id, receipt=receipt_base: _private_append(
                                    run_dir / "job_receipts.jsonl",
                                    {**receipt, "job_id": job_id},
                                ),
                            )
                        except (httpx.HTTPError, ValueError, RetestError) as exc:
                            result = {"state": "error", "error_code": type(exc).__name__}
                        record: dict[str, Any] = {
                            "case_id": case.case_id,
                            "repetition": repetition,
                            "turn": number,
                            "state": result["state"],
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "config_revision": revision,
                            "config_sha256": config_sha,
                            "contract_sha256": fingerprint,
                        }
                        if isinstance(result.get("job_id"), str):
                            record["job_id"] = result["job_id"]
                        if result["state"] != "completed":
                            record["error_code"] = result.get("error_code", "unknown_error")
                            if "http_status" in result:
                                record["http_status"] = result["http_status"]
                            _private_append(run_dir / "results.jsonl", record)
                            print(
                                f"{case.case_id} repetition {repetition} turn {number}: {record['error_code']}"
                            )
                            stop_case = True
                            break
                        data = result["data"]
                        reply = data["response"]
                        diagnostics = data.get("diagnostics")
                        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
                        text = reply.get("reply")
                        if not isinstance(text, str):
                            record.update(state="error", error_code="invalid_reply")
                            _private_append(run_dir / "results.jsonl", record)
                            stop_case = True
                            break
                        try:
                            masked_reply = _mask_text(bridge, text)
                            next_context = _update_context(context, reply, bridge)
                        except Exception:
                            record.update(state="error", error_code="history_privacy_mask_failed")
                            _private_append(run_dir / "results.jsonl", record)
                            stop_case = True
                            break
                        reviews = _safe_gate_reviews(diagnostics)
                        if not _valid_gate_trace(reviews):
                            record.update(state="error", error_code="gate_diagnostics_missing")
                            _private_append(run_dir / "results.jsonl", record)
                            stop_case = True
                            break
                        record.update(
                            reply_sha256=_sha(text.encode()),
                            reply_characters=len(text),
                            gate_reviews=reviews,
                            interaction_mode=reply.get("interaction_mode"),
                        )
                        record.update(_safe_source_groups(reply))
                        record.update(_safe_retrieval_metadata(reply, diagnostics))
                        _private_append(run_dir / "results.jsonl", record)
                        if store_masked_replies:
                            _private_append(
                                run_dir / "masked_answers.jsonl",
                                {
                                    "case_id": case.case_id,
                                    "repetition": repetition,
                                    "turn": number,
                                    "text": masked_reply,
                                    "source_reply_sha256": record["reply_sha256"],
                                },
                            )
                        history.extend(
                            [
                                {"role": "user", "content": message},
                                {"role": "assistant", "content": masked_reply},
                            ]
                        )
                        context = next_context
                        print(
                            f"{case.case_id} repetition {repetition} turn {number}: completed; answer unscored"
                        )
                    if stop_case:
                        # An uncertain external outcome can already have cost
                        # money. Stop the entire batch before any later case or
                        # repetition starts under a new idempotency key.
                        raise RetestError("live_replay_stopped_after_error")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--hidden-terms-file", type=Path)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--api-url", default="http://127.0.0.1:8001/api/v1/admin/test-jobs")
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--origin")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--store-masked-replies", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare and args.live:
        parser.error("Choose --prepare or --live")
    if (args.prepare or args.live) and args.run_dir is None:
        parser.error("--run-dir is required")
    if args.live and (not args.allow_live or args.password_file is None):
        parser.error("--live requires --allow-live and --password-file")
    if args.repetitions < 1 or args.repetitions > 5:
        parser.error("--repetitions must be between 1 and 5")
    try:
        cases = load_cases(args.workbook)
        if args.prepare:
            prepare(args.workbook, cases, args.run_dir, _hidden_terms(args.hidden_terms_file))
        elif args.live:
            replay(
                args.workbook,
                cases,
                args.run_dir,
                api_url=args.api_url,
                password_file=args.password_file,
                origin=args.origin,
                repetitions=args.repetitions,
                selected_ids=set(args.case),
                store_masked_replies=args.store_masked_replies,
            )
        else:
            print(
                json.dumps(
                    {
                        "source_sha256": _sha(args.workbook.read_bytes()),
                        "pending_confirmation": sum(c.status == "待確認" for c in cases),
                        "pending_fix": sum(c.status == "待修正" for c in cases),
                        "cases": [{"id": c.case_id, "turns": len(c.turns)} for c in cases],
                        "model_calls": 0,
                    },
                    ensure_ascii=False,
                )
            )
    except (RetestError, OSError) as exc:
        code = str(exc) if isinstance(exc, RetestError) else "file_unavailable"
        print(code, file=sys.stderr)
        return 2
    except Exception:
        # Workbook questions and provider responses may appear in exception text.
        print("retest_failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
