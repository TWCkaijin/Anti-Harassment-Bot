"""Produce a complete two-version presentation comparison from immutable samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path

from report_main_alignment import raw_answer


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def presentation(text):
    bold = re.findall(r"\*\*([^*]+)\*\*", text)
    return {
        "characters": len(text),
        "list_items": len(re.findall(r"(?m)^\s*(?:\d+[.)]|[-*+])\s+", text)),
        "bold_spans": bold,
        "bold_characters": sum(map(len, bold)),
        "headings": len(re.findall(r"(?m)^#{1,6}\s+", text)),
    }


def build(directory):
    cases = json.loads((directory / "cases.json").read_text())
    common = json.loads((directory / "common-config.json").read_text())
    sources = {row["doc_id"]: row for row in json.loads((directory / "sources.json").read_text())}
    manifest = json.loads((directory / "manifest.json").read_text())
    variants = ("before", "candidate")
    records = {variant: [] for variant in variants}
    for variant in variants:
        prompt_file = "prompt-before.json" if variant == "before" else "app-dev-prompt-draft.json"
        prompts = json.loads((directory / prompt_file).read_text())
        prompts = prompts.get("agent_prompt_sections", prompts)
        for case in cases:
            for repetition in range(1, 4):
                path = directory / "results" / variant / f"{case['id']}-{repetition}.json"
                row = json.loads(path.read_text())
                expected = {
                    "engine_sha256": manifest["engines"][variant]["tree_sha256"],
                    "config_sha256": digest(common),
                    "prompt_sha256": digest(prompts),
                    "case_sha256": digest(case),
                    "sources_sha256": digest([sources[key] for key in case["source_ids"]]),
                }
                if any(row.get(key) != value for key, value in expected.items()):
                    raise ValueError(f"Provenance mismatch: {path}")
                row["raw_answer"] = raw_answer(row)
                row["presentation"] = presentation(
                    row.get("displayed_reply") or row.get("streamed_reply", "")
                )
                row["result_path"] = str(path.relative_to(directory))
                records[variant].append(row)
    metrics = {}
    for variant, rows in records.items():
        first = [
            row["first_display_delta_ms"] / 1000
            for row in rows
            if row["first_display_delta_ms"] is not None
        ]
        metrics[variant] = {
            "samples": len(rows),
            "ok": sum(row["status"] == "ok" for row in rows),
            "errors": dict(Counter(row.get("error_type") for row in rows if row["status"] != "ok")),
            "model_calls": dict(Counter(row["model_calls"] for row in rows)),
            "first_display_seconds_median": round(statistics.median(first), 2) if first else None,
            "total_seconds_median": round(
                statistics.median(row["total_ms"] / 1000 for row in rows), 2
            ),
            "outputs_with_rewrites": sum(bool(row["diagnostics"].get("rewrites")) for row in rows),
            "complete_stream_matches_final": sum(
                row["status"] == "ok" and row["streamed_reply"] == row["displayed_reply"]
                for row in rows
            ),
            "cases": {
                case["id"]: [
                    {
                        "repetition": row["repetition"],
                        "status": row["status"],
                        **row["presentation"],
                    }
                    for row in rows
                    if row["case_id"] == case["id"]
                ]
                for case in cases
            },
        }
    (directory / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# 彈性呈現：完整回答比較",
        "",
        "六類合成案例，修改前後各三次，共 36 筆。失敗與部分回覆同樣保留；未挑選單次最佳答案。",
        "所有模型原始 content 與診斷見各筆 JSON；不保存 provider reasoning。粗體／條列數量僅描述輸出，不代表品質通過。",
        "",
    ]
    review_cases = []
    for case in cases:
        lines += [f"## {case['title']}", "", case["message"], ""]
        if case["summary"]:
            lines += ["共同摘要：", "", case["summary"], ""]
        if case["history"]:
            lines += [
                "固定歷史：",
                "",
                "```json",
                json.dumps(case["history"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        groups = {}
        for variant in variants:
            groups[variant] = []
            for row in (item for item in records[variant] if item["case_id"] == case["id"]):
                lines += [
                    f"### {variant} · 第 {row['repetition']} 次 · {row['status']}",
                    "",
                    f"[完整 JSON]({row['result_path']})",
                    "",
                    "實際可顯示回覆：",
                    "",
                    row.get("displayed_reply") or row.get("streamed_reply") or "（沒有有效回覆）",
                    "",
                ]
                if row["raw_answer"] != row.get("displayed_reply"):
                    lines += [
                        "模型產生的正式答案（處理前）：",
                        "",
                        "```text",
                        row["raw_answer"] or "（未形成完整正式答案）",
                        "```",
                        "",
                    ]
                question = (row.get("guidance") or {}).get("clarification")
                groups[variant].append(
                    {
                        "repetition": row["repetition"],
                        "status": row["status"],
                        "text": row.get("displayed_reply")
                        or row.get("streamed_reply")
                        or "（沒有有效回覆）",
                        "question": question.get("question", "")
                        if isinstance(question, dict)
                        else "",
                        "source_labels": [source["label"] for source in row.get("sources", [])],
                        "model_calls": row["model_calls"],
                        "total_seconds": round(row["total_ms"] / 1000, 2),
                        "result_path": row["result_path"],
                        "presentation": row["presentation"],
                    }
                )
        review_cases.append({**case, "groups": groups})
    (directory / "all-responses.md").write_text("\n".join(lines))
    (directory / "review-data.json").write_text(
        json.dumps({"cases": review_cases, "metrics": metrics}, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                variant: {key: value for key, value in result.items() if key != "cases"}
                for variant, result in metrics.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    build(parser.parse_args().directory)
