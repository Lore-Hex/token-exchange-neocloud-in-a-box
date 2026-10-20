# Offline tau result aggregation

Status: **implemented and tested with synthetic records only**. No evaluator,
model, endpoint, paid API or benchmark is executed by this tool. It does not
satisfy [P2.T3 quality acceptance](../benchmarks/tau-validation.md).

## Frozen inputs and identity

`scripts/tau_aggregate.py` consumes the canonical JSON produced by the
[preflight foundation](p2-t3-tau-preflight.md), its separately retained SHA-256,
and one strict trial-report file for each of `candidate` and `reference`.
The caller supplies the previously frozen manifest digest, not a newly accepted
digest for modified settings. The tool verifies that seal, the pinned upstream
catalog, model/settings constraints, domain counts and seeded trial matrix.
It never substitutes a smaller observed denominator or an unpaired reference.

Report identity, serving configuration and receipts remain **declared,
unverified provenance**: a hash string does not authenticate a remote endpoint,
prove its model revision or establish that an evaluator actually ran. The
manifest's task IDs are trusted through the supplied frozen seal; aggregation
does not reread the public task bodies. A future live adapter must establish
those bindings before any quality claim. Missing identity/receipt fields,
unobserved trials or unevaluated outcomes make the summary incomplete and
suppress every paired interval, even for otherwise complete domains.

```sh
python3 -I -S -B scripts/tau_aggregate.py \
  --manifest "$FROZEN_MANIFEST" \
  --manifest-sha256 "$FROZEN_MANIFEST_SHA256" \
  --candidate "$CANDIDATE_TRIALS" \
  --reference "$REFERENCE_TRIALS" \
  --output "$NEW_PRIVATE_SUMMARY_DIR"
```

All paths must be absolute, with no symlink ancestors. Input reads are bounded,
nonblocking and no-follow; directories, FIFOs, hardlinks, changed files and
oversized inputs reject. The manifest is at most 1 MiB, each report 2 MiB, and
the shared operation deadline is 60 seconds. JSON rejects duplicate keys and
nonfinite numbers. No endpoints or credentials belong in these files.

The helper and entrypoint must equal the committed source bytes. The helper
loads directly from verified source, not cached bytecode. Git object reads
disable replacement objects, lazy fetching, protocols and prompting. A new
0700 output directory contains a 0600 `summary.json`, synchronized without
overwriting a previous attempt. Errors print fixed text, never input paths,
values or tracebacks. An I/O failure may leave a partial attempt; retain it and
use a new directory rather than overwrite it.

Exit 0 means only `complete-declared-inputs`; exit 1 means an incomplete
summary or rejected operation. Every summary retains `quality_accepted:false`,
`confidential:false`, `provenance_verified:false`, and
`benchmark_executed_by_aggregator:false`. Synthetic fixtures remain explicitly
labelled `synthetic-fixture`. Do not present complete fixture arithmetic as an
observed benchmark.

## Strict report contract

The exact report keys are:

| Field | Meaning |
| --- | --- |
| `schema` | `nib.tau-trials/v1` |
| `manifest_sha256` | Frozen manifest digest |
| `role` | Exactly `candidate` or `reference`, matching the input slot |
| `kind` | `synthetic-fixture` or `declared-unverified`; both reports must agree |
| `producer` | `git_sha`, `adapter_sha256`, `runtime_receipt_sha256` |
| `binding` | `model_revision`, `generation_sha256`, `serving_sha256`, `simulator_sha256`, `evaluator_sha256` |
| `trials` | At most the frozen number of unique task/trial records |

The producer commit and adapter digest must agree across roles. Runtime
receipts may differ. Producer fields may be null only to report incompleteness.
The same applies to binding fields; a present but conflicting binding rejects.
Serving/simulator/evaluator digests use the preflight canonical JSON encoding:
sorted keys, compact separators, UTF-8, one trailing newline. They cover the
corresponding complete frozen settings objects. Generation digest and model
revision must exactly match the role. No implicit defaults or endpoint names
are accepted.

Each trial has exactly:

| Field | Meaning |
| --- | --- |
| `domain`, `task_id` | Member of the frozen domain/task matrix |
| `trial`, `seed` | Zero-based trial index and its exact frozen seed |
| `status` | `evaluated`, `infrastructure_error`, `timeout`, `truncated`, `protocol_error`, `budget_abort`, or `cancelled` |
| `reward` | Finite bounded decimal string for `evaluated`; null otherwise |
| `elapsed_ms` | Integer elapsed time for the whole trial, including simulator/evaluator work |
| `requests` | Integer count across all model roles in that trial |
| `usage` | Null, or exactly `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd` |

`usage` is the complete aggregate across candidate/reference agent,
simulator and evaluator calls for that trial, not agent usage alone. Token
counts must be nonnegative integers with a consistent sum; cost is a
nonnegative decimal string, not a float. If any constituent usage or cost is
unknown, report null, never an invented zero. Observed request/token/cost and
trial-duration overruns block completeness. Summed serial trial durations
provide a lower bound on run duration; unobserved between-trial wall time is
not measured. These are retrospective checks, **not live budget enforcement**.
Declared resource cost is included once and is not an independently measured
invoice. TTFT, TPOT, cached and reasoning-token breakdowns remain unavailable;
do not infer them from aggregate counts.

Raw prompts, answers, tool arguments/results, headers, URLs, errors,
trajectories and arbitrary extension fields reject. Duplicate task/trial keys,
unknown IDs, wrong seeds and extra trials reject rather than replace records.

## Metric and missingness

The immutable
[upstream metrics source](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/metrics/agent_metrics.py)
has SHA-256
`136b5ebcc0c115b5817a4f6d6b485a871aba199ddedf56458f35f339569a54dc`.
Its `is_successful` predicate accepts rewards within `1 +/- 1e-6` using Python
float comparison. Its `pass_hat_k` is `C(successes,k)/C(trials,k)`, averaged
equally across tasks. Our original standard-library arithmetic matches those
two functions; it does not copy or replace the task evaluator. Pass^4 is
repeated reliability, not best-of-four success.

The upstream dataframe wrapper filters infrastructure errors and lowers `k`
when fewer trials are observed. We deliberately do **not** use that wrapper:
the frozen protocol requires all tasks/trials and explicit failure accounting.
The one-trial smoke has Pass^1 only and Pass^4 null; comparison has four trials
for every task. Evaluated reward failures count as observed zero successes.
Infrastructure errors, cancellation, truncation and absent records remain in
the expected denominator and suppress `upstream_formula_pass` for that role's
incomplete domain. Counts include expected/evaluated tasks, every outcome
class, missing trials and unknown usage.

`frozen_denominator_bounds` treat each ungraded outcome as failure for the
lower bound and success for the upper bound. These are conservative
missingness bounds, **not an upstream score**. All domains remain present.
There is no overall blended score that could conceal one incomplete domain.

## Paired task-cluster uncertainty

Only complete evaluated coverage, complete declared bindings, compatible
producers and known within-limit usage for both roles allow paired intervals.
For each domain and available `k`, resample whole matched tasks with replacement,
keeping all trials and both roles of a task together. Compute candidate minus
reference domain means for 10,000 resamples, with a deterministic seed derived
from the frozen manifest and domain. Use linear 2.5th/97.5th percentiles.
The summary records method, seed, resample count, cluster count, Python version,
RNG and a degenerate-distribution flag.

This is an engineering paired percentile bootstrap, not an upstream tau
metric or evaluator. The
[SciPy bootstrap documentation](https://docs.scipy.org/doc/scipy-1.15.3/reference/generated/scipy.stats.bootstrap.html)
describes paired common-index resampling and percentile intervals; SciPy is
not imported or required here. Intervals are approximate, domain-specific and
not simultaneous multiple-comparison bounds. A five-task smoke or zero-width
interval cannot establish equivalence, represent the full task population or
pass the plan's non-inferiority gate. `acceptance_assessed` is always false.

## Verification and remaining work

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
python3 -I -S -B scripts/test_tau_aggregate.py \
  --public-metrics "$TAU_PINNED_SOURCE_DIR/src/tau2/metrics/agent_metrics.py"
```

The optional audit verifies the source hash and compiles only the two reviewed
pure arithmetic functions for fixture comparison. It imports no upstream
package and runs no evaluator, task, simulator or endpoint. Tests cover every
missing/failure class, reference identity and setting mismatches, pairing,
strict retention, deadlines, real private file publication, no-overwrite,
unsafe files and committed-source tamper.

A live adapter, authenticated runtime/reference provenance, content-free
in-memory grading, transport budget enforcement, approved resources and
source-bound measured evidence remain separate work. This PR contains no
scored `evidence/` record and grants no model-quality or confidential status.
