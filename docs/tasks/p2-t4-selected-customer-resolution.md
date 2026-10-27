# P2.T4: Natural Customer-Resolution Diagnostic

Status: opt-in software with a [failed bounded observation](../../evidence/p2/2026-10-25-selected-customer-resolution-1117/README.md).
The result-decoding correction and failure-stage reporting have offline tests only. No successful
customer resolution, simulator qualification or tau quality score is claimed.
The default selected operational trial and CPU identity probes are unchanged.
An explicit [external-simulator variant](p2-t4-tau-external-simulator.md) reuses
this first-lookup boundary without creating or restarting a candidate owner;
it is separately gated and has no live result yet.

## Scope and Source

This mode asks the selected candidate to converse naturally with the pinned
Qwen3-1.7B simulator on retail task 2, seed 42, trial 0. It does not supply the
fixed assistant questions used by the earlier CPU identity probe, insert correct
tool arguments, alter upstream prompts/tool definitions, or repair model output.
The task remains the pinned original. The diagnostic stops at the first single
assistant customer-lookup attempt, before another completion or evaluation.
An unrelated tool or multiple-tool batch is rejected before tool execution;
this deliberately bounded experiment is not the full upstream task protocol.

Primary source is tau2 `fc0055dc4e0a316c3f83133267fbd6faaa770992`:

- [Original half-duplex step and tool batch](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/orchestrator/orchestrator.py):
  `initialize`, `step`, `_check_termination`, `_execute_tool_calls`, text stop hooks.
- [Original environment error boundary](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/environment/environment.py):
  `get_response` preserves tool errors rather than repairing them.
- [Original retail tools](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/retail/tools.py):
  `find_user_id_by_email` and `find_user_id_by_name_zip` return a customer ID or
  raise. Their unmodified arguments and results are transient, never reported.

The intended-customer comparison is derived only from hash-checked task and
database bytes through the existing identity-fixture verifier. It is not
derived from the candidate's claim, nor copied into its request. The comparison
does not establish correct reasoning, customer intent or completion of the task.

### Successful Lookup Representation

The original producer `975738d7eb26e3d315a41857ece0d70d757db4ab` incorrectly
JSON-decoded `ToolMessage.content` on a successful lookup. In the pinned source,
[retail lookup returns a string ID](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/domains/retail/tools.py#L305-L330),
and [Environment.to_json_str returns string inputs unchanged](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/environment/environment.py#L412-L441).
[get_response](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/environment/environment.py#L463-L490)
uses that serializer, so the ID is not a JSON string literal. Both the synthetic
fixture and the earlier optional audit incorrectly substituted `json.dumps`.

The correction compares the original nonempty string directly, retaining its
256-character bound and the upstream `error` check. It neither JSON-decodes,
strips, case-folds nor repairs the result. A different ID remains
`wrong_customer`; an upstream error or invalid result remains `lookup_error`.
No tool arguments, prompts, limits, retries, evaluation or report schema change.
The existing source closure includes this module, so prepared scorer inputs
must be regenerated from the reviewed producer; no source pin is bypassed.

The failed original receipts are unchanged. Their `lookup_error` was set before
tool execution and also survives local result-validation failure. They therefore
cannot distinguish an upstream lookup error from this decoding defect; this fix
does not retrospectively establish lookup success or blame either model.

### Content-Free Failure Stage

New observations use `nib.tau-selected-customer-resolution/v2`, adding only
`failure_stage` to the nested observation. The enclosing trial/input schemas,
limits and serving profiles are unchanged. The validator still accepts exact
v1 observations without adding a field or inferring a cause, so original
receipts remain immutable and replayable. Prepared scorer inputs must bind the
new producer through the existing source closure; old bundles are not upgraded
or admitted by bypassing source checks.

The field is null unless one of these closed stages was observed:

- `upstream_lookup`: the original tool batch returned a correctly shaped
  `ToolMessage` with `error=true`. This identifies the upstream error boundary,
  not a specific exception, missing customer, argument defect or model fault;
  upstream `get_response` also catches `sync_tools` exceptions.
- `result_validation`: the batch returned, but its envelope, error flag or
  successful customer-ID representation failed local validation.
- `execution_exception`: an ordinary exception escaped the original batch
  before it returned. No exception type, text, traceback or arguments are read
  or recorded by this diagnostic.

All three retain `outcome=lookup_error`, a null customer match and null reward.
Null means no stage was established, not success: timeout/cancellation during
execution keeps the original terminal status and an unknown stage. Wrong
customer and successful lookup observations also have null stages. A later
cleanup failure still rejects the trial without rewriting an observed lookup
stage. Only `resolved` can complete successfully, as before.

This is offline-tested observability only, not a new run or retrospective
diagnosis. The original 11:17 observation has no failure stage; neither the
decoding fix nor this addition establishes its cause. No prompts, completions,
customer IDs, arguments, raw tool results, exception text or secrets are added
to reports. No tool retry, model substitution or evaluator call is added.

## Admission and Limits

Use the existing enclosing selected launcher with explicit
`--customer-resolution-17b`. Its sealed manifest selects
`nib.tau-selected-customer-resolution-input/v1`. No arbitrary endpoint, model,
task, role remap or limit overrides are added. The source archive, prepared
scorer and GPU owner bundle must all bind the reviewed producer as before.
Old bundles cannot be reused by bypassing source admission.

The original CPU owner still calls its 1.7B slot `evaluator`. The diagnostic
explicitly maps logical `simulator` to that slot; it does not relabel receipts.
Admission joins the exact original runtime digest to READY, and binds model,
revision, process, model-files, tokenizer and framing hashes. The parent creates
no evaluator endpoint and rejects evaluator requests before owner dispatch.
The unused 0.6B slot retains the ordinary pair startup/parity prerequisites.

Unchanged ceilings: candidate 8192 context, simulator 4096 context, 256 output
tokens, concurrency one, zero retries, 32 steps/requests, 120-second requests,
900-second trial and 960-second run including the existing cleanup reserve.
Every operation remains clipped to the original admitted owner expiry and
operator cutoff. Existing truncation/cap guards and sticky actual/unknown usage
accounting remain in force. No automatic restart, renewal or deadline extension.

## Closed Outcome, No Reward

The trial report uses `nib.tau-selected-customer-resolution-trial/v1` and includes
the parent-bound simulator identity plus a closed observation:

- Tool category: `none`, `email` or `name_zip`, never an arbitrary tool name.
- Outcome: `not_attempted`, `resolved`, `wrong_customer`, `lookup_error`,
  `unsupported_tool` or `multiple_tools`.
- `intended_customer_match`: true/false only after a successful original lookup;
  otherwise null. No customer ID, arguments, reply or exception text.
- `failure_stage`: the v2-only closed stage or null described above; absent
  from unchanged v1 observations.

Only `resolved` can complete successfully. The internal completion status
`evaluated` is retained for existing owner finish/count handshakes, but in this
sealed mode it requires a **null reward**, zero evaluator requests and the
closed resolved observation. No upstream termination reason is invented.
Ordinary scored terminals still require their original reward/evaluator rules.
`run_single_task`, evaluator and trajectory finalization are not called here.
Both original text stop hooks run even after a failure; stop/owner/container
cleanup failures cannot become success. Recorded usage survives later failure;
unknown usage is not zero.

All quality, live-admission, confidentiality and pricing claims remain unchanged
and unqualified. A successful lookup is neither simulator qualification nor
the [original-revision matched-reference quality gate](../benchmarks/tau-validation.md).
Independent final-head review, full CI and a separate operational approval are
required before any real run. This implementation does not modify R2 or authorize
CPU/GPU/cloud execution, public endpoints or production changes.

## Software Checks

[Regression tests](../../scripts/test_tau_customer_resolution.py) use synthetic
dialogue, original-receipt fixtures and real subprocess pipes with fake owner
responses. They cover unchanged defaults, role/pin drift, evaluator rejection,
wrong customer/error/multiple/unrelated lookup, cancellation/deadline/cleanup,
null reward, usage/cap failure and content canaries. An optional offline audit
hash-checks and executes original pinned retail/environment/batch method bodies
including `to_json_str` on a synthetic database; it makes no model or network
calls. The ordinary tests also execute the original producer against a plain
string response to reproduce the regression and cover literal comparison,
malformed results, error precedence and content-free failure reports.
They also replay original v1 observations, reject unknown v2 fields/stages and
inconsistent outcomes, preserve each stage through parent accounting, and test
execution exceptions, in-lookup cancellation/timeouts and cleanup with content
canaries. The optional pinned-method audit covers both successful lookup methods,
wrong customer, malformed result, upstream error, escaping exception,
cancellation, timeout and cleanup using synthetic data only.

For an already verified prepared scorer payload containing `upstream/` and
`bench/tau-upstream-pins.json`, the optional source audit is:

```sh
python3 -B scripts/test_tau_customer_resolution.py --upstream-root /path/to/scorer-payload
```
