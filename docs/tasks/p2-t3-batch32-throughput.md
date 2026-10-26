# P2.T3: bounded fresh-salt C16/C32/C64 experiment

Software-only opt-in, not a measured throughput or capacity result. This uses
the existing selected ordinary B300 DP2/TP4 lab owner, private loopback engine,
tokenizer parity, materialization and runtime observation. It does not change
the public service, selected weights, older execution profiles or receipts.
The [original sustained C8 failure](../../evidence/p2/2026-10-26-selected-sustained-c8/README.md) is not promoted:
its measured 114.279 seconds still fails its original 120-second minimum.

## Closed configuration

Prepare with the existing `host/record-deepseek-lab.py` CLI:

```sh
python3 host/record-deepseek-lab.py \
  --prepare-nonconfidential-uncensored-b300-dp2 "$NEW_BUNDLE" \
  --model-profile uncensored --execution-profile batch32-graphs \
  --tokenizer-parity --tokenizer-materialization --runtime-observation \
  --stream-cohort --stream-profile chat-2k-512-c16-32-64-isolated-900s
```

The existing pinned image metadata and lab-preparation prerequisites still apply.
Run only the committed recorder against the exact validated bundle after all
software gates and independent review. There is no arbitrary URL, run-time profile
override, retry, host network, remote orchestration or operator credential in this
increment. A run needs separate authorization and enough remaining original
lifetime for startup, the finite stage, owner cleanup and public-service restore.
The owner and host retain their original hard cutoff and 80-minute maximum; this
stage clips to that deadline and never extends it. Software may merge without
running in a particular hardware window.

`batch32-graphs` retains context 4096, batch-token cap 4096, FP8 KV, GPU-resident
Engram, TP4/DP2 and existing all-reduce controls. Only max sequences becomes 32
per DP rank, decode graph sizes become `[1,2,4,8,16,32]`, and prompt-token details
are requested. No DSpark or CPU offload. Removing eager also restores the pinned
compiler/JIT defaults, not only graph replay. Existing profiles' argv, source pins
and default request bytes have regression checks against the prior producer.

## Finite measurements

One warmup is separate. Each C16, C32, C64 plateau repeatedly dispatches bounded
waves while its 150-second admission window is open, then drains the final wave.
There is no padding/sleep to satisfy duration. Wave setup, admission, HTTP,
cleanup, gaps and terminal metrics sampling remain in the measured denominator.
This is not continuous admission and does not prove saturation.

| Bound | Value |
|---|---:|
| Whole stage, clipped to original deadline | 900 s |
| Active admission per plateau | 150 s |
| Minimum measured duration to qualify | 120 s |
| Maximum requests per plateau | 4096 |
| Total including warmup | 12289 |
| Exact prompt / maximum output per request | 2048 / 512 tokens |
| Maximum reserved total tokens | 31459840 |
| Request deadline / wave setup / wave cleanup | 60 / 10 / 3 s |
| Each owned metrics probe | 5 s, 2 MiB |

Each request gets a unique random-run-domain-separated salt, including warmup.
Actual cached-token usage must be present and zero for **every** request for a
PASS. Missing usage stays unknown; positive cache usage fails this experiment.
Repeated/warm cache conditions are not implemented or qualified. No inferred
apples-to-apples speedup against old C8 reports with unknown cache conditions.
The nine source-bound synthetic fixtures, tokenizer template, actual usage and
content-free sample format are reused without retaining prompt or output text.

The result retains every admitted response, known failed usage, missing-response
unknowns and not-run reservations. An interruption that loses a whole wave marks
its possible dispatches unknown, never free or confirmed reaped. Cancellation,
malformed/oversized pipe output, source/identity drift or failed cleanup stops
later plateaus. No retry. Only the new closed profile permits a 30 MiB validated
report and 32 MiB capture: 12289 bounded sample/interval pairs at less than 2 KiB
each plus less than 2 MiB of closed wave/summary/owner metadata. Maximum-count
serialization is tested; no samples are truncated. All older caps are unchanged.

## Source and observation limits

All references below are vLLM
[`5df63a7203cdb9328d824a27b682e77c7b9928be`](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be),
with exact local source hashes enforced before collection:

- [SchedulerConfig, lines 62-103](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py#L62):
  `max_num_seqs` is per rank; C64 is a client admission target, not proof of even
  DP distribution or 64 simultaneously scheduled requests.
- [CUDA graph candidate generation](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/worker/gpu/cudagraph_utils.py#L252)
  and [GPU memory profiling](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/worker/gpu_worker.py#L695):
  graph memory is profiled before KV sizing. This harness does not directly
  observe graph replay or its allocated bytes; both remain explicitly unknown.
- [Metrics, lines 1114-1137](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/metrics/loggers.py#L1114)
  and [front-end cache initialization](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/engine/core_client.py#L850):
  rank-labelled cache info uses a shared front-end config. It is **advertised
  capacity**, not independently measured per-rank HBM. Both labels must exist,
  advertise FP8/APC/no KV offload, at least 131072 KV tokens and concurrency 32;
  advertised capacity must remain stable and reported preemptions must not increase. Running,
  waiting and KV usage are snapshots, not a utilization time series. Request
  rank remains unknown and the harness never sends a rank-routing header.
- [First-block salt hashing](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/core/kv_cache_utils.py#L647):
  salts isolate the prefix hash chain. Actual reported cached tokens, not this
  source property alone, decide the fresh-salt cache gate.

## Honest economics

Record node hourly cost, aggregate completed output tokens over the **full**
plateau wall time, actual input/cache counts, failed known usage and unknowns.
`cost_per_million_output = hourly_cost * 1000000 / (3600 * aggregate_output_tps)`
is an allocated node-cost estimate, not provider pricing. For a serving estimate,
include utilization: replace throughput with `aggregate_output_tps * utilization`.
Use actual input/output mix and separately priced cached input for revenue;
account for startup/idle time, other charges and failed work. A single-stream
rate, brief peak, unmeasured utilization or absent billing data cannot establish
cost competitiveness. Quality and confidential-computing acceptance stay false.
