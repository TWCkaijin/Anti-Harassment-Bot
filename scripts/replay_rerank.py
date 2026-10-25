#!/usr/bin/env python3
"""Recompute a saved Admin rerank export offline; never contacts a provider."""

import argparse
import importlib.util
import json
from pathlib import Path

METRICS = Path(__file__).resolve().parents[1] / "admin/backend/admin_app/rerank_tests/metrics.py"
spec = importlib.util.spec_from_file_location("rerank_metrics", METRICS)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def replay(report):
    rows = []
    for original in report["results"]:
        row = dict(original)
        if row.get("metrics") is not None:
            row["metrics"] = metrics.evaluate(
                row["case"],
                row["candidates"],
                row["model_order"],
                row["final_order"],
                report["settings"]["top_k"],
                row["rerank"],
            )
            if row["metrics"] != original["metrics"]:
                raise ValueError(f"Metric mismatch: {row['case_id']}")
        rows.append(row)
    summary = metrics.summarize(rows, report["total"])
    if summary != report["summary"]:
        raise ValueError("Summary mismatch")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(json.loads(args.report.read_text())), ensure_ascii=False, indent=2))
