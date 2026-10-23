# Complete Single-Concurrency Streaming Baseline

Producer `ecddc03f2fd1e39501b92048afe9ab66bc1c8c78` ran on the retained
eight-B300 CC lab from 2026-10-23 03:41:08 to 04:13:29 UTC. The explicit
`chat-2k-512-c1-600s` profile completed its unchanged one warmup and eight
measured requests. All four tokenizer-parity cases also passed across eight
separate prerequisite HTTP requests. These are synthetic local requests, not
customer traffic or a quality benchmark.

## Observed Results

| Measurement | Result |
|---|---|
| Measured requests | 8/8 complete, zero failed or skipped |
| Context and generation | 2,048 input tokens per request; 512 output cap, 400 actual output tokens |
| Total stream usage, including warmup | 18,432 input / 3,600 output tokens |
| Measured usage, excluding warmup | 16,384 input / 3,200 output tokens |
| Measured wall time | 301.078 seconds |
| Measured output throughput | 10.628 tokens/second over measured wall time |
| Eight-GPU normalized throughput | 1.329 tokens/second/GPU at concurrency one |
| TTFT, eight measured samples | p50 286.824 ms / p95 565.222 ms |
| Warmup | 43.231 seconds, 400 output tokens |
| Complete cohort including warmup | 344.309 seconds |
| Engine readiness wait | 1,225.332 seconds, separate from request timings |
| Cache-read usage | Not reported; unknown, not zero |
| Cleanup | Engine stopped, container exited 0, no OOM, removal confirmed |

Throughput is `3200 / 301.0780012059986`; GPU-normalized throughput divides
that value by all eight allocated GPUs. This is a serial workload on TP4/DP2,
not saturated node capacity or per-active-GPU throughput. The eight-sample
percentiles are descriptive, not stable production tail estimates. Each request
contained 399 output events but reported 400 output tokens; events are not
tokens. The interval field is an estimate from stream timing and actual usage,
not a measured interval for every generated token.

## Original Evidence And Validation

The four JSON files are byte-for-byte originals: `run.json`, structured
`output.log`, prepared `input-contract.json`, and `receipt.json`. The last two
bind the exact 110-file payload/support manifest to its historical producer.
No private console, prompts, responses, credentials or endpoint details are
published. Tests pin all four artifact hashes, reconstruct source provenance,
replay the cohort validator, check usage arithmetic and reject a fabricated
quality/confidential verdict or replacement of token counts by event counts.

The exact producer's `bundle_inputs` and `validate_result` passed on the
original remote bundle before copying these files; local hashes matched the
remote originals. A separate read-only check found the owned process and
container absent at 04:15:06 UTC. The VM, weights, cache and key were retained.

## Limits

`functional_pass=true` here means this bounded experiment completed. The
original overall result remains `fail`, status `BLOCKED`, `deployable=false`
and `confidential=false`; none was edited to manufacture qualification.
The original [180-second partial run](../2026-10-23-stream-window-exhaustion/README.md)
remains failed. This explicit rerun changed only the cohort window, not the
requests, request timeout, token ceilings, context or resource caps.

No pricing, original tau trial, GSM8K/MMLU-Pro quality comparison, concurrency
sweep, sustained service, full image provenance or CPU/TLS/GPU confidential
chain was qualified. GPU CC mode is not proof of an end-to-end confidential
service. These results do not satisfy the full P2 or P5 exit criteria.
