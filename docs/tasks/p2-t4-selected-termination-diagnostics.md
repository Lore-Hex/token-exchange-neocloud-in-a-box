# Selected Upstream Termination Diagnostics

The selected operational scorer carries `upstream_termination` through its
terminal pipe frame into `diagnostic.trial`. It is either `null`, when no valid
completed upstream simulation could be projected, or a closed object. Older
records contain exactly:

```json
{"reason":"too_many_errors","tool_errors":1}
```

`reason` is one of the ten values in the pinned upstream
[`TerminationReason` enum](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py#L1234-L1245).
`tool_errors` counts only actual `ToolMessage.error` booleans in the completed
half-duplex simulation. No tool names, IDs, arguments, model text, exception
strings, or hashes of those fields are retained. New runs optionally append
`tool_error_attribution`, described below; older records remain valid without
adding or inferring attribution.

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

## Content-free live tool attribution

New selected runs add this bounded aggregate, for example:

```json
{"path":"live_make_tool_call","counts":[{"requestor":"assistant","known_tool":true,"category":"value_error","count":1}],"unattributed":0}
```

The field is named `tool_error_attribution` inside `upstream_termination`.
It observes only exceptions escaping the original
[`Environment.make_tool_call`](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/environment/environment.py#L170-L200)
during the synchronous live `_execute_tool_calls` batch. Initialization and
evaluation's `set_state` replay are excluded. The original dispatch and
`get_response` still execute, return their original results, and catch their
original exceptions. The hook restores both class and instance methods on all
exits, including `BaseException`. It is selected-only, serial, and not a
general concurrent instrumentation API.

`known_tool` is only membership in the requestor's available pinned toolkit,
using its existing `has_tool` method. It does not identify a tool. `requestor`
is exactly `assistant`, `user`, or `other`; it is the upstream tool-call
requestor, **not a claim identifying which model or component caused bad
inputs**. Pinned retail has assistant tools and no user toolkit. Categories
are exact built-in `ValueError`, `TypeError`, `KeyError`, `IndexError`,
`AssertionError`, or `RuntimeError`, projected to fixed snake-case labels;
every other exception type, including subclasses, becomes `other_exception`.
Exception names, messages, arguments, notes, chains and tracebacks are never
examined by the observer or retained in this diagnostic. Upstream's internal
error response is unchanged and remains inside the existing private scorer.

Counts are aggregated by those three closed fields, never by raw tool name or
content. At most 16 exceptions and 16 distinct rows are retained. Counts plus
`unattributed` must exactly equal the existing completed-simulation
`tool_errors`. Argument-binding failures before dispatch, `sync_tools` errors,
unsupported observation, or an observation that cannot join the completed
simulation remain unattributed, never guessed. Overflow discards attribution
instead of publishing a misleading partial join. If upstream never returns a
valid completed simulation, the entire termination diagnostic remains `null`.

The worker, pipe sender, parent session, and final driver use the same closed
validator. The existing preflight binds the changed worker and diagnostic test
bytes, the complete pinned upstream source tree, and both provider-conformance
worker-source pins. No runtime source bypass or new dependency is introduced.

This cannot recover the exact tool or cause of either historical failed run.
It does not change `max_errors=1`, retries, limits, reward, simulator selection,
or any quality/confidentiality flags. A category such as `value_error` alone
does not distinguish invalid model arguments, simulator-supplied lookup data,
or a tool implementation issue. A future quality benchmark still requires a
qualified simulator; the small support profile remains diagnostic-only.

## Verification

Focused tests cover enum/count validation, canary exclusion, malformed terminal
frames, legacy framing, real private-pipe driver propagation, retained known
usage, and upstream/attach/owner finalization failures. Both provider consumers
pin the exact updated shared worker bytes, including existing rehashed-payload
tamper controls. Attribution controls include exact exception types versus
private subclasses/chains, unchanged return values and exception identities,
method restoration, replay exclusion, bounded overflow, malformed/extra fields,
and rejection before parent acknowledgment.

An optional deterministic source audit accepts the already pinned local archive:

```sh
python3 -B scripts/test_tau_selected_termination.py --upstream-source /private/tmp/nib-tau-fc0055dc-source.tar.gz
```

It verifies the archive and individual source hashes, checks the complete enum,
and executes the original `Environment.get_response`,
`BaseOrchestrator._execute_tool_calls`, and `Orchestrator._check_termination`
methods with synthetic canary tool results. One- and sixteen-tool failing
batches reach the selected worker/pipe projection; matching successful controls
do not terminate. It also executes the original `make_tool_call`, toolkit
dispatch and membership methods through the observer for known/unknown tools,
user dispatch, success, binding failures and synchronization failures, comparing
original response objects before and after instrumentation. It imports no
upstream package and uses no endpoint, model,
Docker, or hardware. This is source-method verification, not pinned-runtime or
model parity.
