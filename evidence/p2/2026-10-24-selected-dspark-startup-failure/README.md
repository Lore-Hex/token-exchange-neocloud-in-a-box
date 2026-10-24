# Selected Checkpoint: DSpark Startup Failure

Failed run, 2026-10-24 13:46:23-13:56:24 UTC, committed producer
`5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1`. Eight ordinary B300 GPUs,
CC disabled; selected checkpoint
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`.

- Fixed `batch8-dspark` profile: TP4 x DP2, 4,096 context, 512 GiB process
  memory limit, no swap.
- Engine child exited 1 during startup after 331,044 ms and 331 health probes.
  No correctness run, completion or inference throughput was produced.
- The legacy bounded worker diagnostic retained eight source-hash/line frames,
  marked truncated, with coarse type `other`. Its final retained frame is
  source SHA-256 `5258e4552792fc48a9058766d3f7892d1a508bd1b0dbd567ca3af140e270dccc`,
  line 270, mapped to graph-profile teardown/synchronization. That location
  does not establish the underlying accelerator failure's cause.
- Engine stopped and container cleanup confirmed. The recorder container
  exited zero without OOM; that is not engine startup success.

Original functional failure and outer FAIL/BLOCKED remain intact. No DSpark
tokens/second, quality, tau, deployment or confidential success is claimed.
The [separate cause-chain diagnostic run](../2026-10-24-selected-dspark-cause-diagnostics/README.md)
retains additional context, not a successful retry or a proven precise cause.

[run.json](run.json), [output.log](output.log), [receipt.json](receipt.json) and
[input-contract.json](input-contract.json) preserve the original bytes; only
`payload/lab.json` was renamed to `input-contract.json`.
[Regression tests](../../../scripts/test_selected_operational_evidence.py) pin
all four hashes, join the original producer sources and receipt, and validate
the original legacy diagnostic without rewriting it to the newer format.
