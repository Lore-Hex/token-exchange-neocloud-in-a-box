# Staged External Tau Selection

**Software preflight and fake-tested dispatch only. No live run, new spending,
hardware mutation or phase completion is authorized by this change.** The
[external runner](../../scripts/record-tau-external.py) can bind one original
task/trial from a predeclared plan to the existing pinned worker, transport,
usage ledger and scorer. The old lookup default and `--full-task` retail task 2,
trial 0, seed 42 remain unchanged, including their input and result formats.

## Selection and Compatibility

The source remains `sierra-research/tau2-bench` revision
`fc0055dc4e0a316c3f83133267fbd6faaa770992`, verified against the existing archive,
runtime catalog and per-file SHA-256, Git blob and byte pins. Planning parses
data only; upstream code is not imported or executed on the host. The producer
must be a committed checkout. Preparation requires the same producer SHA.

Declare a nonempty subset of `airline retail telecom` in that canonical order,
one to four trials per task, and a seed from 0 through 1,000,000. Every selected
domain retains exactly the first five lexicographically sorted `base` task IDs,
using the existing task parser. Seeds follow the existing `random.Random(seed)`
trial-seed policy. Order is domain, task, trial. No hand-picked IDs, replacement
tasks, reference role, auto-resume or automatic retries exist. The separately
versioned sequential controller below never changes this frozen order.

The staged v2 worker supports zero through three natural-language assertions,
the complete range in this pinned first-five-per-domain selection. Airline has
counts `1, 1, 1, 3, 2`; retail and telecom each have five zero-assertion tasks.
The original upstream evaluator skips its LLM judge for zero assertions and
retains the task's other reward checks. Multiple assertions use the original
single judge call, with every ordered result required. No assertion, reward
basis or prompt is added or removed. The original retail-task-2 and CPU/GPU-owner
paths keep their one-assertion guard.

More than three, malformed or oversized assertion shapes remain
`unsupported-assertion-shape`; the plan retains their rows and reserves their
costs. They cannot be prepared for execution. Other supported-shape rows may be prepared
even when the overall plan records unsupported rows; this is not full staged
coverage. A shape check is not a claim that a domain or trajectory will work.
Original unsupported tools, trajectories, token overflow, upstream exceptions
and scored failures remain failures in the existing content-free receipts.
Do not repair prompts, swap tasks, rewrite tool results, or retry failures.

The plan, input, transport and outer result use v2 schemas so an old plan cannot
silently gain broader support. Both parent and worker require the actual pinned
task's assertion count to match the source-bound plan. Results include the
non-content assertion count; a zero-assertion result requires zero judge calls,
not invented judge usage. Existing usage, original task reward and lifecycle
checks remain mandatory. Assertions are bounded to 16,384 UTF-8 bytes each and
65,536 serialized bytes in total. This is a smoke-selection boundary, not
unlimited evaluator support.

The original grading behavior is verified from the authenticated
[pinned evaluator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_nl_assertions.py)
without inference:

```sh
python3 scripts/test_tau_staged_assertions.py --source-archive "$SOURCE_ARCHIVE"
```

This audit checks all 15 selected task shapes and exercises original zero and
multiple-assertion reward logic with synthetic judge results. It is not a
measured task score, benchmark execution or spending approval.

## Aggregate Reservation

The plan requires all four decimal USD inputs: weekly cap (at most $10,000), GPU
commitment, CPU/disk reserve, and other commitments. None defaults to zero.
Inputs are operator-declared commitments, not an independently audited bill.
Existing and previously attempted runs must be included in other commitments.

**Conservative estimate:** every trial reserves 32 possible counting calls at
$2.095152 each plus the existing $3.00 shared generation ceiling:

`32 * 2.095152 + 3.00 = $70.044864 per trial`.

The token-counting documentation does not establish that counting is free.
This increment does not change counting reservations, the 32 shared generation
request cap, 32-turn cap, 32,768 input / 2,048 output limits, 1,114,112 total
tokens, 120-second request deadline, 900-second trial deadline, original
960-second overall cutoff, C1 execution, or zero retries.

With a historical, illustrative **$9,176 GPU allocation**, five single-domain trials reserve
$350.224320, leaving at most $473.775680 for CPU/disk and other commitments.
For an explicit example with $100 CPU/disk and $50 other commitments, the total
is $9,676.224320 and the remaining estimate is $323.775680. These overhead
numbers are illustrative inputs, not approved or measured charges.

All three domains at one trial each require 15 trials and $1,050.672960. That is
$10,226.672960 including GPU alone, already $226.672960 over the weekly cap
before CPU/disk. The plan must fail; it never silently drops tasks, assumes free
counting, or raises the cap. Four trials across all domains reserve $4,202.691840
before hardware and other commitments and cannot fit that illustrative allocation.

This is not a global financial ledger or an execution permit. Separate plans
or manual repeated invocations do not share an automatic spend counter. Before
any approved execution, the operator must reconcile all prior reservations,
unknown usage and resources within the same weekly budget, approve the exact
plan hash and index once, retain each failed attempt, and avoid duplicate runs.
Do not run the illustrative budget example as an approval workaround.

## Sequential Cap

The optional `plan --aggregate-cap-usd` creates
`nib.tau-external-sequential-plan/v1`, not a reinterpretation of a legacy v2
full-sum plan. It supports retail and telecom only, keeps all five tasks and
one to four repetitions per task, and requires a cap from $70.044864 to $10,000
within the same declared weekly commitments. The plan retains both its full
planned worst case and the sequential cap. Neither is a verified invoice or
permission to spend. Legacy plan bytes and single-trial defaults are unchanged.

The [sequential controller](../../scripts/record-tau-sequential.py) authenticates
the pinned source and producer before opening an exclusive journal or reading
credentials. It durably reserves **the full $70.044864 before each trial's
preparation and dispatch**, not an expected average. Only the original owned
receipt, source/selection/payload/endpoint binding, complete known usage,
matched count/usage pairs, original scoring termination and confirmed cleanup
can settle that reservation. Every attempted count still retains $2.095152;
generation uses the existing standard-rate known-usage accounting, not a
claim about the provider's invoice. Unknown or cancelled billing, malformed
receipts, cleanup failures and publication failures keep the full pending hold
and stop. There is no automatic refund, resume, retry or replacement task.

A completed known receipt releases only the unused part of its reservation.
The next trial still requires another complete $70.044864. Otherwise the
controller records `incomplete` / `cap-exhausted`, retaining all planned rows,
the completed count and every original receipt. A score of zero is retained as
zero, not retried. The journal's hash-linked events and original receipts use
exclusive files, fsync and private permissions. Partial failed writes are
preserved; an interrupted journal is not automatically recoverable or proof of
remaining credit. Separate journals do not coordinate the weekly budget.

The caller supplies one absolute UTC deadline, converted once to a monotonic
deadline, at most 24 hours. Every trial retains the original 960-second total
bound and cleanup requirements; preparation has a 600-second bound and the
controller keeps 30 seconds for terminal publication. It stops before starting
a trial without this entire time allowance. A sequential payload cannot run
through the single-trial entry point without its owning reservation journal.

For an independently reviewed later run, pass the canonical selection-plan
path/hash, pinned source archive, wheelhouse, Docker socket, existing private
endpoint contract/hash, candidate/simulator key files, vLLM source, tokenizer
directory/wheel, absolute UTC deadline and a **new** output directory to
`python3 -I -S -B scripts/record-tau-sequential.py`. The argument names match
the existing full-task procedure; the controller prepares each payload itself.
All endpoint, source, tokenizer, budget, observer-coordination and execution
gates remain. This change does not perform paid requests or mutate serving.

The explicit [offline replay](../../scripts/record-tau-sequential-offline.py)
freezes 15 original telecom trials under a synthetic one-trial-size cap,
runs the original network-disabled worker with fixed peers, then checks that
the controller retains the other 14 planned rows without dispatch. Credential
reads and HTTPS constructors are blocked. Synthetic constant counts, tool round
trips and scored zero test integration only, not measured parity or quality:

```sh
python3 -I -S -B scripts/record-tau-sequential-offline.py \
  --source-archive "$SOURCE_ARCHIVE" --wheelhouse "$WHEELHOUSE" \
  --docker-socket "$DOCKER_SOCKET" --output "$NEW_PRIVATE_JOURNAL" \
  --receipt "$NEW_PRIVATE_RECEIPT"
```

## Offline Procedure

Use a new private output directory, the retained source archive and a clean
committed checkout. Supply independently reviewed budget values:

```sh
python3 -I -S -B scripts/record-tau-external.py plan \
  --source-archive "$SOURCE_ARCHIVE" --output "$NEW_PRIVATE_PLAN_DIRECTORY" \
  --domains retail --trials 1 --seed 42 \
  --weekly-cap-usd "$WEEKLY_CAP" --gpu-committed-usd "$GPU_COMMITTED" \
  --cpu-disk-reserved-usd "$CPU_DISK_RESERVED" --other-committed-usd "$OTHER_COMMITTED"
```

`output.log` is the canonical, content-free selection plan; stdout reports its
SHA-256. `run.json` is the generic `nib.evidence/v1` envelope. New files are
exclusive, mode 0600 in a mode 0700 directory, fsynced and never overwritten.
An unaffordable or unsupported plan is still written and exits nonzero. A
malformed/unverifiable source fails without executing upstream or contacting
an endpoint. Preserve failed plans and partial files; create a new directory
for an explicitly reviewed correction.

Preparation is also offline and requires explicit staged flags:

```sh
python3 -I -S -B scripts/record-tau-external.py prepare --full-task \
  --source-archive "$SOURCE_ARCHIVE" --wheelhouse "$WHEELHOUSE" \
  --selection-plan "$NEW_PRIVATE_PLAN_DIRECTORY/output.log" \
  --selection-plan-sha256 "$SELECTION_PLAN_SHA256" --selection-index "$INDEX" \
  --output "$NEW_PRIVATE_PAYLOAD_DIRECTORY"
```

Preparation embeds the entire plan and selected index into the manifest. Both
the parent and isolated worker rederive selection from pinned source data and
validate the whole declared budget. The returned `plan_sha256` is the prepared
payload hash, distinct from `selection_plan_sha256`.

## Later Execution Gate

No live command in this procedure has been run by this change. After separate
run approval, use the existing [full-task procedure](p2-t4-tau-external-full-task.md)
with `run --full-task` and additionally require
`--selection-plan-sha256 "$SELECTION_PLAN_SHA256" --selection-index "$INDEX"`.
All existing private endpoint/key/tokenizer/source arguments, prepared-payload
hash, finite UTC deadline, lease, TLS and cleanup requirements remain mandatory.
Staged payloads cannot run through the legacy flags, or with a different plan
hash/index. Mismatch fails before credential reads or Docker operations.

Each invocation still runs exactly one trial. Receipts use explicit staged
schemas and retain domain, original task ID, trial, seed and plan hash/index.
No raw prompts, completions, tools, exceptions, keys, operator identities or
private endpoint coordinates are added. The retained worker and endpoint
usage checks are reused, not weakened. No server-ownership or remote-cancellation
proof is inferred from client cleanup.

Coordinate any later approval with other scheduled requests; this software
does not inspect, change, stop or reschedule another observer. C1 describes
this trial, not global exclusivity on a shared service.

The selected abliterated model is not the unmodified DeepSeek acceptance target.
The [matched-reference quality gate](../benchmarks/tau-validation.md), all base
tasks across all domains with four trials, unmodified weights, full uncertainty
analysis and confidential qualification remain unproven. A lifecycle pass,
supported shape, five-task stage or scored zero does not complete P2.T3.
