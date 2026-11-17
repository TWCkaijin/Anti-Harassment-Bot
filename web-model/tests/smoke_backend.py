"""Optional local integration check: random TINY Qwen3, not real 0.6B training.

Requires requirements.txt. Runs on CPU, downloads nothing, writes only to a
temporary directory, and checks adapter update, reload, fusion, and loss mask.
"""

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mlx.core as mx
from mlx.utils import tree_flatten, tree_unflatten
from mlx_lm.models.qwen3 import Model, ModelArgs
from mlx_lm.tuner.utils import load_adapters
from backend import completion_loss, train_adapter
from common import ROOT


def main():
    mx.set_default_device(mx.cpu)
    mx.random.seed(42)
    model = Model(
        ModelArgs(
            model_type="qwen3",
            hidden_size=16,
            num_hidden_layers=1,
            intermediate_size=32,
            num_attention_heads=2,
            rms_norm_eps=1e-6,
            vocab_size=64,
            num_key_value_heads=1,
            max_position_embeddings=128,
            rope_theta=1000000.0,
            head_dim=8,
            tie_word_embeddings=True,
        )
    )
    base_weights = [(key, mx.array(value)) for key, value in tree_flatten(model.parameters())]
    config = json.loads((ROOT / "configs/lora.json").read_text())
    config.update(
        iters=4,
        steps_per_report=4,
        steps_per_eval=4,
        save_every=4,
        max_seq_length=64,
        val_batches=1,
        grad_checkpoint=False,
        fine_tune_type="lora",
    )
    batch = mx.array([[1, 2, 3, 4, 5, 0, 0]])
    loss, count = completion_loss(model, batch, mx.array([[3, 5]]))
    assert int(count.item()) == 2, "Must include first completion + EOS, exclude padding"
    # Changing a prompt-only target and padded logits must not add supervised positions.
    samples = SimpleNamespace(samples=[([1, 2, 3, 4, 5], 3), ([1, 3, 4, 5], 2)])
    with tempfile.TemporaryDirectory(prefix="qwen-tiny-smoke-") as temp:
        config["adapter_path"] = temp
        train_adapter(config, model, samples, samples)
        after = dict(tree_flatten(model.parameters()))
        for name, original in base_weights:
            adapted_name = name.replace(".q_proj.weight", ".q_proj.linear.weight").replace(
                ".v_proj.weight", ".v_proj.linear.weight"
            )
            assert mx.array_equal(original, after[adapted_name]).item(), (
                f"Frozen base changed: {name}"
            )
        adapter = mx.load(str(Path(temp) / "adapters.safetensors"))
        assert adapter and any(
            float(mx.abs(v).sum()) > 0 for k, v in adapter.items() if k.endswith("lora_b")
        )
        reloaded = Model(model.args)
        reloaded.load_weights(base_weights)
        load_adapters(reloaded, temp)
        model.eval()
        reloaded.eval()
        expected = model(batch)
        assert mx.allclose(expected, reloaded(batch), atol=1e-5).item()
        fused = [
            (name, module.fuse())
            for name, module in reloaded.named_modules()
            if hasattr(module, "fuse")
        ]
        reloaded.update_modules(tree_unflatten(fused))
        assert mx.allclose(expected, reloaded(batch), atol=1e-4).item()
    print(
        "PASS: tiny random Qwen3 gradient update / saved adapter / reload / fusion / exact loss mask"
    )


if __name__ == "__main__":
    main()
