#!/usr/bin/env python3
"""Ordered three-law prediction through the ephemeral administrator contract.

Only column B is sent. Existing labels and notes are deliberately never parsed.
The browser's local privacy implementation prepares every complete outbound
request before any model call. Source workbooks are never written.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

# Support both the documented standalone command and package imports in tests.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from admin.tests.reliability.applicable_law_api_eval import (  # noqa: E402
    EvaluationInputError,
    column_index_from_reference,
    read_xlsx,
)
from admin.tests.reliability.applicable_law_workbook import write_prediction_copy  # noqa: E402

OUTPUT_CONTRACT = "applicable_laws_v1"
CONTRACT_FINGERPRINT_KEY = "applicable_laws_contract_sha256"
EXPECTED_CONTRACT_FINGERPRINT_KEY = "expected_applicable_laws_contract_sha256"
SCHEMA_VERSION = 1
DEFAULT_API_URL = "http://127.0.0.1:8001/api/v1/admin/test-jobs"
DEFAULT_RUN_DIR = Path("admin/tests/reliability/results/applicable-laws")
ALLOWED_LAWS = frozenset(("性騷法", "性工法", "性平法"))
DECISION_STATUSES = frozenset(("predicted", "insufficient_facts", "no_target_law"))
WRITABLE_CONFIG_FIELDS = frozenset(
    (
        "openrouter_model",
        "temperature",
        "top_p",
        "max_tokens",
        "reasoning_effort",
        "openrouter_provider_sort",
        "rag_rerank_enabled",
        "rag_rerank_model",
        "rag_rerank_candidate_k",
        "rag_rerank_timeout_seconds",
        "rag_rerank_max_chars_per_doc",
        "agent_prompt_sections",
        "rag_retrieval_top_k",
        "rag_distance_threshold",
        "enable_anonymization",
        "rag_collections",
        "maintenance_message",
        "enable_image_upload",
        "enable_client_privacy_review",
        "development_mode",
        "pipeline",
    )
)


class PredictionRunError(RuntimeError):
    """A fixed, privacy-safe error that aborts preparation or resume."""


class PredictionFormatError(ValueError):
    """The response does not satisfy the explicit prediction contract."""


@dataclass(frozen=True)
class WorkbookProfile:
    sheet: str
    count: int
    prediction_column: str


PROFILES = {
    "chatgpt_scene.xlsx": WorkbookProfile("Sheet1", 335, "F"),
    "gemini_scene.xlsx": WorkbookProfile("工作表1", 299, "G"),
}


@dataclass(frozen=True)
class PredictionCase:
    source: Path
    sheet: str
    excel_row: int
    scenario: str
    prediction_column: str

    @property
    def case_id(self) -> str:
        return f"{self.source.name}:{self.sheet}:{self.excel_row}"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load_cases(paths: Sequence[Path]) -> tuple[PredictionCase, ...]:
    cases: list[PredictionCase] = []
    seen: set[str] = set()
    for source in paths:
        if source.name in seen:
            raise EvaluationInputError("Duplicate source workbook basename")
        seen.add(source.name)
        profile = PROFILES.get(source.name)
        if profile is None:
            raise EvaluationInputError(f"Unsupported prediction workbook: {source.name}")
        sheet = next((item for item in read_xlsx(source) if item.name == profile.sheet), None)
        if sheet is None:
            raise EvaluationInputError(f"Missing prediction worksheet: {profile.sheet}")
        target = column_index_from_reference(profile.prediction_column)
        if any(cells.get(target, "") for _, cells in sheet.rows):
            raise EvaluationInputError(
                f"Source prediction column {profile.prediction_column} is occupied"
            )
        selected = [
            (number, cells[1])
            for number, cells in sheet.rows
            if number > 1 and cells.get(1, "").strip()
        ]
        if len(selected) != profile.count or [number for number, _ in selected] != list(
            range(2, profile.count + 2)
        ):
            raise EvaluationInputError(f"Unexpected scenario rows in {source.name}/{profile.sheet}")
        cases.extend(
            PredictionCase(source, profile.sheet, number, scenario, profile.prediction_column)
            for number, scenario in selected
        )
    if not cases:
        raise EvaluationInputError("No prediction scenarios selected")
    return tuple(cases)


def select_cases(cases: Sequence[PredictionCase], *, smoke: bool) -> tuple[PredictionCase, ...]:
    if not smoke:
        return tuple(cases)
    sources = dict.fromkeys(case.source.name for case in cases)
    selected: list[PredictionCase] = []
    for name in sources:
        group = [case for case in cases if case.source.name == name]
        selected.extend(group[index] for index in sorted({0, len(group) // 2, len(group) - 1}))
    return tuple(selected)


class PrivacyBridge:
    """Persistent Node process; failures never fall back to raw input."""

    def __init__(self, *, node: str | None = None, timeout: float = 30.0) -> None:
        binary = node or shutil.which("node")
        if not binary and Path("/opt/homebrew/bin/node").exists():
            binary = "/opt/homebrew/bin/node"
        if not binary:
            raise PredictionRunError("node_runtime_unavailable")
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(
                marker in key.upper()
                for marker in ("TOKEN", "PASSWORD", "SECRET", "API_KEY", "CREDENTIAL")
            )
        }
        self.timeout = timeout
        try:
            self.process = subprocess.Popen(
                [
                    binary,
                    "--disable-warning=ExperimentalWarning",
                    str(Path(__file__).with_name("privacy_bridge.mjs")),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env=env,
            )
        except OSError as exc:
            raise PredictionRunError("privacy_bridge_start_failed") from exc

    def review(self, request: dict[str, Any], hidden_terms: Sequence[str] = ()) -> dict[str, Any]:
        process = self.process
        if process.stdin is None or process.stdout is None or process.poll() is not None:
            raise PredictionRunError("privacy_bridge_unavailable")
        try:
            process.stdin.write(
                _json_bytes({"request": request, "hidden_terms": list(hidden_terms)}).decode()
                + "\n"
            )
            process.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(self.timeout):
                    raise PredictionRunError("privacy_bridge_timeout")
            line = process.stdout.readline()
            result = json.loads(line)
            if not isinstance(result, dict) or result.get("error_code"):
                raise PredictionRunError("privacy_review_failed")
            approved = json.loads(result["approved_body"])
            if not isinstance(approved, dict) or set(approved) != {
                "message",
                "history",
                "use_rag",
                "contract_version",
            }:
                raise PredictionRunError("privacy_bridge_invalid_output")
            if (
                approved["history"] != []
                or approved["use_rag"] is not True
                or approved["contract_version"] != 4
            ):
                raise PredictionRunError("privacy_bridge_invalid_output")
            findings = result["findings"]
            if not isinstance(findings, list) or any(
                not isinstance(item, dict)
                or set(item) != {"type", "count"}
                or not isinstance(item["type"], str)
                or type(item["count"]) is not int
                or item["count"] < 0
                for item in findings
            ):
                raise PredictionRunError("privacy_bridge_invalid_output")
            return {"request": approved, "findings": findings}
        except (BrokenPipeError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise PredictionRunError("privacy_bridge_invalid_output") from exc

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()

    def __enter__(self) -> PrivacyBridge:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def frozen_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or not set(config) >= WRITABLE_CONFIG_FIELDS:
        raise PredictionRunError("incomplete_runtime_config")
    frozen = {key: config[key] for key in sorted(WRITABLE_CONFIG_FIELDS)}
    if not isinstance(frozen["agent_prompt_sections"], dict) or not isinstance(
        frozen["pipeline"], dict
    ):
        raise PredictionRunError("invalid_runtime_config")
    try:
        _json_bytes(frozen)
    except (TypeError, ValueError) as exc:
        raise PredictionRunError("invalid_runtime_config") from exc
    return frozen


def _contract_fingerprint(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise PredictionRunError("invalid_contract_fingerprint")
    return value


def login(client: httpx.Client, api_url: str, password: str, origin: str | None = None) -> str:
    """Keep the opaque cookie and CSRF token in this process only."""
    parsed = urlsplit(api_url)
    local = parsed.hostname in {"127.0.0.1", "localhost"}
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise PredictionRunError("admin_url_requires_https")
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/test-jobs")
    ):
        raise PredictionRunError("invalid_admin_jobs_url")
    request_origin = origin or (
        "http://localhost:5174" if local else f"{parsed.scheme}://{parsed.netloc}"
    )
    client.headers["Origin"] = request_origin
    try:
        response = client.post(
            api_url.rsplit("/", 1)[0] + "/auth/login", json={"password": password}
        )
        if response.status_code != 200:
            raise PredictionRunError(f"login_http_{response.status_code}")
        csrf = response.json().get("csrf_token")
        if not isinstance(csrf, str) or not csrf or not client.cookies.get("__session"):
            raise PredictionRunError("invalid_login_response")
        return csrf
    except (httpx.HTTPError, ValueError) as exc:
        raise PredictionRunError("login_request_failed") from exc


def get_config(client: httpx.Client, api_url: str) -> tuple[dict[str, Any], str]:
    config_url = api_url.rsplit("/", 1)[0] + "/config"
    try:
        response = client.get(config_url)
        if response.status_code != 200:
            raise PredictionRunError(f"config_http_{response.status_code}")
        payload = response.json()
        config = frozen_config(payload)
        return config, _contract_fingerprint(payload.get(CONTRACT_FINGERPRINT_KEY))
    except (httpx.HTTPError, ValueError) as exc:
        raise PredictionRunError("config_request_failed") from exc


def prepare_run(
    cases: Sequence[PredictionCase],
    config: dict[str, Any],
    bridge: Any,
    *,
    api_url: str,
    contract_sha256: str,
    hidden_terms: Sequence[str] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contract_sha256 = _contract_fingerprint(contract_sha256)
    previews: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    config_sha = _sha(_json_bytes(config))
    for case in cases:
        reviewed = bridge.review(
            {"message": case.scenario, "history": [], "use_rag": True, "contract_version": 4},
            hidden_terms,
        )
        envelope = {
            "request": reviewed["request"],
            "overrides": config,
            "output_contract": OUTPUT_CONTRACT,
            EXPECTED_CONTRACT_FINGERPRINT_KEY: contract_sha256,
        }
        identity = {
            "case_id": case.case_id,
            "source_file": case.source.name,
            "sheet": case.sheet,
            "excel_row": case.excel_row,
            "request_sha256": _sha(_json_bytes(envelope)),
        }
        identities.append(identity)
        previews.append({**identity, "body": envelope, "privacy_findings": reviewed["findings"]})
    source_paths = dict.fromkeys(case.source for case in cases)
    sources = [
        {
            "file": source.name,
            "sha256": _sha(source.read_bytes()),
            "sheet": PROFILES[source.name].sheet,
            "prediction_column": PROFILES[source.name].prediction_column,
            "scenario_count": PROFILES[source.name].count,
        }
        for source in source_paths
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "output_contract": OUTPUT_CONTRACT,
        "api_url": api_url,
        "sources": sources,
        "scenario_count": len(cases),
        "config": config,
        "config_sha256": config_sha,
        CONTRACT_FINGERPRINT_KEY: contract_sha256,
        "hidden_terms_sha256": _sha(_json_bytes(list(hidden_terms))),
        "cases": identities,
        "preview_sha256": _sha(b"".join(_json_bytes(item) + b"\n" for item in previews)),
    }
    return manifest, previews


def _atomic_bytes(path: Path, content: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            temporary.chmod(0o444)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def initialize_or_resume(
    run_dir: Path,
    manifest: dict[str, Any],
    previews: Sequence[dict[str, Any]],
    *,
    resume: bool,
) -> dict[str, dict[str, Any]]:
    path = run_dir / "manifest.json"
    preview_path = run_dir / "outbound_preview.jsonl"
    if resume:
        try:
            previous = json.loads(path.read_text())
            if {key: value for key, value in previous.items() if key != "created_at"} != manifest:
                raise PredictionRunError("resume_manifest_mismatch")
            if _sha(preview_path.read_bytes()) != manifest["preview_sha256"]:
                raise PredictionRunError("resume_preview_mismatch")
        except (OSError, ValueError) as exc:
            raise PredictionRunError("resume_manifest_unreadable") from exc
        return load_records(run_dir / "predictions.jsonl", manifest)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise PredictionRunError("run_directory_not_empty_use_resume")
    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_bytes(
        preview_path, b"".join(_json_bytes(item) + b"\n" for item in previews), immutable=True
    )
    _atomic_bytes(path, _json_bytes({**manifest, "created_at": _now()}) + b"\n", immutable=True)
    return {}


def load_records(path: Path, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    identities = {case["case_id"]: case for case in manifest["cases"]}
    records: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise PredictionRunError("resume_record_invalid")
                identity = identities.get(record.get("case_id"))
                if not identity or any(record.get(key) != value for key, value in identity.items()):
                    raise PredictionRunError("resume_record_identity_mismatch")
                if record.get("config_sha256") != manifest["config_sha256"] or record.get(
                    "state"
                ) not in ("started", "completed", "error"):
                    raise PredictionRunError("resume_record_invalid")
                if record.get(CONTRACT_FINGERPRINT_KEY) != manifest[CONTRACT_FINGERPRINT_KEY]:
                    raise PredictionRunError("resume_contract_fingerprint_mismatch")
                if record["state"] == "completed":
                    validate_prediction(record["prediction"])
                elif record["state"] == "error" and not isinstance(record.get("error_code"), str):
                    raise PredictionRunError("resume_record_invalid")
                elapsed = record.get("elapsed_seconds", 0)
                if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
                    raise PredictionRunError("resume_record_invalid")
                records[record["case_id"]] = record
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PredictionRunError("resume_records_unreadable") from exc
    # A crash between the durable started event and response may have incurred a
    # model call. Treat it as terminal until --retry-failed explicitly opts in.
    for case_id, record in records.items():
        if record["state"] == "started":
            records[case_id] = {**record, "state": "error", "error_code": "interrupted_request"}
    return records


def append_record(path: Path, record: dict[str, Any]) -> None:
    with path.open("ab") as stream:
        stream.write(_json_bytes(record) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def validate_prediction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "predicted_laws",
        "decision_status",
        "decision_reason",
    }:
        raise PredictionFormatError("invalid_prediction_fields")
    laws, status = value["predicted_laws"], value["decision_status"]
    if (
        not isinstance(laws, list)
        or len(laws) > 3
        or any(not isinstance(law, str) or law not in ALLOWED_LAWS for law in laws)
    ):
        raise PredictionFormatError("invalid_prediction_laws")
    if (
        len(laws) != len(set(laws))
        or not isinstance(status, str)
        or status not in DECISION_STATUSES
    ):
        raise PredictionFormatError("invalid_prediction_status")
    if (status == "predicted") != bool(laws):
        raise PredictionFormatError("invalid_prediction_status")
    reason = value["decision_reason"]
    if (status == "predicted" and reason is not None) or (
        status != "predicted"
        and (not isinstance(reason, str) or not reason.strip() or len(reason) > 300)
    ):
        raise PredictionFormatError("invalid_prediction_reason")
    return {"predicted_laws": list(laws), "decision_status": status, "decision_reason": reason}


def validate_response(
    payload: Any, config: dict[str, Any], contract_sha256: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or set(payload) != {"output_contract", "prediction", "diagnostics"}
        or payload["output_contract"] != OUTPUT_CONTRACT
    ):
        raise PredictionFormatError("invalid_response_fields")
    prediction = validate_prediction(payload["prediction"])
    diagnostics = payload["diagnostics"]
    if not isinstance(diagnostics, dict):
        raise PredictionFormatError("invalid_diagnostics")
    if diagnostics.get(CONTRACT_FINGERPRINT_KEY) != contract_sha256:
        raise PredictionFormatError("contract_fingerprint_mismatch")
    try:
        if frozen_config(diagnostics.get("effective_config")) != config:
            raise PredictionFormatError("effective_config_mismatch")
    except PredictionRunError as exc:
        raise PredictionFormatError("effective_config_mismatch") from exc
    retrieval_status = diagnostics.get("retrieval_status")
    if retrieval_status not in ("ok", "empty", "disabled"):
        raise PredictionFormatError("invalid_retrieval_status")
    safe = {"retrieval_status": retrieval_status}
    count = diagnostics.get("retrieved_count")
    if type(count) is int and count >= 0:
        safe["retrieved_count"] = count
    execution = diagnostics.get("execution")
    if isinstance(execution, dict):
        safe["execution"] = {
            key: value
            for key, value in execution.items()
            if key in ("model_calls", "embedding_ms", "retrieval_ms", "model_ms", "validation_ms")
            and type(value) in (int, float)
            and math.isfinite(value)
            and value >= 0
        }
    return prediction, safe


def call_prediction(
    client: httpx.Client,
    api_url: str,
    csrf_token: str,
    preview: dict[str, Any],
    config: dict[str, Any],
    contract_sha256: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 1.0,
    deadline_seconds: float = 600.0,
) -> dict[str, Any]:
    start = time.monotonic()
    result: dict[str, Any]
    try:
        response = client.post(
            api_url,
            headers={
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/json",
                "Idempotency-Key": uuid.uuid4().hex,
            },
            content=_json_bytes(preview["body"]),
        )
        if response.status_code == 200:
            raise PredictionRunError("invalid_job_submission_status")
        if response.status_code == 202:
            try:
                job_id = response.json()["job_id"]
                if not isinstance(job_id, str) or not 1 <= len(job_id) <= 128:
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise PredictionRunError("invalid_job_response") from exc
            job_url = api_url.rstrip("/") + "/" + quote(job_id, safe="")
            while True:
                if time.monotonic() - start >= deadline_seconds:
                    raise PredictionRunError("job_wait_timeout_manual_review")
                polled = client.get(job_url)
                if polled.status_code == 429:
                    try:
                        retry_after = max(1, min(60, int(polled.headers.get("Retry-After", "5"))))
                    except ValueError:
                        retry_after = 5
                    sleep(retry_after)
                    continue
                if polled.status_code != 200:
                    response = polled
                    break
                try:
                    job = polled.json()
                except ValueError as exc:
                    raise PredictionRunError("invalid_job_response") from exc
                if not isinstance(job, dict):
                    raise PredictionRunError("invalid_job_response")
                status = job.get("status")
                if status == "succeeded":
                    response = httpx.Response(200, json=job.get("result"))
                    break
                if status in {"failed", "cancelled", "interrupted", "needs_review"}:
                    error = job.get("error")
                    error = error if isinstance(error, dict) else {}
                    http_status = error.get("http_status", 502)
                    if type(http_status) is not int or not 400 <= http_status <= 599:
                        http_status = 502
                    response = httpx.Response(http_status, json=error)
                    break
                if status not in {"pending", "queued", "running"}:
                    raise PredictionRunError("invalid_job_status")
                sleep(poll_seconds)
        if response.status_code != 200:
            # Provider details and HTTP error bodies may contain personal text.
            # Keep only recognized fixed machine codes, never error prose.
            code = f"http_{response.status_code}"
            try:
                error = response.json()
                known = error.get("code") if isinstance(error, dict) else None
                if known in (
                    "rag_unavailable",
                    "upstream_invalid_response",
                    "model_output_limit",
                    "upstream_request_rejected",
                    "internal_error",
                    "applicable_laws_contract_mismatch",
                ):
                    code = known
            except ValueError:
                pass
            result = {"state": "error", "error_code": code, "http_status": response.status_code}
        else:
            try:
                prediction, diagnostics = validate_response(
                    response.json(), config, contract_sha256
                )
                result = {
                    "state": "completed",
                    "prediction": prediction,
                    "diagnostics": diagnostics,
                    "http_status": 200,
                }
            except (ValueError, TypeError) as exc:
                code = (
                    str(exc) if isinstance(exc, PredictionFormatError) else "invalid_response_json"
                )
                result = {
                    "state": "error",
                    "error_code": code,
                    "http_status": 200,
                    "format_error": True,
                }
    except PredictionRunError as exc:
        result = {"state": "error", "error_code": str(exc)}
    except httpx.TimeoutException:
        result = {"state": "error", "error_code": "request_timeout"}
    except httpx.HTTPError:
        result = {"state": "error", "error_code": "request_transport_failed"}
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    return result


def summarize(records: dict[str, dict[str, Any]], total: int) -> dict[str, Any]:
    counts = {status: 0 for status in DECISION_STATUSES}
    errors = 0
    http_200 = 0
    elapsed = 0.0
    for record in records.values():
        if record["state"] == "completed":
            counts[record["prediction"]["decision_status"]] += 1
        else:
            errors += 1
        http_200 += record.get("http_status") == 200
        elapsed += record.get("elapsed_seconds", 0)
    completed = sum(counts.values())
    return {
        "total_scenarios": total,
        "attempted_scenarios": len(records),
        "unattempted_scenarios": total - len(records),
        "valid_completions": completed,
        "valid_completion_rate": completed / total if total else 0,
        "format_valid_responses": completed,
        "http_200_responses": http_200,
        "format_valid_rate": completed / http_200 if http_200 else None,
        **counts,
        "operational_failures": errors,
        "request_elapsed_seconds": round(elapsed, 3),
        "note": "Execution and format statistics only; no legal or ranking accuracy is measured.",
    }


def export_run(
    cases: Sequence[PredictionCase],
    records: dict[str, dict[str, Any]],
    run_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    sources = tuple(dict.fromkeys(case.source for case in cases))
    expected_hashes = {item["file"]: item["sha256"] for item in manifest["sources"]}
    if set(expected_hashes) != {source.name for source in sources}:
        raise PredictionRunError("export_source_manifest_mismatch")
    # Validate the complete input set before writing even the first copy: a
    # changed second workbook must not partially replace outputs from this run.
    for source in sources:
        try:
            actual_hash = _sha(source.read_bytes())
        except OSError as exc:
            raise PredictionRunError("export_source_unreadable") from exc
        if actual_hash != expected_hashes[source.name]:
            raise PredictionRunError("export_source_hash_mismatch")
    for source in sources:
        group = [case for case in cases if case.source == source]
        predictions: dict[int, str] = {}
        for case in group:
            record = records.get(case.case_id, {})
            laws = (
                record["prediction"]["predicted_laws"] if record.get("state") == "completed" else []
            )
            predictions[case.excel_row] = "、".join(laws)
        profile = PROFILES[source.name]
        write_prediction_copy(
            source,
            run_dir / f"{source.stem}.predicted.xlsx",
            profile.sheet,
            profile.prediction_column,
            predictions,
        )
    summary = summarize(records, len(cases))
    _atomic_bytes(run_dir / "summary.json", _json_bytes(summary) + b"\n")
    return summary


def run_batch(
    cases: Sequence[PredictionCase],
    selected: Sequence[PredictionCase],
    previews: Sequence[dict[str, Any]],
    manifest: dict[str, Any],
    records: dict[str, dict[str, Any]],
    *,
    run_dir: Path,
    client: httpx.Client,
    csrf_token: str,
    delay: float,
    retry_failed: bool,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    preview_map = {item["case_id"]: item for item in previews}
    pending = [
        case
        for case in selected
        if (
            records.get(case.case_id, {}).get("state") == "error"
            if retry_failed
            else case.case_id not in records
        )
    ]
    log_path = run_dir / "predictions.jsonl"
    try:
        for index, case in enumerate(pending):
            if index:
                sleep(delay)
            preview = preview_map[case.case_id]
            identity = {
                key: preview[key]
                for key in ("case_id", "source_file", "sheet", "excel_row", "request_sha256")
            }
            started = {
                **identity,
                "config_sha256": manifest["config_sha256"],
                CONTRACT_FINGERPRINT_KEY: manifest[CONTRACT_FINGERPRINT_KEY],
                "state": "started",
                "started_at": _now(),
            }
            append_record(log_path, started)
            records[case.case_id] = {
                **started,
                "state": "error",
                "error_code": "interrupted_request",
            }
            result = call_prediction(
                client,
                manifest["api_url"],
                csrf_token,
                preview,
                manifest["config"],
                manifest[CONTRACT_FINGERPRINT_KEY],
            )
            record = {**started, **result, "completed_at": _now()}
            append_record(log_path, record)
            records[case.case_id] = record
            print(
                json.dumps(
                    {
                        "case_id": case.case_id,
                        "state": record["state"],
                        "completed": index + 1,
                        "selected": len(pending),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if record.get("error_code") == "applicable_laws_contract_mismatch":
                raise PredictionRunError("applicable_laws_contract_mismatch")
    finally:
        summary = export_run(cases, records, run_dir, manifest)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--xlsx",
        action="append",
        type=Path,
        required=True,
        help="Explicit source workbook; repeat for multiple files",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Inspect inputs only; no network or output files"
    )
    mode.add_argument(
        "--prepare-only",
        action="store_true",
        help="Save complete masked preview/config; no model calls",
    )
    mode.add_argument(
        "--smoke", action="store_true", help="First/middle/last scenario from each workbook"
    )
    mode.add_argument("--all", action="store_true", help="Explicitly select all scenarios")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument(
        "--origin",
        help="Exact Admin browser origin; defaults to the API host (localhost:5174 locally)",
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        help="Owner-only local password file; default is a hidden interactive prompt",
    )
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Verify immutable inputs/settings and skip completed or failed rows",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="With --resume, select only previously failed rows",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Allow selected remote model calls after local preview preparation",
    )
    parser.add_argument("--delay", type=float, default=2.1)
    parser.add_argument("--timeout", type=float, default=190.0)
    parser.add_argument(
        "--node",
        help="Node executable with native TypeScript and registerHooks (verified with Node 26)",
    )
    parser.add_argument(
        "--hidden-terms-file",
        type=Path,
        help="Optional local UTF-8 lines to mask using clientPrivacy",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.xlsx)
        if (
            not math.isfinite(args.delay)
            or not math.isfinite(args.timeout)
            or args.delay < 0
            or args.timeout <= 0
        ):
            raise PredictionRunError("invalid_delay_or_timeout")
        if args.retry_failed and (not args.resume or args.dry_run or args.prepare_only):
            raise PredictionRunError("retry_failed_requires_live_resume")
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "mode": "dry_run",
                        "scenario_count": len(cases),
                        "workbooks": [
                            {
                                "source_file": name,
                                "sheet": PROFILES[name].sheet,
                                "scenario_count": PROFILES[name].count,
                                "scenario_column": "B",
                                "prediction_column": PROFILES[name].prediction_column,
                            }
                            for name in dict.fromkeys(case.source.name for case in cases)
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not args.prepare_only and not args.allow_live:
            raise PredictionRunError("live_calls_require_allow_live")
        if args.password_file:
            if args.password_file.stat().st_mode & 0o077:
                raise PredictionRunError("password_file_requires_owner_only_permissions")
            password = args.password_file.read_text().rstrip("\r\n")
        else:
            password = getpass.getpass("Admin password: ")
        if not password:
            raise PredictionRunError("missing_admin_password")
        hidden_terms = []
        if args.hidden_terms_file:
            hidden_terms = list(
                dict.fromkeys(
                    line.strip()
                    for line in args.hidden_terms_file.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            )
        with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
            csrf_token = login(client, args.api_url, password, args.origin)
            password = ""
            config, contract_sha256 = get_config(client, args.api_url)
            with PrivacyBridge(node=args.node) as bridge:
                manifest, previews = prepare_run(
                    cases,
                    config,
                    bridge,
                    api_url=args.api_url,
                    contract_sha256=contract_sha256,
                    hidden_terms=hidden_terms,
                )
            records = initialize_or_resume(args.run_dir, manifest, previews, resume=args.resume)
            print(
                json.dumps(
                    {
                        "preview": str(args.run_dir / "outbound_preview.jsonl"),
                        "preview_sha256": manifest["preview_sha256"],
                        "scenario_count": len(cases),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if args.prepare_only:
                summary = export_run(cases, records, args.run_dir, manifest)
            else:
                summary = run_batch(
                    cases,
                    select_cases(cases, smoke=args.smoke),
                    previews,
                    manifest,
                    records,
                    run_dir=args.run_dir,
                    client=client,
                    csrf_token=csrf_token,
                    delay=args.delay,
                    retry_failed=args.retry_failed,
                )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if not summary["operational_failures"] else 1
    except KeyboardInterrupt:
        print("prediction_run_interrupted", file=sys.stderr)
        return 130
    except (PredictionRunError, EvaluationInputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError:
        print("prediction_run_io_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
