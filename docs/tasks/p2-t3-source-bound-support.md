# Source-Bound Support Counting

**Software-only opt-in. No paid counting, generation, live admission, budget
approval or running-profile change is granted by this implementation.**

The original telecom simulator supplies 30 tools. The legacy external transport
rejects tools and counts only text messages. The new
[support transport](../../scripts/tau_external_support.py) connects the
[authenticated catalog](p2-t3-telecom-offline-runner.md) to the actual bounded
external trial path, without raising the candidate's 16-tool limit or changing
the default lookup and full-task paths.

## Source and Semantics

The explicit `run --full-task --support-source-archive` opt-in requires the
existing certificate-pinned v2 endpoint contract. Before tokenizer creation,
credentials or HTTP-client construction, it authenticates the entire original
tau archive, source tree, per-file pins and selected plan. Only the existing
retail task 2 diagnostic or the unchanged first-five staged retail/telecom
selection is supported. Airline remains outside this opt-in.

For telecom, the exact committed 30-tool catalog is required; reordered,
modified or foreign tools fail before counting. Chat retains the original
seed, tool schemas, arguments, results and history. The paired Responses count
contains the same input semantics, including every tool schema and tool-call
result. Explicit non-strict schemas prevent Responses defaults from changing
the original optional properties. Retail simulator/evaluator requests retain
the existing text projection. The original evaluator's assertion count is
source-bound; the zero-assertion staged tasks cannot generate an invented judge
call. No task, original reward criterion or user-tool implementation changes.

Primary pinned sources at `fc0055dc4e0a316c3f83133267fbd6faaa770992`:
[simulator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/user/user_simulator.py),
[message/tool conversion](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py),
[original evaluator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_nl_assertions.py).

## Parity and Admission

Official [token-count documentation](https://developers.openai.com/api/docs/guides/token-counting),
checked 2026-10-27, guarantees the count for matching Responses inputs, not a
general proof of Chat Completions equivalence. Source authentication and an
offline fixture cannot establish that equivalence. Each support response must
therefore match the count exactly. Receipts retain only request/wire hashes,
integer counts, fixed statuses and source identities. `observed_pairs_match`
describes those individual responses; `token_count_parity_verified` and
`live_admission` remain false. A previous matched pair never exempts a later
request from counting or usage validation.

**Conservative standard-rate estimate, not an invoice cap:** before a support
count is sent, the additional serial generation ledger requires room for
`1,047,576 * $2/M + 2,048 * $8/M = $2.111536`. This uses the published
[GPT-4.1 context and prices](https://developers.openai.com/api/docs/models/gpt-4.1),
checked 2026-10-27, rather than assuming the unmeasured Chat input equals its
Responses count. It reserves that amount before generation. Candidate requests
reserve `32,768 * $0.65/M + 2,048 * $1.45/M = $0.0242688` using the unchanged
declared candidate ceiling. Both roles share the existing $3 generation ceiling
and at most 32 reservations; the ordinary trial budget remains in force too.

Only a complete, validated, known-usage response settles its actual standard-rate
cost and releases unused generation reservation. Mismatch, cancellation, lost
response, invalid usage or rejected content holds the pending reservation and
blocks all three roles. No retry or automatic continuation follows. Count calls
retain $2.095152 per attempt even when generation is never sent. Counting is
not assumed free, and unknown counting charges are not released.

The shared count cap stays 32. The counted-input ceiling remains 30,720 plus
2,048 reserved output; actual prompt/output must fit 32,768 and agree with the
count or the trial fails. A count is not a pre-send proof about Chat formatting;
the full-context reservation protects the generation budget if they differ.
All body/depth/string/duplicate checks, C1 execution, zero retries, request/trial
deadlines and cleanup rules remain unchanged.

## Execution Gates

Use the existing [full-task command](p2-t4-tau-external-full-task.md#explicit-operator-handoff)
only after explicit approval, adding:

```sh
--support-source-archive "$PINNED_TAU_ARCHIVE"
```

Staged runs still require the complete prepared plan hash and selected index.
No new flag relaxes or replaces the aggregate reservation validator. Its current
$70.044864 per-trial reservation and full-plan sum remain unchanged. Releasing
unused **per-request generation** reservation is not a cross-trial settlement
ledger. A later predeclared sequential plan with a hard aggregate cap needs its
own reviewed admission/settlement contract. Never release unknown or cancelled
billing, silently skip failed trials, or treat example budgets as available funds.

Before any live run: reconcile prior commitments and held charges, verify count
billing/account terms, approve the exact source/endpoint/plan/index and total
ceiling, coordinate C1, and retain every receipt. No worker replay or successful
software merge satisfies those gates. A completed scored trial is not full tau,
matched-reference quality, or confidential qualification.

## Offline Verification

The focused tests use only synthetic HTTP replies. The optional pinned archive
check authenticates legacy retail task 2 and every staged retail/telecom task.
The original network-disabled worker can exercise the same source-bound
transport with fixed synthetic peers:

```sh
TMPDIR=/private/tmp python3 -I -S -B scripts/record-tau-telecom-offline.py \
  --source-archive "$PINNED_TAU_ARCHIVE" --wheelhouse "$PINNED_WHEELHOUSE" \
  --docker-socket "$EXISTING_DOCKER_SOCKET" --index 0 \
  --source-bound-support --receipt "$UNUSED_PRIVATE_RECEIPT"
```

That replay retains the original scorer/tools and exercises real parent
transport code, but its 100-token fixture counts are not measured parity and
its scripted reward is not model quality. It has no key or live HTTP factory.

The [retained index-0 replay](../../evidence/p2/2026-10-27-source-bound-support-offline/run.json)
at producer `4c9a2ef2fb4c3fc46296ae7b4fef1a67bf6ae93b` completed both original
tool round trips, zero tool errors and owned-container cleanup. The original
receipt and output bytes are retained. Its three simulator count/usage pairs
are synthetic 100-token fixtures, and its original reward is 0.0; neither is
live parity or model-quality evidence. The selected task has zero NL assertions,
so this replay did not generate an evaluator request.
