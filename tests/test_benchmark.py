import json

import httpx
import pytest

from tests.reliability.benchmark import (
    DEFAULT_RECORDING,
    build_report,
    collect_sse,
    main,
    measure,
    read_recording,
    sse_events,
)


def test_first_readable_delta_ignores_progress_heartbeat_and_whitespace():
    events = list(
        sse_events(
            [
                ": connected",
                "",
                "event: progress",
                'data: {"phase":"waiting_model"}',
                "",
                ": keep-alive",
                "",
                "event: delta",
                'data: {"text":" "}',
                "",
                "event: delta",
                'data: {"text":"可讀文字"}',
                "",
                "event: done",
                "data: {}",
                "",
            ]
        )
    )
    record = {
        "events": [
            {"at_ms": at, "event": name, "data": data}
            for at, (name, data) in zip([10, 20, 400, 900], events, strict=True)
        ]
    }
    result = measure(record, 1)
    assert result["metrics"]["first_readable_delta_ms"] == 400
    assert result["metrics"]["complete_ms"] == 900


def test_synthetic_percentiles_preserve_unknowns_and_do_not_count_failed_completion():
    report = build_report(
        read_recording(DEFAULT_RECORDING), mode="offline_recording", source_sha256="fixture"
    )
    assert report["sample_count"] == 3
    assert report["complete_count"] == 2
    assert report["metrics"]["first_readable_delta_ms"]["p50"] == 600
    assert report["metrics"]["first_readable_delta_ms"]["p95"] == 780
    assert report["metrics"]["complete_ms"]["p50"] == 1500
    assert report["metrics"]["complete_ms"]["p95"] == 1950
    assert report["metrics"]["model_calls"]["sum"] == 3
    assert report["metrics"]["retry_ms"]["known_count"] == 1
    assert report["metrics"]["retry_ms"]["unknown_count"] == 2
    assert report["metrics"]["model_cost_usd"]["p50"] == "unknown"
    assert report["total_service_cost"] == "not_evaluated"


def test_report_never_copies_request_facts_reply_or_error_text():
    canary = "PRIVATE_CASE_CANARY"
    record = {
        "request": {"message": canary, "case_context": {"facts": canary}},
        "events": [
            {"at_ms": 5, "event": "delta", "data": {"text": canary}},
            {
                "at_ms": 20,
                "event": "done",
                "data": {"reply": canary, "execution": {"route": canary, "model_calls": 1}},
            },
        ],
    }
    report = build_report(
        [record], mode="offline_recording", source_sha256="abc", metadata={"model": canary}
    )
    assert canary not in json.dumps(report)
    assert report["samples"][0]["route"] == "unknown"


def test_cost_requires_usage_prices_and_all_calls_scope():
    record = {
        "events": [],
        "usage": {"input_tokens": 1000, "output_tokens": 500},
        "prices": {"currency": "USD", "input_per_million": 1, "output_per_million": 2},
    }
    assert measure(record, 1)["metrics"]["model_cost_usd"] == "unknown"
    record["usage_scope"] = "all_model_calls"
    assert measure(record, 1)["metrics"]["model_cost_usd"] == 0.002
    record["usage"].pop("output_tokens")
    assert measure(record, 1)["metrics"]["model_cost_usd"] == "unknown"


@pytest.mark.parametrize(
    "events",
    [
        [{"at_ms": 5, "event": "done"}, {"at_ms": 6, "event": "delta"}],
        [{"at_ms": 5, "event": "delta"}, {"at_ms": 4, "event": "done"}],
        [{"at_ms": float("nan"), "event": "done"}],
    ],
)
def test_invalid_recording_order_or_nonfinite_times_are_rejected(events):
    with pytest.raises(ValueError):
        measure({"events": events}, 1)


def test_live_sse_mock_transport_collects_real_event_types_without_recording_text():
    seen = []
    content = 'event: progress\ndata: {"phase":"waiting_model"}\n\n: heartbeat\n\nevent: delta\ndata: {"text":"PRIVATE_DELTA"}\n\nevent: done\ndata: {"reply":"PRIVATE_REPLY","execution":{"route":"tool","model_calls":2}}\n\n'

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})

    ticks = iter([0, 0.1, 0.4, 1.0, 1.01])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        record = collect_sse(
            client,
            "https://example.test/chat",
            {"message": "PRIVATE_REQUEST"},
            clock=lambda: next(ticks),
        )
    assert seen[0]["stream"] is True
    assert "PRIVATE" not in json.dumps(record)
    result = measure(record, 1)
    assert result["metrics"]["first_readable_delta_ms"] == 400
    assert result["metrics"]["complete_ms"] == 1000
    assert result["metrics"]["retry_ms"] == "unknown"


def test_default_cli_is_offline_and_live_needs_both_flags(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network client must not be constructed")

    monkeypatch.setattr(httpx, "Client", forbidden)
    output = tmp_path / "report.json"
    assert main(["--output", str(output)]) == 0
    assert main(["--live", "--output", str(tmp_path / "other.json")]) == 2
    assert (
        main(["--api-url", "https://example.test/chat", "--output", str(tmp_path / "other.json")])
        == 2
    )
    assert main(["--output", str(output)]) == 2


def test_incomplete_stream_has_no_complete_latency():
    record = {
        "events": [{"at_ms": 10, "event": "delta", "data": {"text": "partial"}}],
        "elapsed_ms": 50,
    }
    result = measure(record, 1)
    assert result["status"] == "incomplete"
    assert result["metrics"]["complete_ms"] == "unknown"
    with pytest.raises(ValueError):
        list(sse_events(["event: done", "data: {}"]))
