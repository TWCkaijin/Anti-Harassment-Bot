# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["torch==2.15.0", "transformers==4.57.6", "onnx==1.23.2"]
# ///
"""Export the pinned CKIP model for the local Admin masking lab."""

import argparse
import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlretrieve

import torch
from transformers import AutoModelForTokenClassification

REVISION = "bcb519856ca93a666b1e48a9daef3f88c9b572a0"
SOURCE = f"https://huggingface.co/ckiplab/albert-tiny-chinese-ner/resolve/{REVISION}/"
WEIGHTS_SHA256 = "03f6e38f92ada4b59b88ae9122a50a0c98b85f07722ff1234048928e55ed10d3"
FILES = ("pytorch_model.bin", "config.json", "vocab.txt")
DESTINATION = Path(__file__).resolve().parents[1] / ".masking-tiny" / REVISION


class Logits(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, input_ids, attention_mask, token_type_ids):
        return self.model(
            input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids
        ).logits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, help="Use a local copy of the pinned source artifacts"
    )
    args = parser.parse_args()
    torch.set_num_threads(1)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=DESTINATION.parent) as scratch:
        directory = Path(scratch)
        for name in FILES:
            if args.source:
                shutil.copyfile(args.source / name, directory / name)
            else:
                urlretrieve(SOURCE + name, directory / name)
        with (directory / FILES[0]).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != WEIGHTS_SHA256:
                raise SystemExit("CKIP source weights SHA-256 did not match")
        model = AutoModelForTokenClassification.from_pretrained(
            directory,
            local_files_only=True,
            weights_only=True,
            attn_implementation="eager",
        ).eval()
        # These BIOES label IDs are part of the browser decoder contract.
        for start, label in ((14, "PERSON"), (12, "ORG")):
            assert [model.config.id2label[start + i * 18] for i in range(4)] == [
                f"{tag}-{label}" for tag in "BIES"
            ]
        ids = torch.tensor([[101, 1920, 102]], dtype=torch.int64)
        names = ("input_ids", "attention_mask", "token_type_ids")
        torch.onnx.export(
            Logits(model).eval(),
            (ids, torch.ones_like(ids), torch.zeros_like(ids)),
            str(directory / "model.onnx"),
            input_names=list(names),
            output_names=["logits"],
            dynamic_axes={name: {0: "batch", 1: "length"} for name in (*names, "logits")},
            opset_version=17,
            dynamo=False,
        )
        DESTINATION.mkdir(exist_ok=True)
        for name in ("vocab.txt", "model.onnx"):
            (directory / name).replace(DESTINATION / name)
    print(f"Prepared Tiny NER assets in {DESTINATION}")


if __name__ == "__main__":
    main()
