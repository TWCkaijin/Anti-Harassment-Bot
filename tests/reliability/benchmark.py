#!/usr/bin/env python3
"""Offline-first SSE timing benchmark. Reports contain no request or reply text."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

HERE = Path(__file__).resolve().parent
DEFAULT_RECORDING = HERE / "fixtures" / "benchmark.synthetic.jsonl"
UNKNOWN = "unknown"
METRICS = (
    "first_readable_delta_ms",
    "complete_ms",
    "elapsed_ms",
    "model_ms",
    "embedding_ms",
    "retrieval_ms",
    "vector_search_ms",
    "validation_ms",
    "model_calls",
    "retry_ms",
    "attempt_count",
    "model_cost_usd",
)
ROUTES = {"short_clarify", "direct_retrieval", "tool"}
BOUNDARY = (
    "Synthetic/offline timing verifies the benchmark only, not deployed latency or savings. "
    "Time to readable text counts non-whitespace delta.text only; progress and heartbeats do not count. "
    "Stage times may overlap; retrieval_ms includes embedding and must not be added to it. "
    "Model cost excludes embedding, database, hosting, and other unpriced services."
)


def number(value):
    return (
        float(value)
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def percentile(values: list[float], fraction: float):
    if not values:
        return UNKNOWN
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return round(
        ordered[lower]
        + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower),
        3,
    )


def _model_cost(record: dict, terminal: dict) -> tuple[float | str, str]:
    usage = record.get("usage") or terminal.get("usage")
    prices = record.get("prices")
    if not isinstance(usage, dict) or not isinstance(prices, dict):
        return UNKNOWN, "not_evaluated_missing_usage_or_prices"
    if record.get("usage_scope") != "all_model_calls" or prices.get("currency") != "USD":
        return UNKNOWN, "not_evaluated_usage_scope_or_currency_unknown"
    values = [
        number(value)
        for value in (
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            prices.get("input_per_million"),
            prices.get("output_per_million"),
        )
    ]
    if any(value is None for value in values):
        return UNKNOWN, "not_evaluated_incomplete_usage_or_prices"
    input_tokens, output_tokens, input_rate, output_rate = values
    return round(
        (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 8
    ), "estimated_from_supplied_aggregate_usage_and_rates"


def measure(record: dict, index: int) -> dict:
    """Whitelist numeric observations; never return input, facts, deltas or errors."""
    first_delta = None
    complete = None
    previous = -1.0
    terminal = {}
    status = "incomplete"
    terminal_seen = False
    for event in record.get("events", []):
        at = number(event.get("at_ms"))
        if at is None or at < previous or terminal_seen:
            raise ValueError("Invalid SSE recording event order")
        previous = at
        data = event.get("data")
        name = event.get("event")
        if name in {"delta", "reply_delta"} and isinstance(data, dict):
            text = data.get("text")
            if isinstance(text, str) and text.strip() and first_delta is None:
                first_delta = at
        if name in {"done", "error"}:
            terminal_seen = True
            terminal = data if isinstance(data, dict) else {}
            status = "complete" if name == "done" else "error"
            if name == "done":
                complete = at
    if record.get("transport_error"):
        status = "transport_error"
    execution = terminal.get("execution") or record.get("execution") or {}
    if not isinstance(execution, dict):
        execution = {}
    values = {
        key: number(execution.get(key))
        for key in METRICS
        if key not in {"first_readable_delta_ms", "complete_ms", "elapsed_ms", "model_cost_usd"}
    }
    for key in ("retry_ms", "attempt_count"):
        if values[key] is None:
            values[key] = number(record.get(key))
    values.update(
        first_readable_delta_ms=first_delta,
        complete_ms=complete,
        elapsed_ms=number(record.get("elapsed_ms")),
    )
    cost, cost_status = _model_cost(record, terminal)
    values["model_cost_usd"] = cost
    return {
        "sample_index": index,
        "status": status,
        "route": execution.get("route") if execution.get("route") in ROUTES else UNKNOWN,
        "metrics": {key: value if value is not None else UNKNOWN for key, value in values.items()},
        "cost_status": cost_status,
    }


def build_report(
    records: list[dict], *, mode: str, source_sha256: str, metadata: dict | None = None
) -> dict:
    samples = [measure(record, index) for index, record in enumerate(records, 1)]
    metrics = {}
    for key in METRICS:
        measured = [
            value for sample in samples if (value := number(sample["metrics"][key])) is not None
        ]
        metrics[key] = {
            "known_count": len(measured),
            "unknown_count": len(samples) - len(measured),
            "p50": percentile(measured, 0.5),
            "p95": percentile(measured, 0.95),
            "sum": round(sum(measured), 8) if measured else UNKNOWN,
        }
    supplied = metadata or {}
    # Hash config/model/prompt metadata rather than copying arbitrary user text.
    metadata_hash = (
        hashlib.sha256(
            json.dumps(supplied, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if supplied
        else UNKNOWN
    )
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "source_sha256": source_sha256,
        "run_metadata_sha256": metadata_hash,
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sample_count": len(samples),
        "complete_count": sum(sample["status"] == "complete" for sample in samples),
        "failure_count": sum(sample["status"] != "complete" for sample in samples),
        "metrics": metrics,
        "samples": samples,
        "total_service_cost": "not_evaluated",
        "evidence_boundary": BOUNDARY,
    }


def sse_events(lines):
    """Parse SSE frames and ignore comment heartbeat frames."""
    event_name = "message"
    data_lines = []
    for line in lines:
        if not line:
            if data_lines:
                yield event_name, json.loads("\n".join(data_lines))
            event_name, data_lines = "message", []
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        # An unterminated frame is not a completed SSE event.
        raise ValueError("Incomplete SSE frame")


def collect_sse(
    client: httpx.Client, endpoint: str, payload: dict, *, clock=time.perf_counter
) -> dict:
    started = clock()
    record = {"events": []}
    headers = {"Accept": "text/event-stream", "User-Agent": "rag-harass-bot-benchmark/1"}
    if token := os.getenv("RAG_EVAL_APP_CHECK_TOKEN"):
        headers["X-Firebase-AppCheck"] = token
    try:
        with client.stream(
            "POST", endpoint, json={**payload, "stream": True}, headers=headers
        ) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ValueError("Endpoint did not return SSE")
            for event_name, data in sse_events(response.iter_lines()):
                at = round((clock() - started) * 1000, 3)
                # Keep timing and a boolean readability marker, not generated text.
                if event_name in {"delta", "reply_delta"}:
                    text = data.get("text") if isinstance(data, dict) else None
                    safe_data = {
                        "text": "readable" if isinstance(text, str) and text.strip() else ""
                    }
                elif event_name == "done":
                    safe_data = (
                        {"execution": data.get("execution", {})} if isinstance(data, dict) else {}
                    )
                else:
                    safe_data = {}
                record["events"].append({"at_ms": at, "event": event_name, "data": safe_data})
                if event_name in {"done", "error"}:
                    break
    except (httpx.HTTPError, ValueError):
        record["transport_error"] = True
    record["elapsed_ms"] = round((clock() - started) * 1000, 3)
    # Client retries are disabled. Upstream retry_ms/attempt_count remain unknown
    # unless the endpoint explicitly reports them; never fabricate zero values.
    return record


def read_recording(path: Path) -> list[dict]:
    result = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not result or any(not isinstance(record, dict) for record in result):
        raise ValueError("Recording requires one or more JSON objects")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recording", type=Path, default=DEFAULT_RECORDING)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output", type=Path, default=HERE / "results" / "benchmark.offline.json")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--live", action="store_true", help="Explicit opt-in; requests may incur model charges"
    )
    parser.add_argument("--api-url", help="Explicit SSE chat endpoint; no default")
    parser.add_argument(
        "--requests", type=Path, help="Live-only JSON list of complete chat request objects"
    )
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        if bool(args.live) != bool(args.api_url) or args.limit < 1:
            raise ValueError("Live mode requires both --live and --api-url; limit must be positive")
        if args.output.exists() and not args.overwrite:
            raise ValueError("Output exists; use --overwrite explicitly")
        metadata = json.loads(args.metadata.read_text(encoding="utf-8")) if args.metadata else {}
        if args.live:
            parsed = urlsplit(args.api_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or not args.requests
            ):
                raise ValueError("Live mode requires a credential-free HTTP(S) URL and --requests")
            payloads = json.loads(args.requests.read_text(encoding="utf-8"))
            if (
                not isinstance(payloads, list)
                or not payloads
                or any(not isinstance(payload, dict) for payload in payloads)
            ):
                raise ValueError("Requests must be a nonempty JSON list of objects")
            source_hash = hashlib.sha256(args.requests.read_bytes()).hexdigest()
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                records = [
                    collect_sse(client, args.api_url, payload) for payload in payloads[: args.limit]
                ]
        else:
            if args.requests:
                raise ValueError("--requests is live-only")
            records = read_recording(args.recording)
            source_hash = hashlib.sha256(args.recording.read_bytes()).hexdigest()
        report = build_report(
            records,
            mode="live_sse" if args.live else "offline_recording",
            source_sha256=source_hash,
            metadata=metadata,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "mode": report["mode"],
                    "sample_count": report["sample_count"],
                    "output": str(args.output),
                }
            )
        )
        return 3 if args.live and report["failure_count"] else 0
    except (ValueError, OSError, TypeError, KeyError):
        print(
            "Benchmark input/output error; verify arguments and recording schema. No request content was logged.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
