# P2.T4: Finite sustained C8 baseline

Status: **implemented, software tests only**. No sustained hardware result,
public-service change, saturation claim or deployment approval. The existing
[C8 evidence](../../evidence/p2/2026-10-24-selected-c8-stream/README.md) is one short burst, not this run.

## Closed experiment

The explicit `chat-2k-512-c8-32waves-600s` stream profile runs on the existing
ordinary selected-checkpoint B300 lab, `batch8-graphs`, TP4 x DP2. It does not
change the engine arguments, model/source pins, 4,096 context, 512 GiB memory,
no-swap contract, graph sizes or confidential boundary. C1 and the previous
single-C8-burst profiles retain their default contracts and receipt schemas.

The pinned [vLLM scheduler](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py)
documents `max_num_seqs` as per data-parallel rank. `batch8-graphs` permits eight
per rank, not eight across both ranks. This load offers eight across the
endpoint; it neither proves rank occupancy nor saturates both replicas.
The current public C1 service is not demonstrated saturated.

Fixed bounds:

- One separately accounted warmup, then exactly 32 waves of eight concurrent
  requests: at most 257 total requests, no retries or automatic engine reload.
- Existing exact-tokenizer synthetic 2,048-token prompts and 512-token output
  ceilings. Reservation: 526,336 prompt plus 131,584 output tokens, 657,920 total.
  Actual generated tokens, including failed requests with known usage, are
  retained; a requested ceiling is not a measurement.
- One original 600-second stage cutoff, clipped to the owner's existing
  deadline. Each request retains the transport's 60-second bound; each wave
  reserves the existing 10-second setup and three-second collector cleanup.
  No wave starts without the full setup/request/cleanup reservation remaining.
- All 32 waves must complete, each with eight observed overlapping requests and
  confirmed collector cleanup. The measured window must last at least 120
  seconds. A faster finite run records `insufficient-duration`, not a padded
  duration, retry, or claimed sustained pass.

Requests use the existing source-verified lab tokenizer and the owner's fixed
loopback endpoint. The owner supplies PID/start/namespace/listener identity;
every collector performs its peer check in its main thread. The engine identity
is checked between waves and again at the existing terminal boundary. Exec
collectors retain `close_fds=True`, no inherited owner/control descriptors,
bounded private metadata packets, cancellation and process-group reaping.

## Reuse and integration

[`stream_c8.py`](../../scripts/stream_c8.py) supplies the same measured-wave
executor for the old burst and new sequence. The old burst still performs its
own warmup and emits v2. A wave has a distinct schema and no fabricated warmup.
[`stream_sustained.py`](../../scripts/stream_sustained.py) owns only the fixed
sequence, accounting and strict aggregate validator. It publishes each wave
into the parent result before restoring cancellation handlers so a late signal
cannot erase already settled usage.

[`stream_lab.py`](../../scripts/stream_lab.py) admits the explicit profile and
loads its exact source closure; existing host preparation, wrapper dispatch,
engine ownership and cleanup remain the launch path. The host's larger report
bound applies only to this profile: 512 KiB capture / 500,000-byte validated
terminal report for 256 sample/interval pairs. Old profiles retain
65,536 / 60,000 bytes. No generic endpoint, schedule, profile or token-limit
override is introduced.

Prepare from a clean reviewed source using the existing pinned inputs:

```sh
python3 -I -S -B host/record-deepseek-lab.py \
  --prepare-nonconfidential-uncensored-b300-dp2 "$NEW_PRIVATE_BUNDLE" \
  --model-profile uncensored --execution-profile batch8-graphs \
  --tokenizer-parity --tokenizer-materialization --runtime-observation \
  --stream-cohort --stream-profile chat-2k-512-c8-32waves-600s
```

This is preparation only. A separate approved run still requires the exact
receipt, offline image/weights, source identity, original hard cutoff and
cleanup gates in the [ordinary lab procedure](p2-t3-nonconfidential-baseline.md).
It is not a request against the active public owner.

## Measurement and failures

The existing terminal result contains the warmup, every wave, per-request
usage/finish/timing and collector intervals. Nothing logs prompt/completion
text, headers, endpoints or arbitrary exceptions. Failures stop new waves but
retain completed and failed usage. A missing collector response stays unknown.
A missing entire wave result conservatively reserves all eight uncertain
dispatches, reports unknown usage and unconfirmed cleanup, and does not label
them not-run or free. No success is reconstructed from engine absence alone.

The aggregate denominator starts before the first measured wave's setup and
ends after the final wave's collector cleanup and inter-wave identity checks.
It includes setup, dispatch, prefill, response reads, reaping and gaps. It is
not pure decode time and is not the sum of just the successful wave durations.
Warmup is excluded from measured throughput/latency but included in accounting
and stage elapsed time. Report actual aggregate output tok/s, per-GPU rate,
TTFT/first-content/estimated inter-token p50/p95, failures and unknown usage.
Repeated waves do not mean eight requests remain continuously in flight.

The same eight measured synthetic fixtures repeat across waves. Prefix caches
are not flushed or declared cold. Actual `cached_prompt_tokens` values remain
per request, including null when unavailable; no zero is invented. Cache-state
control, cross-rank routing, cache isolation, quality, confidentiality, pricing,
capacity sweeps and saturation remain explicitly unqualified.

An honest output-cost estimate needs the full eight-GPU deployment cost,
aggregate measured output throughput and a disclosed busy fraction. For a
busy-window rate `R`, hourly cost `H` and utilization `u`, output-only cost is
`H * 1e6 / (3600 * R * u)`. Do not apply utilization again to an already
wall-amortized rate. Include startup/idle/storage costs separately and do not
substitute a single-stream rate or assume an input/output revenue allocation.

## Software checks

[`test_stream_sustained.py`](../../scripts/test_stream_sustained.py) covers the
closed schedule, full-window denominator, short/expired budgets, warmup and
partial-wave failures, settled versus unknown usage, identity drift, cleanup
denial, strict report tampering, private child pipes, source-bound preparation
and byte-identical existing engine/transport/workload sources. Existing C8,
cohort and lab tests remain required. Full `make check`, `make test-e2e`, hosted
gates and independent final-head review are required before any hardware run.
