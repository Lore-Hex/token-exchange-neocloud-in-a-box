# Selected Checkpoint: 32 Concurrent Waves

Measured 2026-10-26 02:17:15-02:39:09 UTC by committed producer
`dd4705c3483bb9f333642c5a904c9d7b74396e09` on eight ordinary B300 GPUs,
with confidential computing disabled. The selected abliterated checkpoint and
`batch8-graphs` TP4 x DP2 engine are unchanged from the earlier C8 burst.

**The qualification failed: insufficient measured duration, not request errors.**
All 256 measured requests completed, but they finished in 114.279358806 seconds,
below the predeclared 120-second minimum. The original failure is retained;
there was no duration padding, retry, changed threshold or inferred pass.

| Measurement | Result |
|---|---|
| Offered concurrency | Eight, in 32 successive waves |
| Actual measured input / output tokens | 524,288 / 102,400 |
| Actual output tokens per request | 400, under a 512-token ceiling |
| Aggregate output rate | 896.05 tokens/second; 112.01 per GPU |
| TTFT median / p95 | 205.68 / 1,738.12 ms |
| Completed / failed / unknown usage | 256 / 0 / 0 |
| Separate warmup | 2,048 input + 400 output; 6.933319799 seconds |
| Engine startup, excluded from measurement | 888.152 seconds |
| Cleanup | Collectors reaped, engine stopped, container exited 0; not OOM-killed |

The rate denominator includes measured setup, dispatch, prefill, collection,
reaping and inter-wave identity checks. Warmup and startup are excluded from
that rate, not from costs. The total run used 526,336 known input and 102,800
known output tokens including warmup. Runtime identity remained stable.

The same eight synthetic fixtures repeat. Cached-token counts are unknown,
not zero. There is no cold-cache control, per-rank identity, demonstrated
saturation, quality acceptance, production cache-discount qualification or
confidential claim. Eight sequences per rank permits more work than the eight
offered across the endpoint. Wave boundaries also leave gaps; this is not an
uninterrupted saturated load test. The old single-wave result is not a
controlled apples-to-apples improvement baseline.

**Cost estimate, not measured profitability:** at an operator-supplied
$54.07/hour for the entire node, allocating all GPU rent to output gives
`54.07 * 1,000,000 / (3600 * 896.049829732)` = **$16.76/M output tokens**
at 100% utilization, or $33.52 at 50%. This excludes startup, storage, idle
time and fees; it also ignores input revenue and is not a blended margin.
The measured result does not justify competitive selling prices yet.

## Preserved Transport Failure

A first launch at 02:07:36 UTC rejected the transported bundle before GPU
execution. The macOS archive included 190 `._*` AppleDouble entries despite
`--no-xattrs`; the committed bundle inventory guard rejected the file count.
The archive was rebuilt with `COPYFILE_DISABLE=1`, `--no-xattrs` and
`--no-acls`; the original receipt and every source byte stayed unchanged.
The subsequent clean bundle passed the existing validator. No safety gate
was relaxed. Private operational records of the rejected launch are retained
separately; it produced no benchmark result and is not a hardware measurement.

[run.json](run.json), [output.log](output.log), [receipt.json](receipt.json)
and [input-contract.json](input-contract.json) are the original producer bytes.
The contract is the executed `payload/lab.json`, renamed only. The
[regression tests](../../../scripts/test_selected_sustained_evidence.py)
join hashes to the original producer, re-run its validator, recompute totals
and reject fabricated success, cache usage, rates and cleanup.
