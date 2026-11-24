"""Pure, replayable recall scoring: cut the chunk budget BEFORE unit deduplication."""

from collections import Counter
from statistics import mean

STAGES = ("candidate", "before", "model", "final")


def evaluate(case, documents, model_order, final_order, top_k, rerank_info):
    by_key = {d["key"]: d for d in documents}
    mapping = {a["document"]["key"]: a["document"]["unit_id"] for a in case["annotations"]}
    gold = {a["document"]["unit_id"] for a in case["annotations"] if a["relevance"] == "relevant"}
    gold.discard(None)
    unit_kinds = {a["document"]["unit_id"]: a["document"]["kind"] for a in case["annotations"]}

    def units(keys):
        return {mapping.get(key) or by_key[key].get("unit_id") for key in keys} - {None}

    orders = {
        "candidate": [d["key"] for d in documents],
        "before": [d["key"] for d in documents[:top_k]],
        "model": None if model_order is None else model_order[:top_k],
        "final": final_order[:top_k],
    }
    result = {}
    for stage, order in orders.items():
        hit = None if order is None else units(order) & gold
        result[stage] = {
            "hits": None if hit is None else len(hit),
            "relevant": len(gold),
            "recall": len(hit) / len(gold) if hit is not None and gold else None,
            "hit_ids": None if hit is None else sorted(hit),
            "returned_chunks": None if order is None else len(order),
        }
    before, after, candidate = (
        set(result[s]["hit_ids"] or []) for s in ("before", "final", "candidate")
    )
    per_kind = {}
    for kind in case["data_types"]:
        relevant = {key for key in gold if unit_kinds[key] == kind}
        per_kind[kind] = {
            stage: {
                "hits": None if row["hit_ids"] is None else len(set(row["hit_ids"]) & relevant),
                "relevant": len(relevant),
            }
            for stage, row in result.items()
        }
    return {
        "stages": result,
        "per_kind": per_kind,
        "delta": result["final"]["recall"] - result["before"]["recall"] if gold else None,
        "missing_candidates": sorted(gold - candidate),
        "dropped": sorted(before - after),
        "gained": sorted(after - before),
        "missing_final": sorted(gold - after),
        "unmapped_keys": [
            d["key"] for d in documents if not (mapping.get(d["key"]) or d.get("unit_id"))
        ],
        "rerank_status": rerank_info["status"],
    }


def summarize(results, total):
    measured = [r for r in results if r.get("metrics")]

    def aggregate(rows):
        evaluated = [r for r in rows if r["hits"] is not None and r["relevant"] > 0]
        return {
            "evaluated_cases": len(evaluated),
            "macro": mean(r["hits"] / r["relevant"] for r in evaluated) if evaluated else None,
            "micro": sum(r["hits"] for r in evaluated) / sum(r["relevant"] for r in evaluated)
            if evaluated
            else None,
        }

    latencies = sorted(r["elapsed_ms"] for r in measured)
    return {
        "total": total,
        "finished": len(results),
        "measured": len(measured),
        "completion_rate": len(results) / total if total else 0,
        "measurement_rate": len(measured) / total if total else 0,
        "unmeasured": total - len(measured),
        "statuses": dict(Counter(r.get("rerank", {}).get("status", r["status"]) for r in results)),
        "stages": {s: aggregate([r["metrics"]["stages"][s] for r in measured]) for s in STAGES},
        "per_kind": {
            kind: {
                s: aggregate(
                    [
                        r["metrics"]["per_kind"][kind][s]
                        for r in measured
                        if kind in r["metrics"]["per_kind"]
                    ]
                )
                for s in STAGES
            }
            for kind in ("law", "judgment", "remedy")
        },
        "mixed": {
            s: aggregate([r["metrics"]["stages"][s] for r in measured if len(r["data_types"]) > 1])
            for s in STAGES
        },
        "p95_ms": latencies[max(0, (len(latencies) * 95 + 99) // 100 - 1)] if latencies else None,
    }
