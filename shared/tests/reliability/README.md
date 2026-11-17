# Synthetic Gate evaluation

Dry run validates the packaged pre and post cases without external model calls:

```sh
PYTHONPATH=shared/python .venv/bin/python -m shared.tests.reliability.gate_eval
```

An explicit live run uses only the packaged synthetic cases and incurs OpenRouter calls:

```sh
PYTHONPATH=shared/python .venv/bin/python -m shared.tests.reliability.gate_eval --live --model MODEL_ID --output /tmp/gate-eval.json
```

The default evaluator uses separate pre and post policies. The report contains case IDs,
decisions, errors, latency, and a hash for each stage in `policy_hashes` (`policy_hash`
remains the pre-policy hash for older report consumers). To test only the post policy,
pass `--cases-file shared/tests/reliability/gate_post_probe_cases.json`; to compare a
candidate post policy, add `--post-prompt-file PATH`. The legacy `--prompt-file PATH`
applies the same prompt to both stages.

The report does not
store case text or model replies. Gate errors are counted as unavailable because the
chat service hides the candidate and offers retry. This small case set checks basic
contrasts; a successful score alone does not establish safety for real conversations.
