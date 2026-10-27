# Offline telecom tool/count projection

Status: implemented for offline tests only. No live owner imports this helper.
No service call, paid trial, token-count parity or benchmark score is claimed.

The pinned tau telecom simulator exposes 30 tools. The live external simulator
currently accepts only text histories. Simply permitting its tools would omit
their schemas and tool history from the existing count request. This increment
prepares a paired Chat Completions / Responses-count projection without changing
live admission, source bindings, budgets, model selection or upstream execution.

`scripts/tau_telecom_projection.py` reuses the existing bounded Chat validator,
with a separate 30-definition bound. Candidate calls retain their 16-definition
bound. The projection requires equality to a caller-supplied expected catalog,
preserves schema/argument/result text, rejects duplicate or orphan calls and
out-of-order results, and limits each call frame to the existing 16-call bound.
Private payloads stay in memory and must never be logged as receipts.

The count payload includes flattened function definitions, complete matching
call/output items, disabled truncation and the original tool-choice controls.
Explicit `strict: false` preserves Chat's non-strict schema semantics; no schema
properties are rewritten. The original seeded Chat request remains the intended
generation path. Official sources inspected on 2026-10-27:

- [Token counting](https://developers.openai.com/api/docs/guides/token-counting):
  count tool definitions together with input using Responses-format requests.
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling):
  call IDs join tool calls to their outputs; Responses and Chat have different
  omitted-strict defaults.

These sources do not establish exact Chat-versus-Responses count parity for
`gpt-4.1-2025-04-14`. The helper makes no such assertion. It also does not
authenticate the caller's catalog source. Both limitations are explicit false
fields, not acceptance flags.

## Verification

`python3 -m unittest discover -s scripts -p 'test_tau_telecom_projection.py'`
tests synthetic 30-tool catalogs, private in-memory semantics, missing/duplicate
and reordered results, unsupported fields, byte bounds and absence of network
or content logging. Existing external-endpoint tests cover the shared validator.
Full `make check`, `make test-e2e`, hosted CI and exact-head independent review
remain merge gates. Tests are not authenticated upstream catalog evidence.

## Remaining work

- Authenticate the exact original telecom tool schemas in the pinned worker and
  bind the expected catalog to a newly versioned staged contract.
- Wire only that reviewed staged contract into the existing count/generate
  owner; retain count-before-generation and measured-count equality checks.
- Prove live parity under a separately admitted bounded smoke. A mismatch must
  remain a retained failure, including known usage; do not loosen equality.
- Reconcile all weekly commitments before any additional paid trial. The count
  endpoint is not assumed free. Existing request/token/cost/deadline and zero-
  retry limits remain unchanged.
