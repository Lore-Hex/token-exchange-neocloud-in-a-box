# Selected-checkpoint long graph trial: passed, ordinary eight-B300

The bounded C1 trial completed on October 26, 2026, using the selected
`dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8` checkpoint at
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, 79 files / 510,311,056,155 bytes.
This is **nonconfidential hardware**. It is not quality, sustained throughput,
general cache isolation, pricing or deployment qualification.

## Producer and immutable artifacts

Producer commit: `3d496e0cc6964a42dc3c6e3f321f4f7a66499d96`.
The original bytes are retained, not reconstructed from this summary:

| Artifact | Meaning |
|---|---|
| [preparation.json](preparation.json) | Source closure and 489-file prepared inventory |
| [input-contract.json](input-contract.json) | Exact pinned model and engine configuration |
| [build.json](build.json) | Build-only receipt; runtime claims remain false |
| [owner.json](owner.json) | Original host owner stdout, successful qualification and cleanup |
| [progress.jsonl](progress.jsonl) | Original phase timestamps and execution/lifetime bindings |

The committed [lab owner](../../../host/record-deepseek-lab.py) prepares the lab
payload; [selected_service_image.py](../../../scripts/selected_service_image.py)
with `prepare` prepares the serving image, and `build` builds it;
[the serving owner](../../../host/record-selected-service.py)
runs it with `--execution-profile long128k-graphs --lifetime-profile ordinary-24h
--long-qualification`. Private paths, network identifiers, credentials and TLS
material remain outside the repository. The reusable run path and prerequisites
are documented in [the qualification guide](../../../docs/tasks/p2-t3-long-qualification.md).

The prepared identity is
`f64b7cb3763d704c18f3085ec419540b4164b4b144589288fc7c057a627a2ae9`;
the image is
`sha256:3346a8d5d741d4bfef1a1f363a9befc78f089d819ad7a26ea48efe16282a70c8`.
The graph profile retains TP4 x DP2, FP8 KV, GPU-resident Engram, chunked prefill,
one admitted request and unchanged deadlines. It disables eager mode, selects
decode-only graph shape `[1]`, and does not enable speculative decoding.
The [source-backed settings](../../../docs/tasks/p2-t3-long-context-cache.md)
explain the compiler defaults that also change when eager mode is removed.

## Observed results

Readiness was reached at 03:47:01 UTC; drain began at 03:49:36 UTC. All ten
requests completed with final integer usage and no retries. Qualification took
155.08 seconds, using 800,512 input tokens and producing 16,420 output tokens.

| Prompt tokens | Warm cached tokens | Cold cached tokens | Different-namespace cached tokens |
|---|---|---|---|
| 32,512 | 32,384 | 0 | 0 |
| 65,280 | 65,152 | 0 | 0 |
| 130,816 | 130,688 | 0 | 0 |

All nine retrieval checks passed and returned four output tokens each. Cache
reuse is replica-local; request-to-replica identity was not observed. These
controls are evidence for these requests, not proof of all cache-isolation
properties. Tiny four-token streams are not a meaningful decode benchmark.

The final request used **114,688 input + 16,384 actual output tokens**, filling
the 131,072-token combined context. It finished with `length` in **114.83 seconds**,
with **2.46-second TTFT**, zero cached tokens and 16,381 output events. Events
are not tokens. The recorded decode-rate **estimate** is **145.85 tokens/second**,
the inverse of the measured mean output-token interval; whole-request output
rate is **142.68 tokens/second** (`16384 / 114.830993033`). This one serial case
is not a node-wide sustained throughput or cost-competitiveness measurement.

The [earlier eager run](../2026-10-26-selected-long-qualification/README.md)
timed out at 900 seconds on the same bounded workload, with final usage unknown.
Its failure is preserved. A precise speedup ratio cannot be computed, and this
test does not isolate CUDA graph replay from the other compiler-default changes.

All TLS/guard/engine drains and reaps passed. The host confirms container/network
removal, private-input cleanup and watchdog cleanup. The build-only and broad
deployment/quality/privacy flags remain false in the original artifacts.

## Replay

`python3.12 -m unittest discover -s scripts -p test_selected_long_graph_evidence.py`
checks exact artifact hashes, historical source/preparation/image bindings,
actual tokens versus events, cache controls, timestamps and confirmed cleanup.
It replays the original historical validators and rejects mutated usage and
inflated capacity/isolation claims. This replay is offline; it starts no engine.
