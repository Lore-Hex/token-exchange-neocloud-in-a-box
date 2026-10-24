# Selected Checkpoint: DSpark Cause Diagnostics

Failed run, 2026-10-24 16:19:43-16:29:42 UTC, committed producer
`e627eac092f42fdeb172aab747f71bd5d7d61660`. Eight ordinary B300 GPUs,
CC disabled; selected checkpoint
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`.

- Same fixed `batch8-dspark` profile: TP4 x DP2, 4,096 context, 512 GiB
  process memory limit, no swap. This increment added bounded diagnostics,
  not a new engine flag or kernel change.
- Engine child exited 1 during startup after 323,065 ms and 323 health probes.
  No inference or correctness measurement was produced.
- The worker v2 diagnostic preserves two linked exceptions: an `accelerator`
  exception with `context: 1`, pointing to the preceding `runtime` exception.
  Both explicit `cause` links are null. Each node retains eight frames and is
  marked truncated; the two-node chain itself is not marked truncated.
- Source-mapped context ends in parallel attention projection at source
  SHA-256 `dd146642fc7d7034f32feecf35ff4d7d9460f59a8a57444c0dd6f367fe8f15fb`,
  line 955. The later accelerator exception ends in graph-profile cleanup/
  synchronization at `5258e4552792fc48a9058766d3f7892d1a508bd1b0dbd567ca3af140e270dccc`,
  line 270. These locations and the context link do not prove a specific CUDA
  or kernel defect. **The precise root cause remains unknown.**
- Engine stopped and cleanup confirmed; recorder container exit zero and no
  OOM do not override the engine child's failed startup.

The content-free report contains coarse exception types, source hashes and
line numbers, not exception messages or request data. Functional failure and
outer FAIL/BLOCKED remain unchanged. No DSpark throughput, tau, quality,
deployability or confidentiality is established.

[run.json](run.json), [output.log](output.log), [receipt.json](receipt.json) and
[input-contract.json](input-contract.json) preserve the original bytes; only
`payload/lab.json` was renamed to `input-contract.json`.
[Regression tests](../../../scripts/test_selected_operational_evidence.py) pin
all four hashes and original producer/receipt joins, validate the v2 chain with
that producer's validator, and reject invalid links and message-bearing nodes.
