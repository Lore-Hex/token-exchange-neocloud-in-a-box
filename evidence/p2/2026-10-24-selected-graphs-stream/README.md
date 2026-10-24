# Selected Checkpoint: Graph Stream Measurement

Measured 2026-10-24 13:18:07-13:38:44 UTC by committed producer
`5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1` on eight ordinary B300 GPUs,
with CC disabled. Selected checkpoint:
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`.

- Fixed `batch8-graphs` profile: TP4 x DP2, 4,096 context, maximum eight
  sequences, 512 GiB process memory limit, no swap. Actual request concurrency
  was one, not eight. Engine readiness took 903,126 ms.
- All four tokenizer parity cases passed, followed by one warmup and eight
  measured sequential streaming requests. All eight completed with zero errors.
- Each request used 2,048 input tokens and returned 400 actual output tokens
  with a 512-token cap. The measured cohort returned 3,200 output tokens in
  22.320929104 seconds: **143.36 output tokens/second overall**,
  17.92 output tokens/second/GPU. Median TTFT was **98.61 ms**; p95 was 117.70 ms.
- The separate warmup returned 400 output tokens in 6.916219717 seconds and
  is excluded from those measured rates and latency percentiles. No partial
  output or configured token caps are counted as generated usage.
- API runtime identity was stable before and after the cohort. Engine stopped,
  cleanup confirmed, no OOM.

This small synthetic cohort is not a capacity/concurrency sweep, sustained
throughput guarantee, tau score, quality acceptance, pricing measurement or
confidential qualification. Original outer FAIL/BLOCKED and all false
deployment, confidentiality and hardware-qualification flags are preserved.

[run.json](run.json), [output.log](output.log), [receipt.json](receipt.json) and
[input-contract.json](input-contract.json) preserve the original bytes; only
`payload/lab.json` was renamed to `input-contract.json`.
[Regression tests](../../../scripts/test_selected_operational_evidence.py) pin
all four hashes and source/receipt joins, run the original parity/runtime/stream
validators, recompute the rate and reject fabricated usage or quality flags.
