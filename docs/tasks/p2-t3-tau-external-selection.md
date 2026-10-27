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
tasks, reference role, auto-resume, batch executor or automatic retries exist.

The retained selected worker requires exactly one natural-language assertion.
The plan records zero, multiple or malformed assertion shapes as
`unsupported-assertion-shape`, retains their rows and reserves their costs. They
cannot be prepared for execution. Other supported-shape rows may be prepared
even when the overall plan records unsupported rows; this is not full staged
coverage. A shape check is not a claim that a domain or trajectory will work.
Original unsupported tools, trajectories, token overflow, upstream exceptions
and scored failures remain failures in the existing content-free receipts.
Do not repair prompts, swap tasks, rewrite tool results, or retry failures.

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

With the current **$9,176 GPU allocation**, five single-domain trials reserve
$350.224320, leaving at most $473.775680 for CPU/disk and other commitments.
For an explicit example with $100 CPU/disk and $50 other commitments, the total
is $9,676.224320 and the remaining estimate is $323.775680. These overhead
numbers are illustrative inputs, not approved or measured charges.

All three domains at one trial each require 15 trials and $1,050.672960. That is
$10,226.672960 including GPU alone, already $226.672960 over the weekly cap
before CPU/disk. The plan must fail; it never silently drops tasks, assumes free
counting, or raises the cap. Four trials across all domains reserve $4,202.691840
before hardware and other commitments and cannot fit the current allocation.

This is not a global financial ledger or an execution permit. Separate plans
or manual repeated invocations do not share an automatic spend counter. Before
any approved execution, the operator must reconcile all prior reservations,
unknown usage and resources within the same weekly budget, approve the exact
plan hash and index once, retain each failed attempt, and avoid duplicate runs.
Do not run the illustrative budget example as an approval workaround.

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
