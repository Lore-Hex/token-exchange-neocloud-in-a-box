# DeepSeek Lower-Memory Functional Pass

Committed producer `9e1eb42441427e07375f3347c822a58d150882e2` completed the same
eight v3 smoke cases and ten requests on four selected B300 GPUs on
October 22, 2026, from 01:04:32 to 01:22:45 UTC. This producer caps the
inference container at **512 GiB with no additional swap**, instead of 768 GiB.
Its launcher requires Docker's configured memory and memory-swap limits to
match before running the engine; this is not a measured peak-RSS claim.

All eight cases passed, including both two-turn tool interactions. Ready took
764,162 ms and the request phase took 21,165 ms. The container exited zero,
reported no OOM, and cleanup was confirmed. These tiny fixed cases are not a
throughput measurement or quality benchmark.

The original [run](../../evidence/p2/2026-10-22-b300-bounded-memory/run.json)
and [output](../../evidence/p2/2026-10-22-b300-bounded-memory/output.log) are
preserved byte-for-byte and hash-pinned by the shared evidence test. They keep
the fail-closed outer result: `fail`, `BLOCKED`, not hardware-qualified,
deployable or confidential. Only `functional_pass` is true.

This supports the host-memory budget used by the
[bounded lab profile](p2-t3-bounded-lab-memory.md). It does not prove a smaller
B200's GPU-memory fit, eight-GPU DP2 capacity, long-context behavior, streaming
front-door conformance, tau quality, immutable boot, or CPU/GPU/service
attestation. Those remain separate gates. No provider listing was enabled.
