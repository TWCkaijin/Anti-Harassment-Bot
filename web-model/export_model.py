"""Merge an MLX LoRA adapter into original nonquantized Qwen3 weights."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--model", help="Must be the same base snapshot used by training")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for name in ("adapter_config.json", "adapters.safetensors"):
        if not (args.adapter / name).is_file():
            parser.error(f"Missing {args.adapter / name}")
    config = json.loads((args.adapter / "adapter_config.json").read_text())
    model = args.model or config["model"]
    if args.model and args.model != config["model"]:
        parser.error("Base model differs from recorded training model; use the identical path/ID")
    if args.output.exists():
        parser.error("Output already exists; choose a new directory")
    command = [
        sys.executable,
        "-m",
        "mlx_lm",
        "fuse",
        "--model",
        model,
        "--adapter-path",
        str(args.adapter),
        "--save-path",
        str(args.output),
        "--dequantize",
    ]
    print("Command:", command)
    if args.dry_run:
        return
    subprocess.run(command, check=True)
    if not list(args.output.glob("*.safetensors")) or not (args.output / "config.json").exists():
        raise RuntimeError("Merged model artifacts are missing")
    (args.output / "export-provenance.json").write_text(
        json.dumps(
            {
                "base_model": model,
                "adapter": str(args.adapter.resolve()),
                "format": "merged nonquantized safetensors; still requires MLC conversion for WebLLM",
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Merged model: {args.output}. This is not yet a WebLLM model.")


if __name__ == "__main__":
    main()
