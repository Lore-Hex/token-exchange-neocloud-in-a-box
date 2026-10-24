# Selected Upstream Termination Diagnostics

The selected operational scorer carries `upstream_termination` through its
terminal pipe frame into `diagnostic.trial`. It is either `null`, when no valid
completed upstream simulation could be projected, or exactly:

```json
{"reason":"too_many_errors","tool_errors":1}
```

`reason` is one of the ten values in the pinned upstream
[`TerminationReason` enum](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py#L1234-L1245).
`tool_errors` counts only actual `ToolMessage.error` booleans in the completed
half-duplex simulation. No tool names, IDs, arguments, model text, exception
strings, error categories, or hashes of those fields are retained.

Pinned upstream
[`get_response`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/environment/environment.py#L465-L490)
marks failed tool executions. The original orchestrator
[counts each failed tool](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/orchestrator/orchestrator.py#L313-L329)
and [checks the error limit after the batch](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/orchestrator/orchestrator.py#L734-L750).
The unchanged `max_errors=1` therefore permits a final count of 1 through 16,
the existing maximum tools per accepted response. Timeout can supersede the
error reason in that same check. Other termination reasons require zero errors.
The parent additionally bounds the count by accepted agent/simulator requests.

The selected terminal field is mandatory, strictly validated, and cannot
authorize an evaluated result with an absent or failed upstream termination.
Legacy modes retain their exact terminal-frame field set. Invalid diagnostics
fail closed without acknowledgement or publication. A later scorer exit,
deadline, or owner-finish failure retains already validated upstream facts and
measured usage but removes reward. A failure before upstream returns a completed
simulation produces `null`, not an inferred reason or zero error count.

No budgets, retries, source admission, owner identity checks, cap guards,
`max_errors`, evaluator behavior, or quality eligibility change. These are
diagnostics, not hardware success or benchmark qualification. They do not
retroactively recover the missing tool/error detail from the 1854 run.

## Verification

Focused tests cover enum/count validation, canary exclusion, malformed terminal
frames, legacy framing, real private-pipe driver propagation, retained known
usage, and upstream/attach/owner finalization failures. Both provider consumers
pin the exact updated shared worker bytes, including existing rehashed-payload
tamper controls.

An optional deterministic source audit accepts the already pinned local archive:

```sh
python3 -B scripts/test_tau_selected_termination.py --upstream-source /private/tmp/nib-tau-fc0055dc-source.tar.gz
```

It verifies the archive and individual source hashes, checks the complete enum,
and executes the original `Environment.get_response`,
`BaseOrchestrator._execute_tool_calls`, and `Orchestrator._check_termination`
methods with synthetic canary tool results. One- and sixteen-tool failing
batches reach the selected worker/pipe projection; matching successful controls
do not terminate. It imports no upstream package and uses no endpoint, model,
Docker, or hardware. This is source-method verification, not pinned-runtime or
model parity.
