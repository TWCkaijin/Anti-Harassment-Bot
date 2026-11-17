"""Small MLX training backend, imported only when actual training is requested."""

import json
import math
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx_lm.tuner.trainer import TrainingArgs, train
from mlx_lm.tuner.utils import linear_to_lora_layers, print_trainable_parameters


def completion_loss(model, batch, lengths):
    """Token positions refer to the unshifted sequence; end is exclusive.

    For a prompt of P tokens and total length L including EOS, score positions
    P..L-1. This includes the first answer token and EOS but excludes all padding.
    """
    logits = model(batch[:, :-1])
    positions = mx.arange(1, batch.shape[1])
    mask = (positions >= lengths[:, 0:1]) & (positions < lengths[:, 1:])
    losses = nn.losses.cross_entropy(logits, batch[:, 1:])
    count = mask.sum()
    return (losses * mask).astype(mx.float32).sum() / count, count


class LocalLog:
    def __init__(self, path):
        self.path = Path(path)

    def write(self, stage, values):
        loss = values.get("train_loss", values.get("val_loss"))
        if loss is not None and not math.isfinite(loss):
            raise FloatingPointError(f"Non-finite {stage} loss; training stopped")
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"stage": stage, **values}) + "\n")

    def on_train_loss_report(self, values):
        self.write("train", values)

    def on_val_loss_report(self, values):
        self.write("valid", values)


def train_adapter(config, model, train_set, valid_set):
    mx.random.seed(config["seed"])
    model.freeze()
    if config["num_layers"] > len(model.layers) or config["num_layers"] == 0:
        raise ValueError("num_layers must be -1 or a valid positive layer count")
    linear_to_lora_layers(model, config["num_layers"], config["lora_parameters"])
    print_trainable_parameters(model)
    output = Path(config["adapter_path"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "adapter_config.json").write_text(
        json.dumps(config, indent=2) + "\n", encoding="utf-8"
    )
    args = TrainingArgs(
        **{
            key: config[key]
            for key in [
                "batch_size",
                "iters",
                "val_batches",
                "steps_per_report",
                "steps_per_eval",
                "max_seq_length",
                "grad_checkpoint",
                "grad_accumulation_steps",
            ]
        },
        steps_per_save=config["save_every"],
        adapter_file=output / "adapters.safetensors",
    )
    optimizer = optim.AdamW(learning_rate=config["learning_rate"], weight_decay=0.01)
    train(
        model=model,
        optimizer=optimizer,
        train_dataset=train_set.samples,
        val_dataset=valid_set.samples,
        args=args,
        loss=completion_loss,
        training_callback=LocalLog(output / "loss.jsonl"),
    )
