"""Strict two-stage evaluation. Predictions can also be scored without MLX."""

import argparse
from collections import Counter
import json
from pathlib import Path
import time

from common import (
    ROOT,
    TYPES,
    compact,
    fingerprint,
    messages_for,
    parse_output,
    read_jsonl,
    render_prompt,
    write_jsonl,
)


def extract(text, generate):
    rounds = []
    try:
        raw = generate(messages_for(text), 512)
        rounds.append({"stage": "locate", "raw": raw})
        spans = parse_output(raw, text)
        entities = []
        for span in spans:
            raw = generate(messages_for(text, span), 32)
            rounds.append({"stage": "classify", "candidate": span, "raw": raw})
            kind = parse_output(raw)
            if kind != "none":
                entities.append({"text": span, "type": kind})
        return {"entities": entities, "valid": True, "rounds": rounds}
    except (ValueError, TypeError) as error:
        # Match the browser's fail-closed case: an invalid round invalidates all output.
        return {"entities": [], "valid": False, "error": str(error), "rounds": rounds}


def score(cases, predictions):
    by_id = {p["id"]: p for p in predictions}
    if len(by_id) != len(predictions) or set(by_id) != {c["id"] for c in cases}:
        raise ValueError("Predictions must contain exactly one row for every selected case")
    counts = Counter()
    per_type = {kind: Counter() for kind in TYPES}
    for case in cases:
        pred = by_id[case["id"]]
        gold = {(e["text"], e["type"]) for e in case["entities"]}
        actual = set()
        valid = pred.get("valid") is True
        if valid:
            for e in pred["entities"]:
                if e["type"] not in TYPES or not e["text"] or e["text"] not in case["text"]:
                    raise ValueError("Invalid stored prediction")
                actual.add((e["text"], e["type"]))
        tp, fp, fn = actual & gold, actual - gold, gold - actual
        counts.update(
            tp=len(tp),
            fp=len(fp),
            fn=len(fn),
            valid=int(valid),
            exact=int(valid and actual == gold),
            negative=int(not gold),
            negative_ok=int(valid and not gold and not actual),
        )
        for kind in TYPES:
            per_type[kind].update(
                tp=sum(e[1] == kind for e in tp),
                fp=sum(e[1] == kind for e in fp),
                fn=sum(e[1] == kind for e in fn),
            )

    def metrics(c):
        p = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
        r = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
        return dict(c, precision=p, recall=r, f1=2 * p * r / (p + r) if p + r else 0.0)

    return {
        "cases": len(cases),
        "micro": metrics(counts),
        "per_type": {k: metrics(v) for k, v in per_type.items()},
        "valid_output_rate": counts["valid"] / len(cases),
        "case_exact_rate": counts["exact"] / len(cases),
        "negative_case_accuracy": counts["negative_ok"] / counts["negative"]
        if counts["negative"]
        else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--split", choices=["valid", "test"], default="valid")
    parser.add_argument("--limit", type=int, help="Smoke only; not a full evaluation")
    parser.add_argument(
        "--predictions", type=Path, help="Score saved predictions only, without loading a model"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a new output directory to preserve earlier results")
    cases = [c for c in read_jsonl(ROOT / "data/annotations.jsonl") if c["split"] == args.split]
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be positive")
        cases = cases[: args.limit]
    start = time.monotonic()
    if args.predictions:
        predictions = read_jsonl(args.predictions)
    else:
        from mlx_lm import load, generate
        from mlx_lm.sample_utils import make_sampler

        model, tokenizer = load(
            args.model,
            adapter_path=str(args.adapter) if args.adapter else None,
            tokenizer_config={"trust_remote_code": False},
        )

        def infer(messages, max_tokens):
            return generate(
                model,
                tokenizer,
                prompt=render_prompt(tokenizer, messages),
                max_tokens=max_tokens,
                sampler=make_sampler(temp=0),
                verbose=False,
            )

        predictions = []
        args.output.mkdir(parents=True)
        with (args.output / "predictions.jsonl").open("w", encoding="utf-8") as stream:
            for index, case in enumerate(cases, 1):
                result = extract(case["text"], infer)
                prediction = {"id": case["id"], **result}
                predictions.append(prediction)
                stream.write(compact(prediction) + "\n")
                stream.flush()
                print(f"{index}/{len(cases)} {case['id']} valid={result['valid']}", flush=True)
    report = score(cases, predictions)
    report.update(
        model=args.model,
        adapter=str(args.adapter) if args.adapter else None,
        split=args.split,
        partial=args.limit is not None,
        duration_seconds=time.monotonic() - start,
        prompt_sha256=fingerprint(ROOT / "prompts.json"),
        annotations_sha256=fingerprint(ROOT / "data/annotations.jsonl"),
        execution_mode="score-existing-predictions" if args.predictions else "mlx-local-inference",
        decoding="unverified source settings"
        if args.predictions
        else "greedy; no JSON grammar; no browser chunking",
        scoring="unique exact (text,type) pairs; invalid case output counts as empty",
    )
    if args.predictions:
        report["predictions_sha256"] = fingerprint(args.predictions)
    elif args.adapter:
        report["adapter_sha256"] = fingerprint(args.adapter / "adapters.safetensors")
    snapshot_record = Path(args.model) / "download.json"
    if snapshot_record.is_file():
        report["base_snapshot"] = json.loads(snapshot_record.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output / "predictions.jsonl", predictions)
    (args.output / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
