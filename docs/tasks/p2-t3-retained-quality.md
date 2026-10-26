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
