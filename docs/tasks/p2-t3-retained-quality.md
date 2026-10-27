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
It verifies the immutable benchmark commit and records the dataset hash before
inference. The import loader compiles those verified source bytes directly;
cached bytecode and untracked benchmark modules cannot replace the scorer.
GSM8K and IFEval select the first N cases; MMLU-Pro uses the pinned
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

### Public HTTPS calibration

The code-scored calibration runner also accepts a separate private
`nib.quality-calibration-endpoint/v1` contract for an existing public HTTPS
endpoint. It requires exactly these fields:

- `schema`: `nib.quality-calibration-endpoint/v1`.
- `model`: the selected checkpoint's catalog ID, unchanged from the v2 contract.
- `serving_profile`: `long1m-graphs`.
- `serving_context_tokens`: integer `1048576`.
- `retire_at`: integer Unix timestamp matching the existing owner's admission
  cutoff, more than one hour and no more than seven days away. A new input
  contract never renews or extends the serving owner's lifetime.
- `tls_mode`: `system-roots`.
- `hostname`: the operator-reviewed certificate hostname, without a URL scheme.
- `address`: its verified public IPv4 address, fixed for this run.
- `port`: integer `443`.

Keep this contract outside the repository and pass its exact SHA256 through
the existing `--contract-sha256` flag. No certificate path, certificate hash,
extra fields, alternate port or TLS auto-detection is allowed in this schema.
The existing bounded HTTPS implementation verifies system-root trust and the
certificate hostname before transmitting credentials. It rejects environment
trust overrides, redirects and address retries. TLS failure cannot fall back to
an unverified connection or the old certificate mode. The old v2 contract still
requires its exact PEM and peer pin and cannot opt into system roots.

Only the calibration runner accepts this new schema; the tau2 simulator,
evaluator, scorer, task selection and transport contracts are unchanged. No
external judge calls are added. Numerical receipts record `candidate_tls_mode`;
historical receipts without it remain explicitly `unrecorded`, not retrospectively
certified. This software support does not claim a new live quality result,
hardware attestation or confidential operation.

Each run is serial, temperature zero, reasoning disabled, 8,192 output tokens
maximum and 32K combined context; 119 seconds per request and one hour overall.
Three consecutive protocol/transport failures stop the run without retrying.
`scripts/summarize-quality-calibration.py --input PRIVATE.jsonl --output EVIDENCE --trbench-root PRIVATE`
publishes a content-free standard evidence pair. Its `pass` means every selected
case was evaluated; answer accuracy is reported separately. It rechecks the
immutable benchmark commit's source tree before publishing a result, validates
suite-specific scores and usage, and retains failed cases in the denominator.
Published results retain each request's transmission marker and reconcile
known and unknown usage totals. A completed answer must have HTTP 200, a stop
finish reason and usage within the declared request limits. Truncated responses
receive no credit. CLI validation failures do not echo arbitrary input values.

### Cache usage accounting

New receipts use `nib.quality-calibration/v2`; their numeric evidence summaries
use `nib.quality-calibration-summary/v2`. Each item retains
`cached_prompt_tokens` from the response's `usage.prompt_tokens_details.cached_tokens`.
Missing or null cache details/counts remain `null` (unknown); an explicit integer
zero means the provider reported no cache hits. Known counts must be integers
between zero and the measured prompt count. Malformed cache accounting rejects
the response but preserves already validated total input/output usage.

Both receipts and summaries reconcile `known_cached_prompt_tokens` and
`unknown_cache_usage_requests`. The latter counts only transmitted requests with
unknown cache usage, including requests with no usable total usage. Unsent cases
are not charged as unknown requests. Historical v1 evidence retains its original
format without invented cache counts. These are reported token counts, not
measured cache savings, invoice reconciliation or proof of a customer discount.
Request payloads, scoring, failure denominators and execution limits are unchanged.

## October 26-27 results

| Calibration | Measured result | Scope |
|---|---|---|
| tau2 | Reward 1.0; zero tool errors | One original retail task, external simulator and evaluator |
| GSM8K | 50/50 correct | First 50 test cases |
| MMLU-Pro | 171/200 correct | 200 proportionally selected cases; one truncation counted wrong |
| IFEval | 174/200 strict; 181/200 loose | First 200 prompts; all requests evaluated |

The [README status](../../README.md#status) links the committed receipts.
The 450 calibration requests and 20 tau generation calls had known token usage.
These establish functional and bounded correctness results, not full tau2
pass^k scores, original-versus-abliterated parity, confidential operation or
production reliability. Further qualification still requires those separate
tests and the plan's admission gates.
