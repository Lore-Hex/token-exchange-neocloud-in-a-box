# One original retail task with external support

P2.T4 software preparation only. The explicit `--full-task` mode extends the
[external first-lookup transport](p2-t4-tau-external-simulator.md); the default
lookup mode, selected-owner paths and current live endpoint are unchanged.
It has not run inference. This is one bounded original retail task 2, seed 42,
not the whole benchmark, a reference-checkpoint comparison or quality acceptance.

## Original task and evaluator

The source archive and all task/runtime files remain pinned to tau revision
`fc0055dc4e0a316c3f83133267fbd6faaa770992`. No task instructions, tools,
arguments, simulator history, candidate history or evaluator prompts are replaced.
There is no first-lookup stop. The existing sealed scorer calls
`run_single_task(..., evaluation_type=ALL_WITH_NL_ASSERTIONS, auto_review=False)`.

Primary source at that revision:

- [config.py](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/config.py)
  pins both `DEFAULT_LLM_USER` and `DEFAULT_LLM_NL_ASSERTIONS` to
  `gpt-4.1-2025-04-14`. The separate optional user-review judge uses Claude;
  `auto_review=False` does not invoke that judge or require its credentials.
- [Original retail tasks](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/data/tau2/domains/retail/tasks.json)
  give task 2 one natural-language assertion and reward basis `DB` plus
  `NL_ASSERTION`. Do not substitute the generic task class's default basis.
- [Evaluator aggregation](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator.py)
  multiplies only the original task's applicable reward components.
  [The NL evaluator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_nl_assertions.py)
  renders the original trajectory and assertions. Its JSON answer is validated
  against the exact original assertion before the worker can finish.

The normal original runner, tool-error attribution, assertion hash, terminal
acknowledgement and actual reward are reused without changing the worker bytes.
A completed scored task preserves upstream reward `0.0` or `1.0`. Outer
`result=pass` means execution, protocol and scorer cleanup completed, **not**
that the task earned reward one. Premature termination, budget exhaustion,
truncation, bad evaluator output, missing usage or failed finalization cannot
be promoted to evaluated; those failures keep null reward. A late attach or
container failure preserves an already validated reward while the outer run fails.

## Closed execution limits

- One task/trial, concurrency one, at most 32 steps and 32 total generation
  requests across candidate, simulator and evaluator; no retries. These are
  bounded operational limits, not the upstream default 200-step/10-error limits.
  `max_errors=1`, 900-second trial, at most 960 seconds including preparation and
  the retained 60-second cleanup reserve. One frozen UTC/monotonic deadline.
- All three roles have a 32,768-token context and 2,048-token output ceiling.
  Actual prompt plus reserved output must fit. The candidate requires the
  reviewed `quality32k-eager` service contract from
  [PR402](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/pull/402),
  not the current 8K/256 contract. Merge, source-bound image preparation,
  readiness and conformance remain separate gates before any live use.
- Existing candidate limits still apply: 16 tools, 128 messages and 64 KiB
  encoded request. No truncation, summary, schema repair, tool filtering or
  argument rewrite makes an oversized request pass. The two existing null/name
  projections only remove equivalent upstream aliases. Full-history fit is
  checked per request, not claimed for every possible trajectory.
- The same verified native tokenizer and pinned vLLM rendering count candidate
  requests. Simulator and evaluator use the fixed OpenAI Responses endpoint and
  exact input-token endpoint with identical model/input/truncation fields.
  They share **32 total counting attempts**, including failed attempts.
- No redirects, credential forwarding, SDK retries, fallback models or proxy
  environment. System-root TLS verification precedes credentials. Existing
  120-second whole-request bounds include counting and generation. Scorer
  container networking remains `none`; only the bounded parent owns HTTP.
- Measured integer usage is retained per role before content validation. Lost
  responses stay unknown; no cost or usage is invented from missing replies.
  No prompts, tool bodies, conversations, judge reasoning or keys are recorded.

The simulator and evaluator use the same upstream snapshot, not a newly chosen
replacement. Official [model details](https://developers.openai.com/api/docs/models/gpt-4.1)
and [standard prices](https://developers.openai.com/api/docs/pricing#standard-pricing-data),
checked 2026-10-25, support Responses and $2/M input, $8/M output. The snapshot
has an announced [2027-01-14 shutdown](https://developers.openai.com/api/docs/deprecations#2026-10-14-gpt-4-series-model-deprecations).
Account access and current execution terms still require approval/verification.

Generation reserves use a shared **$3.00** standard-token-price ceiling; the
candidate's required declared prices remain $0.65/M input and $1.45/M output.
The token-count endpoint is not assumed free. Each count conservatively reserves
the model's entire published 1,047,576-token input at $2/M: **$2.095152**.
Thus the total reservation estimate is `32 * 2.095152 + 3 = $70.044864`.
This is not an invoice cap or spending approval; unverified custom fees/taxes,
account terms and counting-endpoint billing remain external constraints.

## Explicit operator handoff

Preparation is offline and accepts the same pinned archive and 74-wheel set:

```sh
python3 -I -S -B scripts/record-tau-external.py prepare --full-task \
  --source-archive "$PINNED_TAU_ARCHIVE" --wheelhouse "$PINNED_WHEELHOUSE" \
  --output "$NEW_PRIVATE_PREPARATION"
```

The private contract uses `nib.tau-external-trial-endpoint/v1`. It has the
lookup contract's exact fields, but `context_tokens=32768`, `output_tokens=2048`,
and prices for all three roles. It additionally requires
`candidate_profile=quality32k-eager` and `evaluator=gpt-4.1-2025-04-14`.
The simulator is the same snapshot. Candidate model is the exact selected
canonical ID; destination remains a single explicit hostname/global IPv4/443.
Conformance digest and retirement cutoff are required operator assertions,
not remote process attestation. No current endpoint is upgraded by this file.

Only after independent review, full gates and separate explicit execution
approval may the operator run:

```sh
python3 -I -S -B scripts/record-tau-external.py run --full-task \
  --payload "$NEW_PRIVATE_PREPARATION/payload" --plan-sha256 "$PLAN_SHA256" \
  --docker-socket "$EXISTING_DOCKER_SOCKET" \
  --contract "$PRIVATE_CONTRACT" --contract-sha256 "$CONTRACT_SHA256" \
  --candidate-key "$PRIVATE_CANDIDATE_KEY" --simulator-key "$PRIVATE_OPENAI_KEY" \
  --vllm-source "$PINNED_VLLM_SOURCE" --tokenizer "$PINNED_TOKENIZER_FILES" \
  --tokenizer-wheel "$PINNED_NATIVE_WHEEL" --deadline-utc "$FIXED_UTC_CUTOFF" \
  --output "$NEW_PRIVATE_RESULT"
```

The key is supplied once for the fixed simulator/evaluator destination. Keep
inputs private using the existing owned-directory/0600-file checks. A full-task
payload without explicit `run --full-task`, or a lookup payload with that flag,
fails before Docker commands or credential reads. Source closure is recomputed
from the exact clean committed producer; do not reuse a stale lookup payload.

The new content-free outer report is `nib.tau-external-full-task-run/v1` and
contains the `trial` execution report, selection, evaluation mode, actual
role usage, source/plan/endpoint joins and cleanup. All full-benchmark,
quality-eligibility, confidential and live-admission flags remain false.

## Verification and remaining gates

Focused tests cover all-role counting/accounting, original history projection,
context/body/tool limits, old-contract rejection before keys, full-task opt-in,
source closure, real private-pipe acknowledgement/reaping, zero/one rewards,
missing or malformed evaluation, late failure and unchanged lookup tests.
The optional offline source audit executes hash-checked upstream aggregation
and NL rendering with synthetic components, not models:

```sh
python3 -B scripts/test_tau_external_trial.py \
  --upstream-root "$VERIFIED_SCORER_PAYLOAD"
```

The full local/hosted suites, final independent review, deployment contract,
real API/tokenizer compatibility, approved credentials and budget, and an
original completed trial receipt are distinct remaining gates. A single
task's reward cannot establish selected-checkpoint aggregate quality or the
original model-revision reference gate.
