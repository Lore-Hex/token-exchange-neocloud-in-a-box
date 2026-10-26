# Selected Checkpoint: Fresh-Salt C16/C32/C64

Measured 2026-10-26 05:06:49-05:34:31 UTC by committed producer
`ac6f33384fd91d90c1ac17db1e925bbc8d9b5d35` on eight ordinary B300 GPUs,
with confidential computing disabled. This is the selected abliterated
checkpoint, `batch32-graphs`, TP4 x DP2, FP8 KV, GPU-resident Engram,
4096 context/batched tokens and 32 maximum sequences **per DP rank**.
The fixed source-bound [experiment contract](../../../docs/tasks/p2-t3-batch32-throughput.md)
uses fresh cache namespaces, bounded waves and one separate warmup.

**The finite functional experiment passed all three plateaus and cleanup.**
The original outer `run.json` still says `result: fail` and
`hardware_validated: false`; the original report remains `status: BLOCKED`,
`result: fail`, `deployable: false`, `confidential: false`. These broader gates
are not promoted by the nested functional PASS. The private launcher exited 1.

| Offered concurrency | Completed requests | Actual output tokens | Full measured seconds | Aggregate output tokens/s | TTFT p50 / p95 (s) |
|---|---:|---:|---:|---:|---:|
| 16 | 608 | 243,200 | 153.500012482 | 1,584.365 | 0.384 / 0.640 |
| 32 | 1,088 | 435,200 | 152.202063225 | 2,859.357 | 0.570 / 0.855 |
| 64 | 1,664 | 665,600 | 150.176868583 | 4,432.107 | 0.986 / 1.637 |

All 3,360 measured requests completed with **2,048 actual prompt and 400 actual
output tokens each**, below the 512-output ceiling, finishing with `stop`.
There were zero failed started requests, missing/unknown usages, unknown
dispatches, unknown cached usages or reported cached prompt tokens. All
collectors were reaped. The 8,928 unused request reservations were not run;
they are not request errors or free inferred responses. No retry occurred.

Each plateau admitted waves during its original 150-second window and exceeded
the original 120-second qualification minimum without padding. Its denominator
includes setup, dispatch, prefill, collection, cleanup, wave gaps and final
metrics sampling. This is **not continuous admission or demonstrated saturation**.
Warmup (2,048 input / 400 output, 7.231305336 seconds) and engine startup
(893.227 seconds) are excluded from plateau rates, not from costs. Total
including warmup: 6,883,328 input and 1,344,400 output tokens. The bounded stage
elapsed 463.177408900 seconds. The whole recorder ran for 1,662 seconds.

## Cache, Capacity and Cleanup Limits

Fresh namespaces plus actual zero cached usage qualify this isolated-cache
condition only; they do not qualify warm/repeated-cache throughput. Request
rank is unknown. Both rank-labelled metrics snapshots advertised the same
7,372,889 KV-token capacity and approximately 1,800.02 maximum concurrency,
with zero preemptions before/after. These are shared front-end configuration
advertisements, **not independent per-rank HBM measurements**, and must not be
added together or treated as demonstrated concurrent capacity. Snapshot
running/waiting counts were zero, not a utilization time series. Graph replay
and graph memory remain unobserved. The rate increase across these plateaus
does not establish a hardware ceiling or cause attribution.

The recorder confirms cleanup, engine stopped and container exited 0, not
OOM-killed. Private operator absence checks are not synthesized into public
receipts. No quality, confidential-computing, full P2, public endpoint capacity
or pricing acceptance is claimed. The [prior C8 duration failure](../2026-10-26-selected-sustained-c8/README.md)
remains failed; its unknown cache condition prevents a controlled speedup claim
against this fresh-salt experiment. This batch profile is not the public C1
long-context profile and this evidence does not change public limits.

## Cost Estimates, Not Measured Profitability

Use the operator-supplied **$54.07/hour for all eight GPUs**, not per GPU. For
aggregate measured output rate `R` and assumed utilization `u`, allocating all
node rent to output gives `54.07 * 1000000 / (3600 * R * u)` dollars per million
output tokens. At 100% equivalent utilization this is **$9.48 / $5.25 / $3.39**
for C16/C32/C64; at 50% it doubles. This allocation ignores input revenue.

For the coupled measured **2048:400 input/output mix**, using the plan's
proposed $0.65/M input and $1.45/M output prices, estimated revenue per hour is
`3600 * R * u * ((2048 / 400) * 0.65 + 1.45) / 1000000`:
**$27.25 / $49.18 / $76.24** at 100%. Rent-only break-even is **3,143.458 output
tokens/s** at that same mix. C16/C32 do not reach it; C64 reaches it at an
estimated **70.92% equivalent utilization**, before storage, fees, startup,
idle periods or other overhead. This is not measured utilization, revenue or
profit, and it cannot be transferred to a different input/output/cache mix.

The actual one-off recorder duration at that hourly rate estimates **$24.96**
node rent. All known tokens including warmup would total only **$6.42** at
those proposed prices; these were synthetic tests, not customer sales. Startup
amortization and real demand matter. No competitiveness or sustained margin
claim follows from a short plateau alone.

## Original Bytes and Replay

[run.json](run.json), [output.log](output.log), [receipt.json](receipt.json) and
[input-contract.json](input-contract.json) preserve original bytes; the last
file is the executed `payload/lab.json`, renamed only. Private launcher intent,
process identity, paths and logs remain private. The
[tests](../../../scripts/test_selected_batch32_evidence.py) bind all four hashes
and source closures to the original producer, replay its strict validator,
recompute rates/usage/cost arithmetic and reject altered cache, rank, capacity,
timing and cleanup claims. Scanner exceptions cover only exact public source
and synthetic token hashes at these two specific original paths.
