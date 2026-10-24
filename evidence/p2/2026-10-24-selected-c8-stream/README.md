# Selected Checkpoint: Eight Concurrent Streams

Measured 2026-10-24 21:12:36-21:32:44 UTC by committed producer
`a53d31450b5ce801596218b9988b31f44025c15e` on eight ordinary B300 GPUs,
with confidential computing disabled. Selected checkpoint:
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`.

- Fixed `batch8-graphs` engine profile: TP4 x DP2, 4,096 context, eight
  sequences, 512 GiB process memory limit and no swap. Startup took 892,130 ms.
- Four tokenizer parity cases passed. One warmup preceded eight measured
  streaming requests, with observed peak concurrency eight. Every request
  completed; all usage was known and all collectors were reaped.
- Each request used 2,048 input tokens and returned 400 actual output tokens
  under a 512-token cap. The measured burst returned 3,200 output tokens in
  4.959102935 seconds: **645.28 output tokens/second overall**, or 80.66/GPU.
  This rate includes dispatch, prefill and collection/cleanup, not just decode.
- Median TTFT was **1,835.91 ms**, p95 1,933.63 ms. The prior sequential
  [graph run](../2026-10-24-selected-graphs-stream/README.md) measured
  143.36 output tokens/second and 98.61 ms median TTFT. More concurrent work
  improved aggregate throughput but increased latency in these small cohorts.
  They are not a controlled sustained-load comparison or a service guarantee.
- The separate warmup returned 400 output tokens in 6.821974958 seconds and
  is excluded from measured throughput and latency percentiles. Cached-token
  counts are unknown, not zero; configured caps are not generated token usage.
- Runtime identity remained stable. Engine and container exited, cleanup was
  confirmed, and the container was not OOM-killed.

This is one synthetic burst, not a complete capacity sweep, tau score,
quality acceptance, price measurement or confidential qualification. Original
outer FAIL/BLOCKED and false deployment/hardware/confidential flags remain.
No retries, deadline extension or service configuration changes were made.

[run.json](run.json), [output.log](output.log), [receipt.json](receipt.json) and
[input-contract.json](input-contract.json) preserve original bytes. The input
contract and receipt came from the exact executed bundle; `payload/lab.json`
is renamed to `input-contract.json`, not regenerated from current source.
[Tests](../../../scripts/test_selected_operational_evidence.py) join all
hashes to producer source, invoke its original validators, recompute rates,
check actual request overlap and reject invented usage/qualification.
