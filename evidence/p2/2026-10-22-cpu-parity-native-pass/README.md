# Native Two-Model CPU Tokenizer Parity

**Functional parity PASS, not live admission or quality.** Original artifact
from [workflow 39857959628 attempt 1](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39857959628),
producer `0a89d7f5193fab48f1b1e3cfa8e88fd772436f4f`. The recorder ran from
**2026-10-22 21:33:14 to 21:38:39 UTC** on native GitHub-hosted Linux/amd64.
Both files are unchanged downloaded bytes, not reconstructed summaries.

| File | SHA-256 |
| --- | --- |
| `run.json` | `25b871a3653381c2ccb2195ff82ba6c329e7242723b2f7956beae8de6033f94e` |
| `output.log` | `392863195c43d19c5f871b76ba47363e6442cb5b0ac2ea32c3c3593014be137f` |

The original report binds 21 source files at that producer, the existing pinned
llama.cpp revision `620d2225cc436ab0b001e3200e590e3b286ef9ab`, both distinct GGUF
hashes, the built helper/server hashes and observed process/socket identities.
Later source-closure or live-consumer additions are not substituted into it.

| Model | Text | System | Tools | Tool Continuation | Ready |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3-0.6B Q8_0 | 20 | 28 | 160 | 198 | 812 ms |
| Qwen3-1.7B Q8_0 | 20 | 28 | 160 | 198 | 1610 ms |

All four unchanged synthetic probes passed for each model. Each table count
matches independently invoked vocabulary/template helper output, the server's
token-ID hash/count, and generated `usage.prompt_tokens`; each generation used
one completion token. This is independent invocation of the same pinned common
library, not an independent tokenizer implementation or arbitrary-history proof.
The strict argv/executable/PID/start-time/network-namespace/listener/peer checks
were not relaxed. Both records have `owner_socket_checked: true`.

The run used the reviewed four-CPU, 8-GiB/no-swap, 128-PID, network-none, no-GPU
container with existing deadlines and confinement checks. Owned image/container
absence and image-build completion are confirmed. Servers ran **sequentially**
and were stopped. No peak-memory value or simultaneous two-model fit was measured;
readiness times are not an application-latency or throughput benchmark. Downloaded
inputs and base/BuildKit caches are not claimed deleted.

`live_admission`, `quality_eligible` and `confidential` remain false. This is not
a tau trial, simulator/evaluator quality validation, fresh live-pair receipt,
boot, confidential-hardware, release or reproducibility proof. The earlier
[OrbStack argv failure](../2026-10-22-cpu-parity-argv-failure/README.md) and
[native base-inspection failure](../2026-10-22-cpu-parity-base-inspect-failure/README.md)
remain unchanged; this pass does not establish the cause of the OrbStack mismatch.

The [producer contract](../../../docs/tasks/p2-t3-tau-cpu-parity.md) and
[native route](../../../docs/tasks/p2-t3-tau-cpu-native.md) retain the operational
boundaries. Evidence tests verify original bytes and all historical source pins,
replay the strict runtime validator and reject altered success/ownership/usage
claims. Scanner exceptions cover only the exact public source hash and four
fixed synthetic token-ID hashes in this exact original output path.
