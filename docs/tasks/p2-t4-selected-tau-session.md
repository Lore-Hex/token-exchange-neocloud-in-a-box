# Selected-Checkpoint Tau Session

Status: implemented and tested with synthetic clients and private pipes. Not
wired to the live launchers; no new hardware, tau score or admission is claimed.

This operational policy is separate from the original-model benchmark.
It pins the selected `dealignai/DeepSeek-V4.1-Flash-1017-UNCENSORED-FP8`
checkpoint through the existing model-profile registry, the pinned tau upstream,
retail task `2`, trial `0`, seed `42`, and the existing distinct Qwen3 CPU
simulator/evaluator revisions. The original preflight and comparison manifest
continue to reject it. No matched-reference or benchmark-quality claim follows
from completing one operational trial with these small support models.

The fixed limits are experimental budgets, not measured capacity: 32 total
requests across all roles, 120 seconds per count-plus-generation, a 900-second
trial, 960-second run, 256 output tokens per request, and 270,336 reserved tokens
maximum. The candidate context is 8,192; each CPU role remains 4,096. Neither
system instructions nor later history are truncated. Every request is counted
with its original tools/history before generation; context exhaustion fails
closed. These limits do not prove that this task fits or will complete.

`tau_selected_session.Session` accepts exactly two independently admitted
process-owner coordinators: the GPU candidate and the two CPU roles. Their
readiness bindings, role/model identities and context limits must match the
transport manifest. A readiness digest is not authentication: the caller still
must independently verify the live producer, pinned runtime, tokenizer parity,
fresh prefix, and both readiness reports before constructing this session.
No arbitrary endpoint, process ID, key or URL is accepted here.

The session uses the existing central Bridge and sticky resource Budget. The
count-before-dispatch implementation is shared with the existing local vLLM
session, including same-or-shorter request deadlines and exact usage counts.
Unknown usage retains its reservation and aborts without retry. Known owner
errors keep their original category. All original process identity guards run
beside their engines, not in the scorer container.

Results carry a separate schema and `quality_eligible=false`,
`confidential=false`, `live_admission=false`, and `pricing=not-measured`.
`usage.cost_usd` is null, not a claim of free inference. GPU rental remains an
external finite approved cost. Raw task histories are not returned in results.

The [selected scorer](p2-t4-selected-tau-scorer.md) packages the fixed upstream
trial and all evaluators in its own explicit mode. Still required: live GPU/CPU
owner launch integration, a selected-model prefix measurement and an actual
upstream trial. The earlier two-step CPU support probe is not a substitute.

Verification: `test_tau_selected_session.py`, existing `test_tau*.py`, full
`make check`, hosted CI, and independent final-head review. Synthetic tests
prove accounting, role separation, context/deadline rejection, unknown usage,
count mismatch, truncation, and real-pipe transport, not model performance.
