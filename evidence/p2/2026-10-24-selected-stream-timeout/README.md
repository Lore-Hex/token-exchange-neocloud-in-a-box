# Selected Checkpoint: Streaming Warmup Timeout

Original producer `5b4e2f54f2b9c14ff46fb9c1efcfbcdbf37b49f1` ran from
2026-10-24 09:53:09 to 10:20:15 UTC on eight ordinary B300 GPUs.
The four artifacts are copied byte for byte from the run and its input bundle.

The exact dealign checkpoint, revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, had already passed the separate
[correctness run](../2026-10-24-ordinary-selected-correctness/README.md).
This experiment retained eager TP4 x DP2, one sequence per replica, 4096
context, GPU-resident Engram and the same fixed image/source inputs. It did not
enable graph replay, DSpark, concurrent load or public serving.

| Observation | Result |
| --- | --- |
| Startup | Ready after 1,163,493 ms |
| Tokenizer controls | All four pass, eight requests; counts 12, 16, 275, 332 |
| Workload | Exact 2048-token inputs, max 512 output, one warmup plus eight planned measured requests |
| Warmup | Timeout after 60.0002 seconds; 371 output events, no terminal usage |
| Measured cohort | Not started; zero samples, no throughput or TTFT result |
| Cleanup | Engine stopped; container exit 0, no OOM; removal confirmed |

Output events are not token counts. Unknown usage remains unknown; the zero
known-token counters do not mean the request consumed zero tokens. Cache reads
are also unknown. The fixed request timeout was not extended and the incomplete
warmup was not promoted into a measurement.

The engine did not crash in this run. This result establishes a slow/incomplete
warmup under this conservative profile, not its precise kernel-level cause or
the performance of another profile. Graph replay and DSpark require their own
correctness and measured runs before any speedup claim.

`functional_pass=false`, `result=fail`, `status=BLOCKED`, `deployable=false`
and `confidential=false` remain original. GPUs reported CC disabled; CPU/GPU
attestation, tau, broad quality, concurrent throughput and public deployment
remain unproven. Tests bind the producer/source/receipt chain and replay the
original stream validator, including fabricated-success and usage mutations.
