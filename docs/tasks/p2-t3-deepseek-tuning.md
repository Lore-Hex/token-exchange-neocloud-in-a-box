# P2.T3: Explicit DeepSeek execution experiments

Status: **implemented and CPU-tested, not GPU-validated or deployed**. These
profiles are candidates for measurement, not measured tok/s improvements.
They use the exact [selected abliterated checkpoint](../operators/dealign-checkpoint.md).
The existing baseline and every confidential profile remain unchanged.

## Why the baseline is slow by design

The [ordinary correctness experiment](p2-t3-nonconfidential-baseline.md) uses
TP4 x DP2, one running sequence per rank, eager execution, 4096 context and
4096 batched tokens. Those are startup/correctness bounds, not production
throughput settings. Its ten short correctness requests cannot establish
sustained tokens/second or batching efficiency.

## Source review

Read October 24, 2026:

- [vLLM's October 7 performance explanation](https://vllm.ai/blog/2026-10-07-deepseek-v41-flash)
  distinguishes low-latency TP4/FlashInfer from high-throughput DEP/MegaAttention.
  It explains graph replay and SWA replay; published speedups are external
  measurements, not results for our hardware or modified weights. SWA bounded
  replay is approximate and needs a quality check, not a bit-equality claim.
- [Pinned recipe e28104e0](https://github.com/vllm-project/recipes/blob/e28104e0e9204b86e9f74686c232dc4a92af03e3/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml):
  `features.spec_decoding` specifies five-token DSpark, probabilistic drafting,
  block rejection and NVIDIA adaptive verification. Its larger scheduler
  limits and offloaded TP2 configuration are not copied into these experiments.
- [Pinned engine 5df63a72](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be):
  `vllm/config/{compilation,scheduler,speculative}.py` defines the exact flags.
  `FULL_DECODE_ONLY` captures decode without requiring piecewise prefill
  compilation. `SpeculativeConfig` resolves an omitted DSpark model to the
  target checkpoint, so no alternate draft weights are downloaded.
- [DeepInfer 9bd9cf24](https://github.com/deepseek-ai/DeepInfer/blob/9bd9cf243fc53ef9afbf8f04d391d989117d6ab9/README.md)
  currently supplies offline inference, not a complete serving system. Its
  published decode benchmark uses 64 GPUs. Switching engines is a separate
  quality, integration and performance comparison, not a free improvement.

The supplied X post was inaccessible during this review. No unseen post
content is treated as evidence; the primary vLLM article, recipe and source
above are the basis for the changes.

## Closed profiles

| Selection | Sequences per DP rank | Decode graph rows | Drafting |
| --- | --- | --- | --- |
| No flag: existing baseline | 1 | Eager | None |
| `batch8-eager` | 8 | Eager | None |
| `batch8-graphs` | 8 | 1, 2, 4, 8 | None |
| `batch8-dspark` | 8 | 6, 12, 24, 40, 48 | Five-token DSpark, real block rejection |

The graph sizes are bounded experiment choices, not upstream measured
presets: eight requests times six target positions gives 48 rows; the
drafter needs five positions per request and its own 40-row coverage. Without
40, the drafter rounds 48 to 50, excludes it above its 40-row cap, and falls
back to eager at six through eight requests. CPU regression tests execute
unchanged candidate-building and dispatch definitions from the pinned vLLM
`cudagraph_utils.py`, including variable-length adaptive target queries.
These are graph-selection tests, not GPU capture or speed measurements.
These steps isolate batching, graph replay, and drafting. DSpark acceptance
and quality must be checked on the **abliterated** weights, not inferred from
the original model. Synthetic acceptance is never enabled.

`scripts/deepseek_tuning.py` owns selection, scope validation and argv
transformation. Both preparation and runtime use that source-bound helper;
there is no second launch command to drift. Changes replace the one existing
flag rather than adding last-wins duplicates. Prelaunch checks hash the
three audited upstream configuration files and graph-candidate implementation
before starting the engine.

All profiles retain GPU-resident Engram, the same attention backend and KV
dtype, TP4 x DP2, disabled custom/fused collectives, 512 GiB host memory/no
added swap, offline weights and `--network none`. They change neither CUDA
driver nor image pins. No confidential profile can select them. Nothing
changes the source/weight verification, cleanup deadlines, request budget,
logging restrictions or production qualification gates.

## Prepare and verify

From a clean reviewed commit, with the existing pinned build prerequisites:

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-nonconfidential-uncensored-b300-dp2 "$NEW_PRIVATE_BUNDLE" \
  --model-profile uncensored \
  --execution-profile batch8-graphs
```

Use a new private bundle for each selection. The profile is recorded in the
hashed contract, wrapper and final evidence provenance. It cannot be selected
or changed at `--run` time. Unknown names or combinations fail closed. Use
the [existing bounded run procedure](p2-t3-nonconfidential-baseline.md), only
on an approved node after complete persistent-weight verification. A profile
does not authorize rental, restart, public serving or an automatic retry.

The runtime still runs the unchanged correctness smoke. Before promoting a
profile, require correctness, a separate agent-quality comparison, and a
controlled sustained benchmark at concurrency 1/2/4/8/16. Keep weights,
hardware, prompts, output budget and cache state identical; separate warmup
from measurements. Report actual usage-based aggregate output tok/s, per-GPU
tok/s, TTFT and inter-token latency, errors, cache reads and memory. Never
count speculative draft tokens as customer output. Report cold and warm
prefix-cache runs separately. No such concurrent benchmark or GPU result is
claimed by this change; the existing confidential-only stream cohort is
deliberately not enabled for ordinary nodes.
