# Retained endpoint quality testing

The external full-task runner can now use a certificate-pinned retained
`long1m-graphs` endpoint. This is ordinary TLS, not confidential attestation.
No GPU restart, serving configuration change, fallback, trajectory persistence
or benchmark scoring change is introduced.

The private `nib.tau-external-trial-endpoint/v2` contract retains the existing
32K/2048 per-request quality budget (`candidate_profile=quality32k-eager` is the
inherited request contract). It separately records the actual serving profile
as `long1m-graphs` and serving context as 1,048,576. Its additional fields are
`certificate`, `certificate_sha256`, `serving_profile`, and
`serving_context_tokens`. `port` is explicit. The local PEM SHA256, hostname
validation and exact peer DER pin must all pass before authentication.
Simulator/evaluator TLS remains system-root verified on the fixed OpenAI host.
The v2 contract uses Chat Completions for both support roles, preserving the
seed that pinned upstream `LLMConfigMixin.set_seed` injects. The legacy Responses
adapter rejected that seed before making any API call. Prompt-token counting
still uses the fixed input-token endpoint, and every generation must match the
reported count exactly. A mismatch preserves usage but rejects the trial; no
estimated usage, message rewrite or seed dropping is permitted.

Use the unchanged [full-task preparation and invocation](p2-t4-tau-external-full-task.md)
with a fresh source-bound payload. The existing 32-request, 900-second, C1,
zero-retry and $3 generation ceiling remain. The separate conservative count
reservation remains $67.044864; it is not actual spend or an invoice cap.

Joseph explicitly requested this live tau2 and additional benchmark run on
2026-10-26. The node is already retained at $54.62/hour. Plan at most two hours
for the initial diagnostic/calibration work ($109.24 existing GPU allocation),
plus the bounded external support allowance above. Preserve failed attempts.
Do not score a malformed or truncated dialogue as completed, and do not call
one retail trial the full tau2 benchmark.

## Other correctness calibration

`scripts/record-quality-calibration.py` uses the exact prompt formatters and
deterministic scorers in
[TrustedRouter benchmarks at 92c1f4d](https://github.com/Lore-Hex/trustedrouter-benchmarks/tree/92c1f4dea58e32fa772f8ee77b69cd9d7cf67bac).
It verifies the tracked benchmark source and records the dataset hash before
inference. GSM8K and IFEval select the first N cases; MMLU-Pro uses the pinned
loader's proportional stride. These are selected-case calibrations, not official
full-suite headline scores. Failures and skipped cases remain in the denominator.

Use that checkout's frozen `uv.lock` environment and pre-download its public
datasets plus NLTK `punkt`/`punkt_tab` before running. The invocation takes the same
private endpoint, certificate, key and pinned tokenizer inputs as the tau runner,
plus `--trbench-root`, `--suite gsm8k|mmlu_pro|ifeval`, `--count 20|50|200`, and
`--output PRIVATE.jsonl`. Run with the benchmark environment's Python and `-I -B`.
Only counts, timings and numerical scores are written; the benchmark's normal
trajectory/call-log writer is not used. This directly tests the node, not the
TrustedRouter gateway or billing path.

Each run is serial, temperature zero, reasoning disabled, 8,192 output tokens
maximum and 32K combined context; 119 seconds per request and one hour overall.
Three consecutive protocol/transport failures stop the run without retrying.
`scripts/summarize-quality-calibration.py --input PRIVATE.jsonl --output EVIDENCE`
publishes a content-free standard evidence pair. Its `pass` means every selected
case was evaluated; answer accuracy is reported separately.
