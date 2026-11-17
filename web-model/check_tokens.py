"""Check Qwen chat templates and every completion boundary without loading weights."""

import argparse
import json

from common import CompletionDataset, ROOT, read_jsonl, render_prompt, messages_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default=str(ROOT / "models/base"), help="Local tokenizer/model snapshot"
    )
    parser.add_argument("--max-length", type=int, default=2048)
    args = parser.parse_args()
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=False)
    prompt = render_prompt(tokenizer, messages_for("測試資料"))
    if not prompt.endswith("<think>\n\n</think>\n\n"):
        raise ValueError(
            "Unexpected Qwen3 non-thinking prefix; review chat template before training"
        )
    result = {}
    for split in ("train", "valid", "test"):
        dataset = CompletionDataset(
            read_jsonl(ROOT / f"data/{split}.jsonl"), tokenizer, args.max_length
        )
        for tokens, offset in dataset.samples:
            assert tokenizer.decode(tokens[offset:-1]).startswith("{"), (
                "Loss must begin at JSON answer"
            )
            assert tokens[-1] == tokenizer.eos_token_id
        result[split] = {
            "examples": len(dataset),
            "max_tokens": max(len(tokens) for tokens, _ in dataset.samples),
            "min_completion_tokens": min(
                len(tokens) - offset for tokens, offset in dataset.samples
            ),
        }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
