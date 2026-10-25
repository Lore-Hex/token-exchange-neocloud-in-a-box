# P2.T3: Bounded quality evaluation runtime

The explicit `quality32k-eager` execution profile prepares the selected ordinary
B300 DP2/TP4 runtime for longer evaluation answers: 32,768 context tokens, 2,048
output tokens and one total admitted request. It is not the default and is not
a measured capacity or quality claim. Existing `tau8k-eager` and graph profiles
retain their original limits and receipt schemas.

Only `--max-model-len` and `--max-num-batched-tokens` differ from the eager
8K engine arguments. Both become 32,768. This follows the pinned vLLM
[SchedulerConfig validation](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py):
with chunked prefill disabled, the batch-token budget must cover the maximum
model length. One sequence, eager execution, GPU-resident Engram, the original
parsers, 512 GiB host-memory limit and zero swap remain unchanged. No new vendor
flag or speculative-decoding setting is introduced.

Prepare using the existing `host/record-deepseek-lab.py`
`--prepare-nonconfidential-uncensored-b300-dp2` path with
`--model-profile uncensored --execution-profile quality32k-eager`,
`--tokenizer-parity --tokenizer-materialization --runtime-observation`.
Stream-cohort and embedded tau-owner modes are not part of this profile.
Use the selected-service image and lifecycle workflow with the same explicit
execution profile; its prepared source, guard, catalog, observed engine context
and runtime receipt must agree. Do not reuse an older image or relabel its
receipt. Ordinary and smoke lifetime bounds, drain, cancellation and ownership
checks are unchanged.

This enables the output ceiling frozen by the
[quality protocol](../benchmarks/quality-protocol.md), not the benchmark itself.
The 64 KiB request-body guard remains. An evaluator must count its exact prompt
with the pinned tokenizer, reserve the full output budget, and reject oversized
examples without silently truncating or changing the frozen subset. Record any
failure rather than dropping examples from the score. The selected abliterated
checkpoint is not a matched-revision first-party reference.

Before a live replacement: independently review the final source, pass local and
hosted checks, build a source-bound image, drain the prior owner, and verify the
new live limits and conformance. Never start a second engine on occupied GPUs.
Hardware fit, quality scores, full tau results, long-context performance and
confidential operation remain unproven by this software change.
