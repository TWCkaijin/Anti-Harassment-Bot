"""Download public base weights or just tokenizer files; record the resolved revision."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revision", default="main", help="Use a commit SHA to reproduce a previous download"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent / "models/base"
    )
    parser.add_argument("--tokenizer-only", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit("Use a new output directory; existing snapshots are preserved")
    from huggingface_hub import HfApi, snapshot_download

    repo = "Qwen/Qwen3-0.6B"
    revision = HfApi().model_info(repo, revision=args.revision).sha
    patterns = [
        "config.json",
        "tokenizer*",
        "vocab.json",
        "merges.txt",
        "*.jinja",
        "generation_config.json",
        "LICENSE*",
    ]
    if not args.tokenizer_only:
        patterns += ["*.safetensors", "*.safetensors.index.json"]
    snapshot_download(repo, revision=revision, allow_patterns=patterns, local_dir=args.output)
    (args.output / "download.json").write_text(
        json.dumps(
            {"repo": repo, "revision": revision, "tokenizer_only": args.tokenizer_only}, indent=2
        )
        + "\n"
    )
    print(f"Downloaded {repo}@{revision} to {args.output}")


if __name__ == "__main__":
    main()
