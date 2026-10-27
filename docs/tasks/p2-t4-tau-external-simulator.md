# P2.T4: Bounded External Simulator Lookup

Status: software implementation with offline/fake tests only. No authenticated
API request, simulator qualification, customer-resolution success, full tau
score, reference comparison or confidential result is claimed. Live execution
requires independent final-head review, complete gates and separate approval.
The existing selected/local modes and live public owner are unchanged.

## Closed Scope

`scripts/record-tau-external.py` reuses the source-verified tau preparer, sealed
network-none scorer, parent pipe, counted session, sticky budget and
[natural first-lookup diagnostic](p2-t4-selected-customer-resolution.md).
It runs original retail task 2, seed 42, one trial, at most 32 steps/requests,
C1, zero retries, 256 output tokens, 120 seconds per request and 900 trial
seconds. One original absolute UTC cutoff admits at most 960 seconds including
preparation and 60 seconds reserved for scorer cleanup. No candidate or
simulator owner is created, stopped, restarted or relabelled.

The candidate is the canonical selected Dealign model through ordinary
authenticated system-roots HTTPS on port 443. The private operator contract
freezes hostname, public IPv4, model, 8192 context, 256 output, declared price
ceilings, expiry and conformance-receipt hash. It is an operator assertion,
not remote process/weights attestation. The simulator is fixed to
`gpt-4.1-2025-04-14` at `api.openai.com`, not an arbitrary model/provider URL.
No evaluator endpoint is created. The only successful result is the original
first lookup resolving the intended customer; reward remains null.

Primary sources, checked 2026-10-25:

- Pinned tau2 [config.py](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/config.py#L17-L24)
  selects that exact user-simulator snapshot. Original prompts, tool schemas,
  arguments, task/database and orchestrator remain source-bound and unmodified.
- [OpenAI model](https://developers.openai.com/api/docs/models/gpt-4.1)
  documents Responses support, non-reasoning behavior and $2/$8 per million
  standard input/output tokens. It is deprecated; the
  [announced shutdown](https://developers.openai.com/api/docs/deprecations#2026-10-14-gpt-4-series-model-deprecations)
  is January 14, 2027. Account access is untested. Any access/rate/model error
  ends this attempt without a replacement model or retry.
- [Responses input counting](https://developers.openai.com/api/docs/guides/token-counting)
  includes model formatting. Its exact input-semantic fields are shared with
  generation; no conversation IDs, history truncation or implicit tools.
  `store=false`, `background=false`, `stream=false` and standard service tier
  are explicit. This is not a zero-data-retention claim.

## Compatibility and Accounting

The candidate gate preserves the current guard's 16-tool, 64-KiB, 128-message,
8192-context and 256-output limits. Tools/arguments are never reduced or
repaired to fit. Two equivalent projections accommodate pinned tau's
[serializer](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py#L168-L199):
omit null `tool_calls`; remove a redundant call-level name only when exactly
equal to `function.name`. Tool argument strings and schemas remain unchanged.
Unsupported controls, histories or over-cap requests fail before their
generation dispatch. This does not pre-claim that every later dialogue fits.

The candidate counter verifies pinned V4.1 renderer/wrapper source, tokenizer
bytes and a separately supplied hash-pinned tokenizers 0.23.3 wheel. It loads
only that native extension, not an ambient Transformers/vLLM installation.
It reproduces the pinned tool model-dump defaults/order for counting, not for
wire mutation. All four existing runtime-parity fixtures must reproduce their
exact token-ID hashes offline before any request. The per-response prompt count
must also match the pre-count. This is not new live or arbitrary-prompt parity
evidence. Exact pins and platform wheel hashes are in
`scripts/tau_external_tokenizer.py`; primary renderer is
[vLLM 5df63a7](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py).

Before each simulator generation, at most one authenticated, non-generating
input-count request verifies input plus the 256-output reservation fits 8192.
That call is reported separately and is not an inference retry. Generation
reservations use actual declared rates, no cached-input discounts, and a
**$1 generation ceiling**. Counting is not assumed free: conservatively reserve
the entire published 1,047,576-token model context at $2/M for each of at most
32 count calls. The combined standard-token-price reservation ceiling is
**$68.044864**, an intentionally conservative estimate, not an invoice or a
claim that counting is charged. Separate execution approval must cover this
ceiling and confirm the account's current terms; no subscription, custom-tier,
tax or unrelated account cost is controlled by this adapter.

Actual integer usage is retained even if subsequent finish/content/count
validation fails. Lost responses remain unknown, never zero. Native length and
ambiguous candidate tool-at-cap failures retain the existing fail-closed guard.
No prompt, output, tool argument, API error body, key, address or hostname enters
the content-free observation. Synthetic task data necessarily leaves the
network-isolated scorer through this explicit parent transport and reaches
both authorized providers; no privacy qualification is implied.

## Operator Handoff

Prepare a fresh payload from the reviewed clean source, using the existing
exact pinned tau archive and Linux/amd64 wheelhouse. This command is offline:

```sh
python3 -I -S -B scripts/record-tau-external.py prepare \
  --source-archive "$TAU_ARCHIVE" --wheelhouse "$TAU_WHEELS" \
  --output "$NEW_PRIVATE_PREPARATION"
```

The printed plan hash binds `payload/trial.json`. Keep the preparation root
private (0700); do not reuse an earlier selected/CPU plan. The parent runtime
needs a cached, already admitted pinned Python scorer image and an approved
local Docker socket. It never pulls images, installs packages, invokes a GPU
runtime, or mounts credentials into the scorer. Do not use a daemon concurrently
owned by another strict-inventory experiment without separate approval.

Create the private endpoint contract and two key files under owned 0700
directories, files 0600, no symlinks/hardlinks. Contract keys are exactly
`schema`, `hostname`, `address`, `port`, `model`, `context_tokens`,
`output_tokens`, `prices`, `simulator`, `retire_at`, `conformance_sha256`.
Schema is `nib.tau-external-endpoint/v1`; prices are
`{"candidate":["0.65","1.45"],"simulator":["2.00","8.00"]}`.
`retire_at` is a UTC Unix-second expiry later than the diagnostic cutoff.
Keys are read only in the parent; they are never inherited as environment
variables or arguments by Docker/scorer processes. Operator contract approval
must independently establish its endpoint, prices, source and current readiness.

Only after all gates and one explicit execution authorization:

```sh
python3 -I -S -B scripts/record-tau-external.py run \
  --payload "$NEW_PRIVATE_PREPARATION/payload" --plan-sha256 "$PLAN_SHA" \
  --docker-socket "$APPROVED_SOCKET" --contract "$PRIVATE_CONTRACT" \
  --contract-sha256 "$CONTRACT_SHA" --candidate-key "$PRIVATE_CANDIDATE_KEY" \
  --simulator-key "$PRIVATE_SIMULATOR_KEY" --vllm-source "$PINNED_VLLM_SOURCE" \
  --tokenizer "$VERIFIED_TOKENIZER_DIRECTORY" --tokenizer-wheel "$PINNED_BPE_WHEEL" \
  --deadline-utc "$FIXED_UTC_CUTOFF" --output "$NEW_PRIVATE_OBSERVATION"
```

HTTPS uses a fixed route set, system trust and verified hostnames, no proxy
environment, redirects, credential forwarding or retries. Whole-operation
absolute alarms include DNS, TLS, headers and slow-drip response reads.
Cancellation closes the scorer channel and reaps its owned attach child;
the existing lifecycle removes only its uniquely labelled scorer container.
Ambiguous create/cleanup cannot become PASS. Output remains private until
privacy review; preserve failures unchanged. No live run has been performed
by this increment, and this command does not authorize one.
