# P2.T3 Explicit Tau Evaluation Mode

The scripted trial recorded in
[`evidence/p2/2026-10-20-tau-default-evaluator-902b473`](../../evidence/p2/2026-10-20-tau-default-evaluator-902b473)
finished with `infrastructure_error`, one agent call, two simulator calls and no
evaluator call. Cleanup was confirmed. The original failed evidence is unchanged.

The pinned upstream airline task 0 has an NL assertion but its reward basis is
`DB, COMMUNICATE`. Tau's default `ALL` evaluation only invokes its NL evaluator
when the reward basis contains `NL_ASSERTION`. Our bridge requires the evaluator
to run for every declared assertion, so the default conflicts with that check.
The historical recorder intentionally does not emit exception details; the
source-level mismatch is proven, not an exception traceback claim.

Sources, all pinned to `fc0055dc4e0a316c3f83133267fbd6faaa770992`:

- [Airline task definitions](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/data/tau2/domains/airline/tasks.json)
- [Default runner evaluation mode](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/batch.py)
- [Evaluator dispatch](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator.py)

Pass `ALL_WITH_NL_ASSERTIONS` explicitly. This keeps upstream task scoring and
the all-role accounting checks intact; it does not inject a score or replace the
upstream evaluator. The regression fixture models assertions outside the default
reward basis and requires the explicit mode at the runner boundary.

Verification: `python -I -S -B scripts/test_tau_trial.py` (38 tests). These are
local software tests, not model quality, GPU, ABI, attestation, or P2 acceptance.
The trial remains scripted, network-disabled and credential-free. A real
candidate/reference benchmark is still required.

## Recorded Corrected Trial

The exact committed fix `b6c203d` completed the pinned upstream trial locally:
[`run.json`](../../evidence/p2/2026-10-20-tau-explicit-evaluator-b6c203d/run.json),
[`output.log`](../../evidence/p2/2026-10-20-tau-explicit-evaluator-b6c203d/output.log).
One agent call, two simulator calls and one evaluator call completed; all four
had known synthetic usage, and cleanup was confirmed. The integration result
is `pass`, while `quality_accepted`, `live_transport`, and `confidential` remain
false. The upstream reward is a scripted fixture result, not an LLM score.

The corrected trial used the unchanged network-disabled container, pinned
Python image, source archive, wheelhouse, manifest, task and seed. Only the
toolkit revision and its bound payload digest changed. No credentials or model
endpoints were used. The exact command and times are in the evidence record.
