# DeepSeek serving-quality validation

Status: **manifest/budget, aggregation, isolated scorer/container wiring,
runtime-identity checks and selected-owner lifecycle implemented;
selected calibration evidence recorded; original-revision full quality gate
unproven** (2026-10-27). The
[preflight foundation](../tasks/p2-t3-tau-preflight.md) verifies pinned public
inputs without importing upstream or contacting endpoints. The
[content-free aggregator](../tasks/p2-t3-tau-aggregation.md) checks frozen coverage
and computes fixture-tested arithmetic; no full tau benchmark score has been measured.
The [local transport](../tasks/p2-t3-tau-local-vllm.md) tests actual loopback
HTTP against synthetic endpoints, including engine-reported input counts and
the shared cost ledger. The [isolated runner](../tasks/p2-t3-tau-container-pipes.md),
[live runtime-identity checks](../tasks/p2-t3-tau-live-runner.md),
[selected scorer lifecycle](../tasks/p2-t4-selected-tau-lifecycle.md) and
[enclosing controller](../tasks/p2-t4-selected-tau-launch.md) are implemented,
not proof of model quality or confidentiality.

Separate selected abliterated-checkpoint observations include
[8K tokenizer parity](../../evidence/p2/2026-10-24-selected-tau8k-parity/output.log)
and [eight concurrency-one graph-stream requests](../../evidence/p2/2026-10-24-selected-graphs-stream/output.log),
not scored tau tasks. The earlier
[October 24 controller failure](../../evidence/p2/2026-10-24-selected-tau-readiness-failure-1800/README.md)
retains no READY/scorer result and unconfirmed cleanup. The separate
[CPU-only parity pass](../../evidence/p2/2026-10-24-cpu-http-threads-pass/output.log)
does not promote that failure.

The October 25 [selected tau diagnostic](../../evidence/p2/2026-10-25-selected-tau-email-relation-0429/README.md),
producer `32771fb4ca03cc25ab3177b70c431813cd1a2bf0`, reached GPU readiness in
840,203 ms and passed four tokenizer-parity cases. Its
[joined controller/scorer report](../../evidence/p2/2026-10-25-selected-tau-email-relation-0429/evidence/output.log)
records **FAIL** / `protocol_error`: `too_many_errors` after one tool rejection
at the retail customer-email lookup. The trial made six requests (three agent,
three simulator, zero evaluator) in 25,040 ms; reward remains null. Both owners,
the scorer and the controller confirmed cleanup. This establishes startup and
scorer execution, not a successful task or quality score. The bounded
prior-user-text relation does not establish the correct customer identity,
simulator correctness or which model caused the failure. The Qwen3-0.6B
simulator and Qwen3-1.7B evaluator remain unqualified support models.

The October 26 [external retail trial](../../evidence/p2/2026-10-26-tau-external-retail-pass/README.md)
used the pinned original tau task 2, trial 0, seed 42, with
`gpt-4.1-2025-04-14` as simulator and evaluator. It scored reward **1.0**,
terminated normally with zero tool errors, and retained known usage for all
14 agent, five simulator and one evaluator requests. This is one selected
task success on the abliterated checkpoint, not full-domain coverage or a
matched-reference comparison. The earlier failed attempts remain evidence.

Separate October 27 selected-case calibrations are also recorded:
[IFEval](../../evidence/p2/2026-10-27-quality-ifeval-200/README.md)
passed 174/200 strict and 181/200 loose prompt checks on the first 200 of 541
prompts, with no retries, truncations or missing cases.
[MMLU-Pro](../../evidence/p2/2026-10-27-quality-mmlu-pro-200/README.md)
scored 171/200 correct on a systematic-stride selection from 12,032 cases.
Its receipt remains **FAIL** because one answer reached the output cap; that
case counts as incorrect and the denominator remains 200. Neither calibration
is the full benchmark, a matched-reference comparison or confidential evidence.

These operational experiments use a different checkpoint from the original revision
required below and cannot satisfy its matched-reference quality gate.
This is an additional
P2.T3 acceptance requirement requested by Joseph, repeated under confidential
computing in P5.T5. We serve DeepSeek's existing weights; this is not a new
trained model. An HTTP 200 or PONG proves neither model quality nor confidentiality.

## Freeze the evaluation

Use the text track from Sierra's
[tau2-bench v1.0.1](https://github.com/sierra-research/tau2-bench/tree/fc0055dc4e0a316c3f83133267fbd6faaa770992),
commit `fc0055dc4e0a316c3f83133267fbd6faaa770992`, with its locked dependencies.
Evaluate `airline`, `retail`, and `telecom` on the `base` split, using the standard
agent, tools, policies and evaluator. Banking, retrieval and voice are outside
this first evaluation; report that scope, not "all domains".

This is the 1.x text protocol, not a claim to reproduce an original tau2 paper
score. Upstream has since introduced a different 2.0 protocol; its
[versioned submission guide](https://github.com/sierra-research/tau2-bench/blob/989d3285ae669e49503324bdb9981bf14fccedeb/docs/leaderboard-submission.md)
explicitly says 1.x and 2.0 scores are not comparable. Never track `main` or
compare leaderboard numbers without matching the complete evaluation setup.

Before requests, freeze a manifest containing:

- Toolkit commit; benchmark revision and task-file hashes; sorted task IDs,
  trial seeds and count; adapter revision and dependency-lock hash.
- Model pack and all-file manifest, tokenizer/chat-template hashes, actual
  engine image/overlay digest and effective serving arguments.
- Agent sampling, reasoning, context/output limits and tool/reasoning parsers;
  simulator and any evaluator model revisions and settings, independently of
  the model under test. Do not silently use the tested endpoint as its own
  simulator or judge.
- Fixed target roles (`candidate`, `reference`), retry policy, concurrency,
  turn/request/run deadlines, token ceilings, dollar ceiling and teardown.
  Credentials and private endpoints remain in the operator workspace.

The candidate is
[DeepSeek-V4.1-Flash-1017@1](../../models/deepseek-v4.1-flash-1017/1/pack.yaml),
weight revision `1bae0e1088993b9b82de08f59de5be5a34e42170`. The pack is
currently a draft, not a serving result. A cost-approved, independently
qualified reference must serve that same revision, not a mutable "latest"
alias. Record precision, quantization and any known runtime differences. If
reference provenance is unknown, report a contextual comparison only: it
cannot satisfy the serving-equivalence gate.

## Run in stages

1. **Transport and parser checks.** Complete provider-check Tiers 1-4 directly
   against our front door, plus deterministic tool-call round trips: valid JSON
   arguments, tool-call IDs, tool results continued into the next turn,
   streaming assembly, finish reasons and reasoning/content separation.
   Test the actual candidate without a model router or alternate-provider
   fallback. Do not repair malformed model output into a passing result.
2. **Small agent smoke.** Preselect five tasks per domain from the frozen
   ordered task list, one trial each (15 conversations per endpoint). Begin
   at concurrency one. This catches integration errors and measures token,
   latency and simulator spend before sizing the full run. It is not a
   publishable capability score, and task failure is not automatically a
   serving bug. Never choose tasks based on observed success.
3. **Scored comparison.** Run every `base` task in each of the three selected
   domains, four trials per task, for both candidate and reference with the
   same simulator, task/trial seeds and evaluation settings. Preserve the
   upstream evaluator; changes to its prompts, tools, scaffold or grading
   require a separately labelled custom evaluation. The
   [pinned text guidance](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/docs/leaderboard-submission.md)
   recommends at least four trials and consistent configuration.
4. **Confidential repeat.** After independent CPU/GPU attestation and weight
   integrity gates pass, repeat the same frozen evaluation on the confidential
   profile and compare it to the plain profile. Do not infer confidentiality
   from a benchmark score, a CC product name or encryption alone.

## Report and acceptance

Report per domain: completed/expected tasks and trials, upstream Pass^1 and
Pass^4, tool/protocol failures, timeouts, truncation and infrastructure errors.
Pass^4 measures repeated reliability, not best-of-four success. Also report
end-to-end task latency, separately measured TTFT/TPOT where available, input,
cached-input, output and reasoning tokens where reported, and all costs.
Missing usage is unknown, never zero; do not estimate TTFT from non-streaming
completion time.

Compare candidate minus reference task success, with task-clustered paired
uncertainty intervals that keep repeated trials together. Before collecting
scores, register a five-percentage-point non-inferiority margin per domain
(an engineering acceptance choice, not an upstream benchmark rule). Require
the lower bound of the per-domain 95% interval to be at least -5 points in
every selected domain, complete coverage, and no unexplained transport/parser
errors. An interval crossing the margin is inconclusive, not a pass. A
material regression blocks quality acceptance until explained and fixed.
Any expanded trial plan must be recorded before running, never stop when a
favorable result first appears.

Retain original failed attempts and bounded retry counts. Deadline/budget
abort means incomplete; no success-only denominator, dropped difficult tasks
or unreported reruns. Published vendor scores are context, not our baseline.
The scored three-domain run is separate from the existing GSM8K/MMLU-Pro
smokes and throughput/load testing; none substitutes for another.

## Evidence, safety and remaining work

The committed runners and fake-endpoint tests are implemented; the remaining
acceptance work is measured execution with the required original-revision
deployments and reference. Each run must enforce the frozen target, resource
limits and cleanup, then export
allowlisted per-task/trial numeric outcomes, error classes, metrics and
configuration hashes to `evidence/p2/<date>-tau-validation/` (P5 for the repeat).
Use the standard `run.json`/scrubbed `output.log` evidence envelope and verify
score aggregation against fixtures before trusting it.

Upstream normally writes trajectories containing conversation content. Our
[logging policy](../../AGENTS.md#secrets-and-data) forbids retaining request or
response bodies. Disable raw trajectory and diagnostic-body persistence
before any live run, evaluate in memory, and export only the allowlisted
non-content results. Add canary tests proving no prompts, completions, tool
bodies, keys or private endpoint details enter files or logs. Do not simply
pipe verbose upstream output through a secret scrubber. This restriction
means no claim to a trajectory-verified leaderboard submission.

Only public synthetic benchmark tasks may be used. The simulator and reference
can incur external API charges even when the tested model runs on our GPUs.
Budget GPU allocation/loading/idle time, storage, simulator, evaluator and
reference calls separately; use measured smoke usage and current verified
rates for the full-run estimate. Benchmarks stay outside the guest image and
do not add benchmark dependencies or debug logging to the attested service.

Reviewed original-revision serving/reference identities, matched simulator and
evaluator settings, measured smoke limits, complete scored coverage and separate
run-cost approvals remain prerequisites for the full quality gate. The selected
retail success and IFEval/MMLU-Pro calibrations do not qualify the original
checkpoint, support models or confidential profile, and do not supersede the
preserved failed attempts. Follow
the [run gates](../gates.md); this plan does not rent resources, spend credits,
extend a rental or publish a score.
