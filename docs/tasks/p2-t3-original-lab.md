# P2.T3: Official DeepSeek ordinary lab identity

Status: implemented for offline preparation and synthetic boundary testing.
No GPU execution, quality, throughput, confidential-computing or public-serving
claim follows from this change. The separately bound
[official service runtime](p2-t3-original-runtime.md) is a required preparation
step, not proof of a completed migration.

The existing official pack pins
[`deepseek-ai/DeepSeek-V4.1-Flash-1017`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/tree/1bae0e1088993b9b82de08f59de5be5a34e42170)
at revision `1bae0e1088993b9b82de08f59de5be5a34e42170`, with
510311045295 bytes and manifest SHA-256
`b74ae635c35372cf6c350e4b33ef60c90ff01fe6426ad0fa7bc99b1982fe5bed`.
The abliterated checkpoint is a different identity, not a serving alias.

## Explicit Preparation

The new preparation action requires all of these options:

```sh
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-nonconfidential-original-b300-dp2 /operator/private/new-bundle \
  --model-profile original \
  --execution-profile original-long1m-graphs \
  --tokenizer-parity --tokenizer-materialization --runtime-observation
```

Use the existing preparation prerequisites and committed source/image closure;
this command does not download weights, start a GPU or publish an endpoint.
The resulting lab contract carries the complete original model identity.
Its manifest, revision, byte count, model-profile source and launch arguments are
validated together. Existing implicit-original legacy payloads remain unchanged.
Existing selected profiles still require their selected checkpoint.

## Runtime Boundary

This candidate is restricted to ordinary B300, TP4 x DP2, with one sequence per
engine configuration, Engram on GPU, a 512 GiB container memory limit and no swap.
It reuses the existing pinned 1M argument transformation: 1048576 context tokens,
4096 batched prefill tokens, decode-only graphs, explicit prefix caching and
actual prompt-token cache accounting. The source basis remains
[vLLM 5df63a7](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be),
including the hash-pinned scheduler, compilation, cache and frontend definitions
in `scripts/deepseek_tuning.py`. No new engine flag or upstream pin is introduced.

The configured context is not fresh hardware evidence. No output limit, cache
discount, concurrency qualification or production tariff is established here.
Tokenizer and runtime prerequisites remain mandatory. Stream cohorts, provider
checker composition and tau owners are excluded from this new lab preparation.
They must not inherit results from the abliterated checkpoint.

## Validation Remaining

`scripts/test_original_deepseek_lab.py` exercises the host/runtime identity join,
unchanged execution flags, CLI requirements, memory/swap limits, wrong-manifest
rejection, implicit-selection rejection and confidential/selected crossover.
The tests use synthetic sources and counters, not hardware receipts.

Before a serving cutover: independently review the final source, pass full CI,
stage and hash the official pack without overwriting live inputs, prepare the
source-bound public owner/image and canonical model identity, then run bounded
readiness, conformance, code-scored quality and cache checks. Coordinate the
catalog update with that verified serving identity and a rollback plan. Preserve
all historical selected-checkpoint evidence without relabeling it.
