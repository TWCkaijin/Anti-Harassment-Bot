"""Train Qwen3 LoRA locally on Apple Silicon; --dry-run needs only Python."""

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform

from common import CompletionDataset, ROOT, fingerprint, read_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/lora.json")
    parser.add_argument("--output", type=Path, default=ROOT / "runs/lora-v1")
    parser.add_argument("--model", help="Local original Qwen3 snapshot, or Hugging Face model ID")
    parser.add_argument(
        "--steps", type=int, help="Override microbatch iterations, e.g. 8 for a smoke run"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.model:
        config["model"] = args.model
    from prepare_data import expand, validate

    cases = read_jsonl(ROOT / "data/annotations.jsonl")
    validate(cases)
    rows = {}
    for split in ("train", "valid"):
        rows[split] = read_jsonl(ROOT / f"data/{split}.jsonl")
        if rows[split] != expand([case for case in cases if case["split"] == split]):
            raise ValueError("SFT data is stale; run prepare_data.py --from-annotations")
    accum = config["grad_accumulation_steps"]
    batch = config["batch_size"]
    if accum < 1 or batch < 1 or config["epochs"] <= 0:
        raise ValueError("Batch, accumulation and epochs must be positive")
    # MLX iters counts microbatches, not optimizer updates. Round up to a full update.
    steps = (
        args.steps
        if args.steps is not None
        else math.ceil(len(rows["train"]) * config["epochs"] / (batch * accum)) * accum
    )
    if steps < accum or steps % accum:
        raise ValueError(f"--steps must be a positive multiple of {accum}")
    config.update(
        iters=steps,
        adapter_path=str(args.output.resolve()),
        fine_tune_type="lora",
        mask_prompt=True,
        train=True,
        test=False,
        data=str(ROOT / "data"),
        optimizer="adamw",
        optimizer_config={"adamw": {"weight_decay": 0.01}},
        resume_adapter_file=None,
        lr_schedule=None,
        report_to=None,
        project_name=None,
    )
    if args.steps:
        config.update(
            steps_per_report=min(steps, config["steps_per_report"]),
            steps_per_eval=min(steps, config["steps_per_eval"]),
            save_every=min(steps, config["save_every"]),
        )
    summary = {
        "config": config,
        "training_examples": len(rows["train"]),
        "validation_examples": len(rows["valid"]),
        "effective_batch": batch * accum,
        "optimizer_updates": steps // accum,
        "approx_epochs": steps * batch / len(rows["train"]),
        "prompt_sha256": fingerprint(ROOT / "prompts.json"),
        "data_sha256": {split: fingerprint(ROOT / f"data/{split}.jsonl") for split in rows},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit(
            "This backend requires macOS Apple Silicon. See README for platform scope."
        )
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("Output is nonempty; use a new --output to preserve earlier checkpoints.")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    import numpy as np
    from mlx_lm import load
    from backend import train_adapter

    np.random.seed(config["seed"])
    model, tokenizer = load(config["model"], tokenizer_config={"trust_remote_code": False})
    datasets = {
        split: CompletionDataset(data, tokenizer, config["max_seq_length"])
        for split, data in rows.items()
    }
    args.output.mkdir(parents=True, exist_ok=True)
    summary["packages"] = {
        name: importlib.metadata.version(name) for name in ("mlx", "mlx-lm", "transformers")
    }
    download_record = Path(config["model"]) / "download.json"
    if download_record.exists():
        summary["base_snapshot"] = json.loads(download_record.read_text())
    summary["max_tokens"] = {
        split: max(len(s[0]) for s in ds.samples) for split, ds in datasets.items()
    }
    (args.output / "run.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    train_adapter(config, model, datasets["train"], datasets["valid"])
    print(f"Adapter saved in {args.output}. Evaluate before export.")


if __name__ == "__main__":
    main()
