# Opt-in selected service decode graphs

Software implementation, not a new service measurement or deployment. The
default remains `tau8k-eager`: 8,192 context tokens, 256 output tokens, one total
admission. The explicit `--execution-profile batch8-graphs` selects **4,096
context / 256 output / eight total admissions**, independently of the existing
`smoke` or `ordinary-24h` lifetime. Unknown profiles and cross-profile artifacts
fail closed. There is no numeric capacity, engine-argument or upstream override.

## Reused engine and evidence

The [existing tuning helper](../../scripts/deepseek_tuning.py) remains the lab
argument authority: selected abliterated checkpoint, ordinary B300, TP4/DP2,
4,096 batched tokens, eight sequences per DP rank, no speculative decoding,
`FULL_DECODE_ONLY`, capture sizes `[1,2,4,8]`, and disabled fused allreduce/RMS.
This reuses its hash-checked vLLM
[`5df63a7` compilation configuration](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/compilation.py),
[scheduler configuration](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py),
and [graph dispatch](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/worker/gpu/cudagraph_utils.py),
not new flags or an engine upgrade. The service admits eight requests **total**,
not sixteen across ranks. GPU memory, 512 GiB host memory, zero swap, immutable
weights, canonical model ID and native request validation remain unchanged.

The [original C8 lab burst](../../evidence/p2/2026-10-24-selected-c8-stream/README.md)
completed eight simultaneous requests at 645.28 output tok/s overall, median
TTFT 1,835.91 ms. Each returned 400 tokens under the lab's 512-token cap; the
service does **not** inherit that cap. The earlier
[C1 graph cohort](../../evidence/p2/2026-10-24-selected-graphs-stream/README.md)
measured 143.36 tok/s and 98.61 ms median TTFT. These small, differently concurrent
lab cohorts do not establish sustained TLS-service capacity, a latency promise,
tau quality or the performance of this 256-token service profile. Original
artifacts and their FAIL/BLOCKED/qualification fields are unchanged.

## Exact preparation boundary

Create a fresh non-owner lab bundle from the final reviewed source using the
existing B300 DP2 preparer with `batch8-graphs`, tokenizer parity,
materialization, observation, and the exact `chat-2k-512-c8-600s` stream cohort.
All generic lab/source/receipt validation remains mandatory. The service adapter
additionally rejects missing metadata, another cohort, tau-owner/provider roles,
an eager flag, speculative decoding or different graph/context/sequence bounds.
It retains the authenticated cohort metadata in the derived payload; it does
not remove fields to make an eager contract pass. Its engine-only entry never
runs the lab cohort or parity requests. At launch, only the served-name value
changes from the lab bare name to the existing canonical catalog ID.

Use the [existing preparation/image builder](p2-t4-selected-service-image.md)
to create new immutable source, payload, prepared receipt and image. No builder,
base image, interpreter, dependency, weight or Dockerfile policy change is
required. The sealed inventory and build receipt bind the newly compiled guard
and Python sources. Graph host admission requires the embedded lifecycle,
process owner and engine adapter bytes to match its reviewed source before
image inspection or private input staging. It also requires the sealed lab
execution profile to match the explicit host selection. An older eager image
cannot be relabeled. Existing eager/day artifacts must not be overwritten.

Both the network-none smoke host and finite network owner pass the closed
execution selection to the container. The existing private runtime journal
and watchdog retain/check the exact container command, image, mounts, network,
labels and original cutoff; no new network controller or resource is added.
The process cross-checks the sealed lab selection before private input snapshots
and before any child start. Engine readiness requires the owned canonical model
and observed integer context 4,096. The guard's closed graph profile supplies
the same catalog context and eight-slot admission; trcs receives max-inflight
eight. The existing guard holds each slot through response EOF, rejects a ninth
with 429/Retry-After, and never queues or rewrites streams. The pinned trcs
[`/healthz` handler](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/8a87d9765ceabe76bbfa5e56a72c91da6d8290d5/internal/shim/server.go#L122-L143)
precedes its limiter; guard health also bypasses generation slots.

## Receipts, lifetimes and gates

Only graph execution uses lifecycle `v7`: the original lifecycle/mode result is
kept flat, with its original schema in `lifecycle_schema`, the closed execution
limits, and `execution_prepared_sha256`. Host validation requires exact profile
and prepared-source equality, then invokes the unchanged strict legacy/mode/day
validator. It cannot promote an old eager result, failed cleanup, unknown usage
or failed checker status. Day progress uses a bound `v2` record for graphs;
its order, content-free reconstruction and total 16 KiB output budget are
unchanged. Eager command, lifecycle, progress and journal formats are unchanged.

The [lifetime contract](p2-t4-selected-service-day-profile.md) still caps smoke
at 4,800 seconds and explicit day at 86,400. Startup remains 1,800 seconds within
the original absolute deadline; forward clock jumps shorten it and rollback
cannot extend it. Front-first drain, per-request timeout, independent watchdog,
quarantine, root/private-input cleanup and no restart/renewal remain intact.
All deployment, hardware, tools/reasoning qualification, quality and privacy
claims remain false. Billing remains TrustedRouter's responsibility.

[Python regressions](../../scripts/test_selected_service_graphs.py) cover the
original C8 contract, exact argument copy, metadata/source/profile mutations,
both lifetimes, clock/cancel/cleanup failures, launch/admission joins and strict
fragmented receipts. [Go regressions](../../internal/pilotapi/graphs_test.go)
cover eight held streams, ninth-request rejection before the engine, health
under load, cancellation slot release, retirement with active response/usage
bytes preserved, and unchanged Qwen/eager behavior. Existing stream collectors,
protocol parsers and their byte-golden tests are unchanged.

Before any hardware use: final independent review and exact-head CI; fresh
validated preparation/build; proven prior-owner cleanup; explicit operator run
approval. A future bounded run should join actual catalog/auth/protocol and
overlapping streams, observed usage/TTFT/errors, active drain, and host cleanup
under this exact service profile. No hardware run, benchmark promotion, public
activation, automatic renewal or graph change to the eager day artifact is
authorized or claimed here.
