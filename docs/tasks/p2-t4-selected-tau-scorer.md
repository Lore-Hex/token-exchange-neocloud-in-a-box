# Selected-Checkpoint Tau Scorer

Status: offline packaging and isolated worker dispatch implemented; synthetic
tests only. No new live trial, quality score or confidential claim.

The [selected session policy](p2-t4-selected-tau-session.md) now has a distinct
sealed scorer plan. `tau_trial.prepare(..., parent_pipe=True,
selected_operational=True)` verifies the same pinned upstream archive, wheel
catalog and source closure as the existing scorer. It fixes retail task `2`,
trial `0`, seed `42`, the selected checkpoint and distinct CPU support models.
Callers cannot supply a substitute selection or comparison manifest.

The selected plan cannot be used by the legacy `tau_trial.run` entry point,
CPU support probe, prefix-only probe or old live deployment. Its worker must
receive both `--parent-pipe` and `--selected-operational`, and validates the
matching schema, transport, fixed policy and authenticated upstream task.
The Python scorer has no GPU, network, credentials or engine socket; all role
completions go through the private parent pipe. No scripted candidate fallback
is available in this mode.

The original upstream `run_single_task` runs the complete trajectory, with
`ALL_WITH_NL_ASSERTIONS`, no retries, no auto review/resume and no logs. The
selected operational trial has 900 seconds inside a 960-second worker deadline.
Verification and the private pipe share that absolute outer deadline; they do
not reset it. The original scorer retains its 180-second trial, 240-second
worker and 300-second host bounds. Timeouts, truncation and upstream errors
remain failures, not successful scores.

Packaging does not admit a live deployment. Still required: a host runner that
joins independently verified candidate/support owner channels from one exact
source closure, limits its entire lifecycle and cleanup to the selected run
budget, verifies the selected model's fresh 8K prerequisite and publishes only
content-free results. It must not loosen exact coordinator type or readiness
checks. One operational trial is not the full matched-reference tau benchmark.

Verification: `test_tau_selected_scorer.py` covers source closure, incompatible
modes, plan/task mutation, worker invocation, unchanged confinement, the real
runner/evaluator interface using synthetic modules, legacy timeouts, and typed
failure propagation. Full `make check`, hosted CI and independent final-head
review are required before merge. No hardware result is claimed by these tests.
