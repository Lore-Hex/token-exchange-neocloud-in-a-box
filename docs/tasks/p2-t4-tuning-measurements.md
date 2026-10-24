# Comparable Tuning Measurements

Status: software-tested integration, not measured performance. The existing
ordinary B300 TP4 x DP2 execution profiles can opt into the complete existing
[measurement cohort](p2-t3-ordinary-measurement.md). No engine flags, model
weights, workloads, deadlines, context or memory limits change here.

After baseline correctness passes, prepare each separately approved experiment
from its clean reviewed commit:

```sh
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-nonconfidential-uncensored-b300-dp2 "$PRIVATE_BUNDLE" \
  --model-profile uncensored --execution-profile batch8-graphs \
  --tokenizer-parity --tokenizer-materialization --runtime-observation \
  --stream-cohort --stream-profile chat-2k-512-c1-600s
```

Use the same command without `--execution-profile` for the ordinary baseline,
or select the already reviewed `batch8-eager` and `batch8-dspark` experiments.
These are individual bounded runs, not a sweep or an automatic retry loop.
Retain every result, including failures, and compare only completed cohorts
with matching model, workload and measurement settings.

The four measurement options must be selected together with tuning; partial
selections and provider conformance are rejected before execution. The owner
authenticates the tuning helper and its exact canonical argv before launching
any engine. Existing tokenizer, process and streaming workers retain their
own input checks. The historical CPU parity assertion still verifies all
helper bytes outside eligibility against its original revision.

Each run uses four fixed tokenizer cases followed by one warmup and eight
measured requests, at concurrency one, 2,048 input tokens and at most 512
output tokens. A scheduler configured for eight sequences does not turn this
into a concurrency-eight benchmark. Timings and usage come from the same
collector, including unknown-usage accounting, deadlines and cleanup.

GPU-resident Engram, 4,096-token context, 512 GiB memory without extra swap,
closed networking and the eight-device CC-disabled check are unchanged.
Every result remains `confidential=false` and `deployable=false`. No quality,
tau2, production, concurrent-load or confidential qualification is implied.

Focused checks: `test_deepseek_tuning.py`, `test_deepseek_model_profile.py`,
`test_stream_lab.py`, and `test_tau_cpu_parity.py`; also run `make check`.

The separate [8K tau prerequisite](p2-t4-tau-context.md) is not a streaming
performance profile and cannot be combined with these cohorts.
