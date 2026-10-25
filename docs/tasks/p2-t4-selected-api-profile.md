# Selected DeepSeek API profile

This is a software-only increment toward an ordinary selected-checkpoint
service. It reuses the [Qwen API boundary](../../internal/pilotapi/handler.go),
not the Qwen engine launcher. It does not deploy a service, change engine flags,
alter the current lab, register a provider, or establish hardware/quality success.

## Closed startup choice

[`nib-pilot-api`](../../cmd/nib-pilot-api/main.go) accepts `--profile qwen`
(the unchanged default) or `--profile selected-deepseek`. Empty and unknown
values fail startup. One private profile value binds the catalog model ID,
request model validation, output limit and admission count. Operator metadata,
key, prices and public retirement remain explicit and use the existing checks.

The selected profile declares text chat and SSE only: 8,192 context tokens,
at most 256 generated tokens and one admitted request. These are conservative
API bounds, aligned with the [selected policy](../../scripts/tau_selected_policy.py)
and [8K lab profile](../../scripts/deepseek_tuning.py), not the draft pack's
larger context/capacity settings or a sustained-service measurement. The API
bounds body bytes and output tokens; it is not a tokenizer or prompt-fit gate.
An owned engine must independently enforce the total context budget.

Tools, structured output, reasoning and prompt caching remain unadvertised.
The initial selected profile rejected reasoning/thinking controls. The
[explicit reasoning follow-up](p2-t4-selected-reasoning-controls.md) now validates
and forwards the pinned engine's native effort settings without inheriting
Qwen's hint stripping. Existing Qwen requests retain their exact compatibility
behavior; live reasoning qualification remains separate.

## Model identity boundary

The [selected pack](../../models/deepseek-v4.1-flash-1017-uncensored-fp8/1/pack.yaml)
has two deliberately distinct names:

- Catalog/API ID: `dealignai/deepseek-v4.1-flash-1017-uncensored-fp8`.
- Current lab engine name: `deepseek-v4.1-flash-1017-uncensored-fp8`.

The catalog requires `vendor/model`. This API accepts and forwards only that
canonical ID; the bare lab name, original checkpoint and other aliases fail.
There is no request model translation or JSON/SSE response rewriting. Usage
passes through unchanged, including missing usage; no second accounting ledger
is introduced. The current lab cannot simply be attached to this API profile.

A future, separately reviewed persistent engine must serve the approved canonical
ID and prove that exact model identity at readiness and in JSON/SSE responses.
The API's existing `/health` is only an upstream health probe, and its catalog
is a startup declaration, neither a model-identity nor a readiness proof.

## Shared controls and remaining gates

The backend remains fixed to loopback port 8000, with no request/environment
backend selection. Existing bearer auth, header isolation, 64 KiB body limit,
120-second request deadline, cancellation, overload, retirement and API drain
behavior are reused. No request bodies or raw upstream errors are logged.
Only the existing standard-TLS front door may eventually be exposed.

All privacy claims remain false: `zdr=false`, `confidential=false`, `e2ee=false`.
The pinned Catalog v2 schema has no privacy fields; this increment does not add
unsupported properties or turn absent catalog fields into privacy guarantees.

Persistent engine ownership, startup readiness, least-privilege container/TLS
wiring, engine-aware drain and failure supervision still need implementation
and direct conformance proof. Catalog timeout declarations are inherited, not
new first-token/stream-idle watchdogs. Authenticated live usage/SSE, sustained
limits, full quality/tau, settlement and confidential qualification remain
unproven for this service profile. Existing [deployment gates](../operators/qwen-service.md#activation-order)
are not bypassed by selecting it.

## Focused regression coverage

`go test -race ./internal/pilotapi ./cmd/nib-pilot-api` uses mock transports and
the existing ephemeral-port drain test, not hardware or a fixed-port service.
Coverage includes pre-change Qwen wire bytes, the committed pack/catalog identity
join, schema and capability limits, rejected aliases/options, auth and startup
checks, exact JSON/SSE/usage pass-through, cancellation, single admission,
retirement during upload and private-error suppression.
