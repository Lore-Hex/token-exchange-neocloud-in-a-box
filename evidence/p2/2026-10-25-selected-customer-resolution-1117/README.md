# Natural customer-resolution diagnostic: lookup failure

Original producer `975738d7eb26e3d315a41857ece0d70d757db4ab`,
2026-10-25 11:17:59-11:37:54 UTC. All twelve controller, GPU, CPU and scorer
files are byte-preserved. [Tests](../../../scripts/test_selected_resolution_evidence.py)
pin their sizes and hashes, producer ancestry, the scorer's 39-file closure,
the CPU source closure, model/process/tokenizer binding, owner finish and cleanup
joins. No inference was performed to prepare this evidence.

## Observation and Limits

The [controller](evidence/output.log) and [scorer](scorer/output.log) retain
**FAIL** / `protocol_error`. The first recognized customer-lookup attempt was
`tool=email`, `outcome=lookup_error`, `intended_customer_match=null`.
Natural retail task 2, trial 0, seed 42 used ten requests: five candidate-agent,
five simulator and zero evaluator. Measured trial time was 35,821 ms; usage was
27,618 prompt +375 completion =27,993 tokens. Reward and dollar cost remain null.
The scorer protocol completed with exit 0, but that is not a successful trial.

The [source-bound diagnostic](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/blob/975738d7eb26e3d315a41857ece0d70d757db4ab/scripts/tau_customer_resolution.py#L28-L68)
sets `lookup_error` before invoking the upstream tool boundary and only replaces
it after a validated, non-error customer result. Thus this record establishes
an unsuccessful first email-lookup attempt, not its precise underlying cause.
An upstream error or a failure validating its result can retain this state.
`upstream_termination` is null: there is no exception category, source line,
submitted email or prior-user-text relation in this diagnostic. Do not import
the earlier run's line-330 attribution or containment boolean into this one.
Null customer match is not a proven wrong-customer match.

The simulator was the pinned **Qwen3-1.7B**, revision
`90862c4b9d2787eaed51d12237eafdfe7c5f6077`, joined to its original
[evaluator-named owner receipt](cpu/evaluator-runtime.json). The receipt keeps
its original role; the scorer explicitly binds that process to the simulator
role. The original Qwen3-0.6B receipt remains present but is not the selected
dialogue simulator. There were no evaluator requests. Both support models remain
unqualified for tau quality measurement. The earlier
[fixed three-turn 1.7B diagnostic](../2026-10-25-tau-cpu-identity-17b/README.md)
proved its own transport and literal checks, not natural-dialogue reliability.
This failure does not assign model blame or establish DeepSeek quality.

The diagnostic stops after the first customer-lookup attempt. It does not run
task finalization or grading. The committed bounds remain concurrency 1,
32 steps/requests, 256 output tokens per request, a 900-second trial, 960-second
lease and zero retries. Candidate context is 8192 tokens; simulator context is
4096. No request, model response, tool arguments or exception text is retained.

## Startup and Cleanup

The selected abliterated checkpoint remains revision
`c19ca5ca4eaeb79d29fdf08aea8327da95b1116a`, ordinary eight B300s, DP2/TP4,
`tau8k-eager`, driver 580.178.04, CUDA driver API 13.0 and CC disabled.
The [GPU owner](gpu/output.log) reached readiness in 847,167 ms and passed four
fixed tokenizer-parity cases. This is not full 8K request-fit qualification.
The CPU pair's eight parity cases also passed before the dialogue.

Both owners acknowledged `protocol_error` after five requests each and exited 1.
GPU engine stop, CPU resource cleanup, scorer attach reaping and controller
cleanup are confirmed in the original reports. The outer GPU qualification is
still FAIL/BLOCKED. Quality, live-admission, deployment and confidentiality claims
remain false. Startup, measured usage and cleanup do not promote the trial.

Private launch intent, PID/start identities, paths and wrapper console logs are
not published. The wrapper exit and the operator's separate post-run inventory
observations are not additional committed producer evidence here. No hardware
retry, model comparison, full tau score or public-service claim follows.

## Scanner Scope

The initial scanner reported 51 matches, all in exact public source hashes,
fixed synthetic parity token hashes or copied receipt hashes. The scoped rules
bind each exception to this evidence path, field and literal value. Existing
negative controls plus the new path cases reject unknown values, wrong fields,
other paths and adjacent synthetic credentials. No private journal or general
hash-shaped value is exempted.
