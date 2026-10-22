# Mixed-runtime tau admission

Status: implemented and tested with synthetic receipts, fake kernel observations,
real subprocess pipes and synthetic loopback HTTP. No model or GPU run, fresh
measurement receipt, tau score or quality/confidential qualification is claimed.
The [CPU parity producer](p2-t3-tau-cpu-parity.md) and a future owned lifecycle
must supply actual proof; historical runs and test fixtures cannot be promoted.

## Explicit contract

`nib.tau-local-deployment/v3` retains the selected-target v2 shape and requires
`engine_kind` in each active role descriptor: `vllm` or `llama.cpp`. Candidate
and reference can only use the existing pinned vLLM receipt/transport. Only
simulator and evaluator may select llama.cpp. V1 still requires four vLLM roles
and three distinct inventories (candidate/reference share the pinned weights);
v2 still requires three vLLM roles and three distinct inventories. Neither
accepts an engine-kind field or a CPU receipt. No missing kind is defaulted in v3.

V3 keeps both target configurations in the frozen benchmark, the selected target,
three distinct processes/ports/listeners/inventories and the same six original
receipt files plus deployment manifest. It emits `nib.tau-live-binding/v3` with
the original digest bindings and explicit `engine_kinds`; no private identities
or endpoints are exported. The default scripted CLI is unchanged.

CPU models must match the exact model ID, revision, GGUF filename, byte size and
SHA-256 in `scripts/tau_cpu_support_models.json`. No aliases, arbitrary 40-hex
revisions or same-weights support roles are admitted. The existing 4096-token
context, 256-output-token maximum, non-thinking/auto-tool subset, shared budget
and deadlines remain unchanged. A larger context or output budget is a separate
measurement and review, not a fallback when an original tau prompt does not fit.

## GGUF identity

`scripts/tau_llama_identity.py` defines deterministic identity recipes using
`tau_preflight.encoded` (sorted compact JSON plus one newline):

- `model_files_sha256` hashes `nib.tau-gguf-inventory/v1` and its one-file list
  of exact filename, bytes and SHA-256.
- `tokenizer_sha256` hashes `nib.tau-gguf-tokenizer-identity/v1`, the full GGUF
  SHA-256 and exact llama.cpp revision/archive SHA-256. It identifies embedded
  tokenizer metadata interpreted by that implementation, not a separate file.
- `framing_sha256` hashes `nib.tau-gguf-framing-identity/v1`: that tokenizer
  identity, embedded templates with no override, the existing native helper's
  full-session/Jinja/generation-prompt/auto-tool/non-thinking/special-token
  settings, 4096 context and the exact helper source hash.

These are domain-separated whole-artifact/interpretation identities, **not
extracted vocabulary, tokenizer-config or template-byte hashes**. No HF
`tokenizer.json` or `tokenizer_config.json` is invented. Computing expected
identity digests does not measure a running server or establish parity.

Primary source is the existing llama.cpp revision
`620d2225cc436ab0b001e3200e590e3b286ef9ab`:
[common_chat_templates_init and common_chat_session](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/common/chat.cpp)
use the model's embedded default/tool-use templates when no override is supplied.
The existing [native helper](../../scripts/tau_cpu_parity.cpp) fixes the complete
framing invocation; the [transport source audit](p2-t3-tau-cpu-framing.md#pinned-sources)
describes the unchanged server API path.

## Original receipts

A CPU runtime receipt uses `nib.tau-llama-runtime/v1`, with the common producer,
role/model/revision/engine_revision, endpoint, process, logging and measurement
objects. Its exact additional fields are `gguf` (filename/bytes/sha256),
`model_files_sha256`, `observed_unix` and `serving`. CPU `serving` contains only
`image_sha256`, `helper_sha256`, `arguments_sha256`, `tokenizer_sha256` and
`framing_sha256`. Image/helper hashes are original measured runtime artifacts,
not values manufactured by the consumer. Measurements must contain equal
expected/observed hashes for all five, plus `gguf_sha256`, `model_files_sha256`
and `executable_sha256`; argv must match the live process. The runtime observation
must not be future-dated or an hour old; deployment expiry cannot extend past
one hour after that observation.

Its `nib.tau-llama-tokenizer/v1` pair contains producer, runtime receipt hash,
GGUF/tokenizer/framing digests, 4096 context, `observed_unix` and `cases`. The
tokenizer observation must follow the runtime observation and not be in the
future. Cases must be exactly the unchanged `text`, `system`, `tools` and
`tool-continuation` probes. Their source-bound case hashes, independent/endpoint
ID hashes and positive counts must match; counts cannot exceed the existing
native helper's 1024-token probe bound. Raw IDs and request bodies are not retained.

As in legacy admission, operator approval authenticates the complete deployment
digest and original measurement provenance. A source hash alone is not proof
of execution. The consumer checks source/receipt integrity and current process
liveness; it never executes receipt-supplied code or converts a historical
parity report into a live receipt. A dishonest issuer/kernel remains outside
this local trust model. No attestation is implied.

## Dispatch and remaining work

Both transports use one shared guarded connection method. It checks freshness
and process identity before connect, then exact connected-peer ownership before
any body for `/apply-template`, `/tokenize` or `/v1/chat/completions`. Procfs
visibility, same UID/network namespace, logging policy, source-only loading,
single Bridge/Budget, upstream worker/scorer and failure accounting are retained.

Actual two-model parity, fresh CPU receipt production, a bounded owned lifecycle
and integration of the guarded parent with DeepSeek's namespace remain required.
Original tau prompt/context fit and structured evaluator output also remain
unmeasured. This change does not keep engines alive, launch models, alter Docker
confinement, select an invented reference or authorize a GPU run.

The subsequent opt-in [fresh CPU-pair session](p2-t3-tau-cpu-session.md) implements
four original receipts and bounded held-server release. It does not remove the
remaining namespace/UID, target-receipt, prompt-fit or reference prerequisites.

## Verification

```sh
PYTHONPATH=scripts python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
python3 -B scripts/check-doc-links.py
git diff --check
```

Tests cover both selected targets, legacy contracts, exact pins and source
closure, stale/malformed receipts, wrong-kind roles, body-before-peer rejection,
real mixed-protocol pipes, retained failed usage and no quality promotion.
