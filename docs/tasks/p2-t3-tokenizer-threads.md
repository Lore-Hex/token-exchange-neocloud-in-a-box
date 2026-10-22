# P2.T3: Bound tokenizer startup parallelism

## Failure and scope

The eight-GPU TP4/DP2 lab at source
`34f99da7d8965fa993512a7f8b51d3c450aee47f` failed before readiness on
2026-10-22. Eight B300 CC-mode observations, the full weights check and the
application integrity gate passed. A worker exited during multimodal dummy-input
profiling after 315109 ms of engine startup. No OOM was reported, and the
supervisor confirmed engine stop and container cleanup. This is not a DP2,
quality, CPU attestation or confidential-inference pass.

Original failed artifacts are retained in
[`evidence/p2/2026-10-22-dp2-tokenizer-fail`](../../evidence/p2/2026-10-22-dp2-tokenizer-fail).
The earlier staging-parent path error is retained separately in
[`evidence/p2/2026-10-22-dp2-pack-path-fail`](../../evidence/p2/2026-10-22-dp2-pack-path-fail).

The last two source hashes in the worker exception match the exact public
Transformers 5.19.0 files:

- [`tokenization_utils_base.py`](https://github.com/huggingface/transformers/blob/v5.19.0/src/transformers/tokenization_utils_base.py#L2279):
  `d683db2cf815e9a524edf691c10c7f2a05f408cd8d52a7036724403181bb6ee0`.
- [`tokenization_utils_tokenizers.py`](https://github.com/huggingface/transformers/blob/v5.19.0/src/transformers/tokenization_utils_tokenizers.py#L1063):
  `32e4ff002092023720bdbe9101e01005a4a149a0729c82e05d7dbf497cbaa388`.
  Line 1063 calls the Rust tokenizer's `encode_batch`.

The exception category is `other`, not proof of a particular Rust panic or
cgroup exhaustion. Per-process Rayon thread-pool creation is the leading
hypothesis; the original recorder did not capture the native panic or cgroup
PID event count, so do not claim that cause as measured.

## Change

Set `TOKENIZERS_PARALLELISM=false` in the fixed lab environment, before the
initial interpreter and every spawned worker. Upstream
[`tokenizers` v0.23.3 parallelism.rs](https://github.com/huggingface/tokenizers/blob/v0.23.3/tokenizers/src/utils/parallelism.rs)
defaults to enabled and explicitly selects serial iterators for `false`.
The tokenizer implementation, weights, token IDs and protocol are unchanged.
The lab's process cap remains 1024; no memory, network or privilege limit is
relaxed. This is a bounded startup change, not a production performance claim.

Tests prove the fixed setting overrides both ambient and plan-provided values,
is carried by the isolated spawn wrapper, and preserves the existing DP1/DP2
and confinement contracts. A new real DP2 run is required; passing unit tests
does not resolve the recorded hardware failure.
