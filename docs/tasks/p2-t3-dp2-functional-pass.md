# P2.T3 eight-GPU DP2 functional result

The original [run](../../evidence/p2/2026-10-22-b300-dp2-functional-pass/run.json)
and [observations](../../evidence/p2/2026-10-22-b300-dp2-functional-pass/output.log)
record the TP4 x DP2 B300 lab at producer
`080a7fe1f594b27113a015a605d29c5d0d7f674e`.

- Window: 2026-10-22 06:15:32 through 06:41:51 UTC.
- Eight B300 devices observed, CC enabled, devtools off, NVLE multi-GPU mode.
- The complete 510,311,045,295-byte model pack passed its gate.
- Engine readiness: 1,211,364 ms after engine launch, 1,212 health probes.
- All eight fixed functional cases and ten requests passed, including both
  two-turn tool continuations. Request phase: 32,564 ms.
- No entry or worker exception diagnostic; container exit 0, no OOM,
  engine stopped and owned-container cleanup confirmed.

The only runtime-environment change from the preceding
[DP2 tokenizer failure](p2-t3-tokenizer-threads.md) was
`TOKENIZERS_PARALLELISM=false`. PID, CPU, memory and network limits were not
relaxed. This successful retry supports the mitigation, but does not recover
the missing original Rust panic or independently prove the precise failure
cause.

These are functional checks, not a concurrency/throughput benchmark or a tau
score. Per-replica request distribution was not measured. The VM's AMD CPU
does not meet the plan's Intel TDX requirement. Native/JIT provenance and the
complete confidential-service qualification remain unproven. The original
envelope therefore remains `result=fail`, `status=BLOCKED`,
`deployable=false`, `confidential=false`, with `functional_pass=true` inside
the experiment. No success flag has been rewritten.

The rented VM is retained under the separate 24/7 authorization; only the
bounded experiment was cleaned up. No public inference listener was created.
Byte hashes and all eight-GPU qualification limits are regression-tested by
`scripts/test_functional_pass_evidence.py`.
