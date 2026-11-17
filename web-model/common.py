"""Standalone prompt, data and strict output contracts (standard library only)."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TYPES = ("person", "organization", "address", "contact", "identifier")
PROMPTS = json.loads((ROOT / "prompts.json").read_text(encoding="utf-8"))


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(compact(row) + "\n" for row in rows), encoding="utf-8")


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def messages_for(text, candidate=None):
    if candidate is not None:
        return [
            {"role": "system", "content": PROMPTS["classify_prompt"]},
            {"role": "user", "content": compact({"text": text, "candidate": candidate})},
        ]
    result = [{"role": "system", "content": PROMPTS["locate_prompt"]}]
    for example in PROMPTS["examples"]:
        result.extend(
            [
                {"role": "user", "content": compact({"text": example["text"]})},
                {"role": "assistant", "content": compact({"spans": example["spans"]})},
            ]
        )
    return result + [{"role": "user", "content": compact({"text": text})}]


def render_prompt(tokenizer, messages):
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )


def parse_output(raw, text=None):
    prefix = "<think>\n\n</think>\n\n"
    value = json.loads(raw[len(prefix) :] if raw.startswith(prefix) else raw)
    if not isinstance(value, dict):
        raise ValueError("Output must be an object")
    if text is None:
        if set(value) != {"type"} or value["type"] not in (*TYPES, "none"):
            raise ValueError("Invalid classification")
        return value["type"]
    if set(value) != {"spans"} or not isinstance(value["spans"], list) or len(value["spans"]) > 100:
        raise ValueError("Invalid spans schema")
    for span in value["spans"]:
        if not isinstance(span, str) or not span.strip() or len(span) > 500 or span not in text:
            raise ValueError("Span is not an exact source substring")
    return list(dict.fromkeys(value["spans"]))


class CompletionDataset:
    """MLX CacheDataset interface; only the final answer and EOS contribute loss.

    Render once with thinking disabled. Do not let MLX re-wrap an already rendered
    prompt as a user message, and never train on system/few-shot/user tokens.
    """

    def __init__(self, rows, tokenizer, max_length):
        self.samples = []
        for row in rows:
            prompt = render_prompt(tokenizer, row["messages"][:-1])
            prefix = tokenizer.encode(prompt, add_special_tokens=False)
            tokens = tokenizer.encode(
                prompt + row["messages"][-1]["content"], add_special_tokens=False
            )
            if tokens[: len(prefix)] != prefix:
                raise ValueError(f"Unstable token boundary: {row['id']}")
            tokens.append(tokenizer.eos_token_id)
            if len(tokens) > max_length:
                raise ValueError(
                    f"{row['id']}: {len(tokens)} tokens exceeds {max_length}; no silent truncation"
                )
            if len(tokens) <= len(prefix) + 1:
                raise ValueError("Empty completion")
            self.samples.append((tokens, len(prefix)))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        return self.samples[index]

    def process(self, sample):
        return sample
