# P2.T3: private 128K and batch32 qualification owner

**Implemented and tested against fakes; not hardware-qualified.** The
[private CLI](../../host/record-selected-combined.py) integrates the
[owner](../../scripts/selected_combined_owner.py), existing long/scaling helpers,
and a separate bounded original-byte receipt channel. It runs one engine
lifetime, long retrieval/cache/16K-output checks first, short C16/C32/C64 scaling
only after those checks pass, then retires the entire owned container.
The [pure planner](../../scripts/selected_combined_plan.py) still performs no I/O
or admission itself. `long128k-batch32-graphs` remains rejected by public and
legacy execution-profile registration; only the closed private CLI selects it.
Existing evidence is unchanged and cannot be relabelled as a combined run.

The existing image preparer inventories the complete Python source tree. A
future preparation at this new source revision binds the added files and closed
entrypoints with new hashes; it cannot preserve the old image's identity.
The private host verifies the complete committed Python source closure against
the prepared image before any container creation. No selected-engine image was
prepared or built here.

This is a software integration of the combined qualification path.
It is not evidence that the combined engine fits in memory, captures graphs,
passes long-context checks or supports higher public admission. No hardware,
deployment, selected-engine image build, live engine endpoint or confidential
appraisal was exercised. The GPU-free fake-node test is software validation only.

## Candidate controls

The preview reuses the closed transforms in
[deepseek_tuning.py](../../scripts/deepseek_tuning.py), with its existing source
hashes at vLLM
[`5df63a7203cdb9328d824a27b682e77c7b9928be`](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be).
It starts from `long128k-graphs` and takes only `--max-num-seqs` and the
compilation configuration from `batch32-graphs`. No new engine flag, version,
model, speculative decoding or override is added. The input must be canonical
lab argv; this preview is not an authenticator for arbitrary argv.

The proposed private engine has 32 sequences per DP rank and decode graph sizes
`[1,2,4,8,16,32]`. Context remains 131072, the batch-token cap remains 4096,
and chunked prefill, prefix caching, real prompt-token details, FP8 KV,
GPU-resident Engram and TP4/DP2 remain as in the existing profiles. These are
configured limits, not observations. The public boundary must remain **C1,
131072 total context tokens, 16384 maximum output tokens**. Engine sequence
capacity must not become the public admission limit.

The later throughput stage is only the existing isolated, fresh-salt
**2048-input / maximum-512-output** C16/C32/C64 workload. C64 means concurrent
short-workload clients, not 64 long requests, even rank placement or public C64.
See the [long profile](p2-t3-long-context-cache.md) and
[batch32 experiment](p2-t3-batch32-throughput.md) for pinned source references
and observation limits. Neither existing result qualifies the combined profile.

## Exact budget

The 8440-second floor is **140 minutes 40 seconds**, excluding additional
preparation. It is a configured worst-case allocation, not a runtime estimate.

| Allocation | Seconds | Existing source |
|---|---:|---|
| Engine/guard/TLS startup | 1800 | [engine_lab.STARTUP_SECONDS](../../scripts/engine_lab.py) |
| Long retrieval/cache/16K-output stage | 3600 | [selected_long_qualification.RUN_SECONDS](../../scripts/selected_long_qualification.py) |
| Isolated C16/C32/C64 short stage | 900 | [stream_scaling.SECONDS](../../scripts/stream_scaling.py) |
| Service cleanup | 1960 | [selected_service.cleanup_reserve](../../scripts/selected_service.py) |
| Host cleanup | 180 | [record-selected-service-smoke.HOST_RESERVE](../../host/record-selected-service-smoke.py) |
| Total before additional preparation | **8440** | 1800 + 3600 + 900 + 1960 + 180 |

The 1960 seconds are two frontend drains of `900 + 30 = 930` seconds plus
four 25-second cleanup steps: TLS, guard, engine and the unregistered-child
sweep. The host's 180 seconds are separate, outside the service cutoff.
The long stage already includes its 180-second tokenizer preparation within
3600; do not add that allowance twice. The short stage includes its own warmup,
waves, collection and reaping, but not its upstream tokenizer preparation.

`DeadlinePlan.begin` requires an explicit positive preparation allocation `P`
and at least `8440 + P` seconds remaining under **both** original clocks. No
default `P` is provided. The private owner additionally requires at least 145
seconds (60 host preflight + 60 short-tokenizer preparation + 25 child cleanup),
but that minimum is not a duration estimate or a guarantee that weight hashing
fits. Host preflight, complete source and weight checks, and short-tokenizer
preparation all consume the original `P` deadline. Operators must budget more
where needed; expiry fails closed and never starts the engine with unverified
weights. The long tokenizer's separate 180 seconds stay inside the long stage.

Each stage end is fixed relative to the captured start, with all later stage
budgets reserved. Clock observations can only shorten the original cutoff;
backwards wall-clock movement, early completion, delayed startup and repeated
observations never grant a fresh duration. Expired ceilings remain expired.
The pure helper reads no clock, schedules nothing and admits no stage. The owner
observes both clocks at stage boundaries and enforces their fixed deadlines;
the child receipt must match the host's original clock allocation exactly.

The legacy lab and smoke owners remain capped at **4800 seconds / 80 minutes**.
Their bounds are unchanged. The explicit combined-only owner uses the existing
ordinary-24h upper ceiling, but requires enough time before the originally
authorized cutoff. Its watchdog ends at `P + 8440`, even if a longer rental
window was authorized. It rejects an insufficient window and cannot renew the
cutoff or silently borrow from cleanup.

## Owner boundaries

1. Bind the closed profile to the existing selected ordinary B300 DP2/TP4
   source closure. Keep public C1 separate from private engine sequence
   capacity; no arbitrary profile knobs or selected-model substitution.
2. Allocate and enforce preparation, then one engine lifetime and fixed stage
   order: long retrieval/cache/16K-output checks first, isolated short scaling
   second. Revalidate original source and process identity before/after each
   stage. Failed long `checks_pass`, cancellation, identity drift, missing
   usage or failed cleanup must stop the path before throughput. No retries.
3. Integrate authenticated private collectors with their existing bounded
   pipe/reap protocol. The selected service uses a fully qualified model name;
   [stream_transport.py](../../scripts/stream_transport.py) now accepts that
   exact name only through an explicit opt-in, with legacy limits unchanged.
   The closed owner binds that choice to its authenticated source/profile;
   there is no general name/endpoint override or request rewriting.
4. Review [stream_capacity.py](../../scripts/stream_capacity.py) against the
   pinned source: its existing concurrency-32 / `32 * 4096` gate belongs to
   the 4K profile. Do not relabel that validator for 128K or infer long-request
   capacity from the short workload. The policy and owned collector below are
   integrated into the private owner. Preserve the old
   gate and receipts.
5. Add a **separate bounded combined receipt path**, not a global cap increase.
   The selected lifecycle capture is currently 16 KiB; the existing scaling
   report can be 30 MiB with a 32 MiB owner capture. Define closed component
   bounds, original-byte hashes, same-owner/source/engine joins, terminal
   cleanup and a replay validator; retain partial known usage and unknowns,
   never truncate samples. Old lifecycle/progress/report schemas stay intact.
6. Test maximum-size receipts, failed-long short-circuiting, cancellation at
   every boundary, source/process drift, missing usage, child/FD cleanup and
   worst-case reserves. Obtain independent exact-head CLEAN and full CI green
   before merge; any hardware run remains separately authorized.

## Private entrypoint and receipts

Future authorized execution uses a new source-bound selected-service image
prepared from the existing `long128k-graphs` lab contract. There is no arbitrary
engine-argument, model, endpoint or concurrency override. The closed launcher
revalidates that contract, applies only the existing batch32 sequence/graph
transform, and retains the long C1 frontend contract. The nested lifecycle
receipt describes those frontends; distinct combined long/scaling schemas
describe the private engine stages. Legacy validators reject those new schemas.

The isolated `python3 -I -S -B host/record-selected-combined.py` CLI accepts
`--inspect` or `--run`, the existing smoke owner's build/preparation/private-input
arguments, an explicit `--preparation-seconds`, and an empty, owner-only 0700
`--receipt-directory`. Run uses Docker `--network none`, no published ports,
the existing read-only inputs and process-identity checks, and the existing
ownership/watchdog cleanup. Inspect validates inputs but starts no engine;
it is not qualification. No run is authorized by this document.

The exclusive 0600 `intent.json`, `capture.jsonl` and `host.json` files are
private originals. Long (12 KiB), scaling (30 MiB), lifecycle (16 KiB) and four
raw capacity observations (2 MiB each) have closed independent bounds. The
base64 envelope has a 52 MiB limit; attach adds at most 64 KiB of progress. No
legacy 16 KiB bound changes. Each component retains exact bytes, size and hash;
replay recomputes stage claims, usage, source/engine joins and metric parsing.
The full host replay also requires the original clock allocation, successful
runtime exit, watchdog/container cleanup and private-input cleanup.

```sh
python3 -I -S -B host/record-selected-combined.py --replay \
  --receipt-directory "$PRIVATE_RECEIPTS" --prepared-sha256 "$PREPARED_SHA256"
```

Replay performs no inference or daemon operations. Partial captures, failed
publication, cancellation and unknown dispatches are retained, never rewritten
as zero usage. A missing/invalid final receipt cannot release a reservation;
neither this owner nor its replay changes billing ledgers or grants live budget.
Private receipts must not be committed wholesale: the raw engine observations
and workload/accounting are for the authorized operator's evidence archive.
All hardware, public-admission and confidentiality claims remain false here.

## Verification scope

### Combined Capacity Policy

[selected_combined_capacity.py](../../scripts/selected_combined_capacity.py)
provides a parser and bounded owned-loopback collector for the reserved profile.
It reuses the existing Prometheus decoder, metrics transport and rank-stability
checks without changing old short-only callers, request bytes or receipts.
Its separate versioned observation
cannot be substituted for the old rank list. Only the closed private owner uses
it for this sequential experiment; it does not grant public profile admission.

At pinned vLLM `5df63a7203cdb9328d824a27b682e77c7b9928be`,
[CacheConfig](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/cache.py)
defines concurrency **per DP engine at max_model_len tokens**. Its `metrics_info`
uses the full `str()` value, not the rounded human log. The pinned
[get_kv_cache_capacity](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/core/kv_cache_utils.py)
returns `int(concurrency * max_model_len)` as the group-aware token capacity.
Both source files are SHA-256 pinned in the helper, in addition to the four
existing metrics/engine source pins. These were read on October 27, 2026;
they are upstream semantics, not our hardware measurements.

Consequently, requiring `kv_cache_max_concurrency >= 32` at 128K would wrongly
require capacity for 32 simultaneous **long** requests per rank. This proposed
experiment runs long C1 and short batches sequentially. Its capacity precondition
is at least one 131072-token request and at least `32 * 4096 = 131072` tokens
per rank. The short allowance is conservative relative to its 2048/512 workload.
The new parser requires concurrency >= 1, token capacity equal to the pinned
formula, both exact ranks, idle scheduler counters and unchanged capacity /
preemption counters between snapshots. FP8 KV, no KV offload and prefix caching
remain mandatory. The old short-only parser still requires concurrency >= 32.

The observation explicitly retains public C1 and false runtime/confidentiality
claims. It does not prove HBM availability, graph capture, even rank placement,
that 64 clients will run simultaneously, mixed-workload safety, or a supported
1M/high-concurrency profile. A source/owner/engine join, retained original-byte
capture and whole-run cleanup are enforced by the owner. Actual hardware
qualification still requires a complete, reviewed run.

### Selected-Model Collector

The collector returns the original bounded metrics bytes and the separate parsed
observation, with one GET, no retry, a five-second whole-operation deadline,
2 MiB response ceiling, and the existing HTTP framing and process-identity checks.
It uses only the owned loopback listener on port 8000; it accepts no arbitrary
host, port, credential or model. Parsing stays inside the same deadline.

The shared short-stream transport adds an explicit, default-off selected-model
option for exactly the selected service's full wire ID. Without that option the
original model validation and bytes remain unchanged. Enabling it changes only
that closed model binding: 4K context, 512 output tokens, 60-second deadline,
response-model validation, peer checks and cleanup remain mandatory. Tests
normalize only literal reviewed additions before comparing the legacy source;
unreviewed changes still fail the historical regression check.

The private owner authenticates the source and execution profile, retains
original observations, and joins them to lifecycle/cleanup evidence. It does
not connect the collector to a public route or increase public concurrency.

Synthetic regression checks:

```sh
PYTHONPATH=scripts uv run --project tools --frozen python -m unittest test_selected_combined_owner test_selected_combined_host test_selected_combined_source test_selected_stream_collector test_stream_transport test_selected_combined_capacity test_stream_capacity test_stream_scaling test_selected_combined_plan test_selected_long_hook
```

The tests cover the old 4K denominator being rejected under the new profile,
exact boundary/fractional capacities, both-rank drift, malformed types, nonfinite
values, source tampering, no-I/O behavior and continued launch rejection. They do
not start an engine, collect live engine metrics or change the serving
configuration. Synthetic loopback tests additionally cover length/chunked
responses, unchanged request ceilings, exact model identity, cancellation,
restored alarms, no retries, malformed deadlines and terminal process drift.

[Offline tests](../../scripts/test_selected_combined_plan.py) compare the
composition to both existing closed transforms; check budget constants against
the actual helpers, exact boundaries and clock movement; assert launch rejection;
and replay legacy argv, request bytes and receipt validators. These tests do not
exercise live combined capacity collection. The owner tests additionally drive
the actual long and scaling control flow through one fake lifetime; verify
source/engine forgery rejection, cancellation and partial unknown accounting,
maximum original receipts, failed payload publication, host/watchdog cleanup,
and private replay. Boundary tests use the real all-role checkpoint around fake
OS process observations, reject engine/guard/TLS identity drift after either
stage, retain raw metrics after parse/identity/source failure, and preserve
cancellation during attach or cleanup. Startup and per-role cleanup failures
must still attempt the remaining owned cleanup; a successful child payload
cannot override host capture/publication failure or forged cleanup receipts.
These remain software tests, not tau2 quality or GPU proof.
