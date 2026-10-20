# Offline tau manifest foundation

The separate [content-free aggregator](p2-t3-tau-aggregation.md) consumes a frozen
manifest and allowlisted trial records. It does not implement the live adapter
or turn this foundation into a measured benchmark.

Status: software-only preflight and budget primitives. **No benchmark execution,
score, endpoint qualification or live tau adapter.** This is preparation for the
[evaluation plan](../benchmarks/tau-validation.md), not satisfaction of P2.T3.
The CLI has no network client, endpoint/key arguments or live execution mode.

## Immutable inputs

`bench/tau-upstream-pins.json` records Git blob IDs, SHA-256 and sizes for 59
public inputs (25,779,850 bytes) at
[tau2 v1.0.1 / fc0055dc](https://github.com/sierra-research/tau2-bench/tree/fc0055dc4e0a316c3f83133267fbd6faaa770992).
The set includes the MIT license, dependency lock, three domains' tasks, splits,
policies and databases, simulator guidance, evaluator sources and selected
runner/transport code. No upstream code or task bodies are vendored or executed.
This is a selected source/data audit, **not a complete runtime dependency closure
or proof of installed package integrity**.

The source files yield 50 airline, 114 retail and 114 telecom `base` tasks.
Task IDs are sorted lexicographically before selection, including the structured
telecom IDs. Smoke selects the first five per domain: 15 trials per target.
Comparison selects all 278 tasks with four trials: 1,112 trials per target,
2,224 across candidate/reference. Every target uses the same ordered IDs and
trial seeds; no result-driven selection is implemented. Trial seeds use the
pinned [batch runner's seeded randint sequence](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/runner/batch.py).

The manifest binds the toolkit commit and relevant toolkit bytes, upstream
inputs, `uv.lock`, model pack and all-file weight manifest. Candidate/reference
must declare the same DS1017 weight revision and the pack's exact tokenizer
and tokenizer-config hashes. Image, overlay, effective serving arguments,
precision and chat-template hashes remain operator declarations, not measured
runtime provenance. No template, simulator revision, commercial price or
reference identity is invented to fill a missing field.

## Local preflight

Use a local copy of the pinned public inputs; unrelated files are not imported
or inspected. Input paths must be absolute with real directory ancestors and
single-link regular files. Symlinks, FIFOs, oversized files, hash mismatches and
changed file identity reject before publication.

```sh
python3 -I -S -B scripts/tau_preflight.py \
  --upstream "$TAU_PINNED_SOURCE_DIR" \
  --settings "$TAU_PUBLIC_SETTINGS_JSON" \
  --output "$NEW_TAU_MANIFEST_DIR"
```

Run from a committed source checkout. The code, pin catalog and model-pack files
must match that commit; Git replacement objects, lazy fetching, transports and
ambient Git configuration are disabled. This binding assumes the local OS,
interpreter and Git executable are trusted; it is not remote attestation.

The settings JSON has exactly these top-level keys:

| Key | Contract |
| --- | --- |
| `schema` | `nib.tau-preflight-input/v1` |
| `stage` | `smoke` or `comparison` |
| `seed` | Unsigned 32-bit integer |
| `roles` | Exactly `candidate`, `reference`, `simulator`, `evaluator` |
| `serving` | Exactly `candidate`, `reference` |
| `limits` | Explicit finite limits listed below |

Each role has `model`, `revision`, `generation`, `settings_sha256`,
`input_usd_per_million`, `output_usd_per_million`, `rate_source_sha256`.
Model/revision strings are public identifiers, never credentials or URLs.
Simulator/evaluator cannot silently use the tested model. `generation` has
`temperature` (decimal string, 0 through 2), `max_tokens` (1 through 8,192) and
`num_retries` (integer zero). `settings_sha256` hashes the UTF-8 canonical JSON
of `generation`: sorted keys, compact separators, trailing LF. Temperature's
decimal-string representation is manifest metadata, not a guessed LiteLLM
argument conversion. Live translation is not implemented.
Candidate and reference must have identical `generation` objects and settings
hashes; independently valid but different sampling/output limits reject.

Each serving row has `image_sha256`, `overlay_sha256`, `arguments_sha256`,
`tokenizer_sha256`, `tokenizer_config_sha256`, `chat_template_sha256` and
`precision_sha256`. All hash fields require nonzero lowercase SHA-256.
The two price fields and `cost_usd`/`resource_cost_usd` use nonnegative decimal
strings, at most nine fractional digits; never binary floats. Rate-source
hashes are declarations requiring separate verification and spending approval.
Synthetic test prices are fixtures, not commercial rates.

Limits are `concurrency=1`, `retries=0`, `turns` (1-100 model requests per trial),
`requests` (1-100,000 total), `request_seconds` (1-120), `trial_seconds` (1-1,800),
`run_seconds` (1-86,400), `input_tokens_per_request` (1-131,072),
`output_tokens_per_request` (1-8,192), `total_tokens` (1-100,000,000),
`cost_usd` (positive, at most 100,000) and `resource_cost_usd` (less than the
dollar ceiling). Request <= trial <= run deadlines. All roles share the budget.
The `turns` bound is a conservative request counter, not an implementation of
upstream's tool/agent `max_steps` semantics.

The preflight has a shared 120-second monotonic check deadline, bounded local
reads (16 MiB/file, 32 MiB total) and a 1 MiB output limit. It exclusively creates
a private 0700 directory and 0600 `manifest.json`, syncing file and directories.
Existing output is never changed or removed. A publication failure may leave
an incomplete attempt directory; preserve it and use a new output name.
Only a fixed summary and manifest hash go to stdout. Failures emit fixed text,
not exceptions, paths or input values.

## Budget and retention boundary

`Budget` is a transport-boundary primitive tested with synthetic endpoint
responses, not an upstream runner. It reserves worst-case token/cost before a
request, uses exact decimal arithmetic, includes predeclared resource cost,
and enforces one outstanding request and zero automatic retries. It fixes the
trial deadline internally, caps request timeout by the remaining trial/run
time, and checks usage against the reservation. Missing/malformed usage,
cancellation or an expired response makes failure sticky and retains the full
reservation. Unknown cost is not converted into a free request.

The primitive does **not** execute or interrupt a transport, meter GPU idle
time, validate token counts independently or supervise upstream workers.
A future live adapter must enforce the returned timeout at the transport and
process boundaries, prevent over-cap input dispatch, account for all roles and
resource charges, and terminate/clean up on cancellation. Merely passing a
timeout value to an arbitrary callback is not sufficient.

Only task IDs, seeds, public settings, hashes and bounded numeric observations
are retained. Task bodies remain in memory during parsing; no prompts,
completions, tool arguments/results, trajectories or endpoint details are
included in the manifest. Input settings must themselves be public; the tool
does not certify arbitrary operator-provided strings as nonsecret.

## Upstream integration findings

The pinned [config model](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/data_model/simulation.py)
and `run_single_task(config, task, ...)` signature are inspected as AST, never
imported. `run_single_task` with no save directory is a possible future
integration point, **not proof that all dependencies are content-safe**:

- `run_domain` selects a results path and persists full trajectories.
- The [LLM utility](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py)
  configures clients/caches at import, can log exception/message contents,
  supplies default retries, and returns zero on cost-calculation failure.
- Disabling per-task files does not disable every logger, client callback,
  cache or content-bearing diagnostic. No upstream function is executed by
  this foundation, and the budget primitive does not repair those defaults.

Remaining implementation: source-reviewed in-memory evaluation and transport
adapter, content-free logger/client isolation, cancellation/cleanup,
allowlisted trial outcomes, authoritative evidence envelopes, and fixture
verification of Pass^1/Pass^4 and paired uncertainty. Dependency installation,
real endpoints, rates/approvals, scored runs and confidentiality remain gated.

## Software checks

```sh
python3 -m unittest discover -s scripts -p test_tau_preflight.py
python3 -I -S -B scripts/test_tau_preflight.py --public-sources "$TAU_PINNED_SOURCE_DIR"
```

The second command rehashes the 59 public inputs, inspects API shape and
asserts the task counts without importing upstream. Its settings/prices are
explicit synthetic fixtures. Neither command constitutes a benchmark run.
