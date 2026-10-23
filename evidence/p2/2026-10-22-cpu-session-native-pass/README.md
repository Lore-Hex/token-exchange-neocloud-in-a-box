# Native Held CPU Pair: Readiness and Release

**Held-pair lifecycle PASS, not tau readiness, live admission or quality.**
[Workflow 39871047432 attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39871047432)
ran exact producer `b04f5e4ec2b0fd0b1ac24710fc259247bad2a039` on native
GitHub-hosted Linux/amd64. Controller recording: **2026-10-22 23:20:44-23:24:51 UTC**;
inner recording: 23:20:45-23:24:51 UTC. The workflow completed at 23:24:55 UTC.
All eight files below are unchanged downloaded originals, not reconstructed
receipts. The artifact ZIP digest was
`198a1510809def9ebda5a38bd8fe97bc205e1be271c8fe40235156bac8537e2c`.

| Original File | SHA-256 |
| --- | --- |
| `run.json` | `75298c343ea3abd31478061b08912e6aef54c7ae4d6e5ba9de76e6ba937722db` |
| `output.log` | `3bd81cfa4a5d5bca66e24b9fc5cf040daf4ab2911051d065956b68cf743242a5` |
| `pair/run.json` | `a2d5cc53517b40af18484dc87f80a10126fd3f6220689e54217ae84de06aa711` |
| `pair/output.log` | `00d89030af95a7752264453b99150f16ab53e32fe43747ccfc31ecd0a533877a` |
| `pair/simulator-runtime.json` | `cab470331a9ddd44db922a6a2db7f741f6e3d5741835f551a3b2c29a04acbbee` |
| `pair/simulator-tokenizer.json` | `de96abc426d3e94cfbc45736e27641c1498220325c57b08306d8eaa98728300f` |
| `pair/evaluator-runtime.json` | `9469cc8a954380237457a47bb4a4cce902f93daef18d86da0a2e32425011f991` |
| `pair/evaluator-tokenizer.json` | `132e5b388cb51278d647d7e1b9627754c5bf4d2537450a749b774314423e430d` |

The original controller binds 27 source files and the inner recorder binds 23
at this producer. Later prompt-probe code is not substituted into those
closures. The pinned llama.cpp revision is
`620d2225cc436ab0b001e3200e590e3b286ef9ab`; both distinct authenticated GGUFs,
built helper/server, argv, PID/start-time, namespace and listener identities
remain bound by the original receipts.

| Model | Text | System | Tools | Tool Continuation | Ready |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3-0.6B Q8_0 | 20 | 28 | 160 | 198 | 585 ms |
| Qwen3-1.7B Q8_0 | 20 | 28 | 160 | 198 | 920 ms |

Each count matches the independently invoked pinned vocabulary/template helper,
endpoint token-ID hash/count and generated `usage.prompt_tokens`; each generation
used one completion token. This is independent invocation of the same pinned
common library, not an independent tokenizer implementation. Only the four fixed
synthetic probes were measured, not arbitrary histories or real tau prompts.

Unlike the [earlier sequential pass](../2026-10-22-cpu-parity-native-pass/README.md),
the first server remained alive while the second was prepared. Both had distinct
PIDs, ports and listener inodes when the source-bound READY frame was admitted.
The controller verified all four fresh original receipts, sent the digest-bound
release, and required successful terminal records. The worker reports
`acknowledged: true`, stage `released`, no failure, and both-model parity PASS.
All child, producer/process-group, container/image absence and image-build
cleanup checks are confirmed. Both servers are now stopped; these historical
receipts are **not reusable for live admission**.

The reviewed bounds were unchanged: 4 CPUs, 8 GiB/no swap, 128 PIDs, UID/GID
65532, no capabilities, no-new-privileges, seccomp, network-none/loopback only,
no GPUs, context 4096 and output cap 256. Controller/runtime ceilings remained
2280/1800 seconds (worker cleanup reserve 60 seconds), record/job ceilings
40/60 minutes. No peak-memory, sustained-load or throughput measurement is
claimed. Downloaded input/base caches are not claimed deleted.

The [earlier failed attempt](../2026-10-22-cpu-session-parity-failure/) is preserved
unchanged. Its broad `parity` stage did not retain a model, substage or exception;
its runtime cause remains **UNKNOWN and unreproduced**. This pass does not prove
the diagnostic-only changes fixed a runtime defect. The separate controller
host-schema omission was fixed for this run without rewriting the older original.

`live_admission`, `quality_eligible`, `confidential` and `hardware_validated`
remain false. This is not a target integration, real tau trial, original-prompt
fit/evaluator-output result, boot, confidential-hardware, release or
reproducibility proof. See the [session contract](../../../docs/tasks/p2-t3-tau-cpu-session.md)
and [controller contract](../../../docs/tasks/p2-t3-tau-cpu-controller.md).

Tests pin all original bytes and historical source hashes, replay the closed
session/receipt schemas at the recorded timestamp without live kernel checks,
verify the original READY and receipt-byte joins, reject claim mutations and
expired readiness, and preserve the prior failure. Scanner allowances bind only
the exact public digest/field/path combinations. Compact JSON uses match-level,
not whole-line, exceptions so an unrelated secret on the same line still fails.
