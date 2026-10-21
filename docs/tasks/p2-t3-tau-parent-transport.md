# P2.T3 bounded parent accounting prerequisite

Implemented against local pipes and synthetic fixtures only. This is **not a
live tau runner**, tokenizer, endpoint verifier, scored trial, or qualification.
The existing [scripted single-trial CLI](p2-t3-tau-single-trial.md), payload
publisher, benchmark manifests, upstream runner/evaluator and historical
evidence remain unchanged. There is no Docker, upstream execution or network
operation in this prerequisite.

## Boundary

[tau_transport.py](../../scripts/tau_transport.py) provides:

- `Channel`: two explicitly supplied, owned pipe descriptors, checked for
  direction/type, non-inheritable and nonblocking. Four-byte lengths precede
  bounded JSON frames. Fragmentation is supported; unknown/duplicate fields,
  malformed JSON, EOF, cancellation and deadlines fail closed. Raw exceptions
  are suppressed. No socket, filesystem mailbox, guessed fd number, pickle,
  endpoint, environment proxy, redirect or credential interface exists.
- `WorkerProxy`: the existing completion-shaped callback mapped to exactly
  three fixed role IDs. It has a sequence counter, but **no accounting ledger**.
  It rejects concurrent/reentrant calls and mismatched response sequences.
- `ParentSession`: exactly one existing `tau_trial_worker.Bridge`, which owns
  exactly one existing `tau_preflight.Budget`. Agent requests select candidate
  or reference; simulator/evaluator keep their separate configured identities.
  Every delivery reserves before dispatch, and uses the existing response,
  tool-call, assertion and usage validation. No second scorer or reducer exists.

Only bounded, in-memory **scripted response records** can be dispatched. They
are supplied by the trusted parent, not worker frames. They cannot specify a
callable, URL, header or credential. There is deliberately no live-dispatch
switch. Tests exercise actual local pipe I/O; they do not import tau/LiteLLM,
execute an upstream tool, or contact an endpoint.

The existing 1 MiB request and 256 KiB response structural caps remain. A frame
allows an additional 4096 bytes for its envelope. Scripted parent records are
capped at 128 records and 4 MiB total. These are engineering limits, **not token
estimates**. The original trial/request budget controls also apply. Response
delivery uses the request deadline, not an extended trial deadline. Late or
invalid usage stays reserved/unknown; failure after valid settlement preserves
known usage but cannot make the failed session successful. Parent EOF/error
classification is authoritative; the proxy can observe only fixed failure/EOF.
Pipe operations also reject caller deadlines beyond the existing preflight's
1800-second maximum trial duration, rather than accepting an unbounded wait.

## Explicit framing, not a quality-manifest rewrite

The library accepts a separate, digest-bound `nib.tau-transport-smoke/v1`
manifest with purpose `nonthinking-transport-prerequisite` and
`quality_eligible=false`. It references, but does not rewrite or independently
authenticate, the benchmark manifest digest. It contains the existing budget
settings and explicit per-role served name, tokenizer/framing hashes, context,
parallel-tool policy and `reasoning_effort="none"`. Omitting reasoning, requesting
thinking, or passing a frozen benchmark manifest directly is rejected.

This is an explicit **different smoke configuration**, never evidence of
equivalence to a frozen quality configuration. Reasoning-history fields and
multimodal content are rejected, not silently removed. Future thinking support
needs reviewed history round trips and a new explicit contract.

Only these generation inputs are mapped: temperature, one of max_tokens or
max_completion_tokens, zero retries, stream=false, n=1, seed, tool_choice,
explicit reasoning and parallel_tool_calls. Unknown keys or conflicts with the
manifest fail rather than being ignored. Upstream-generated seeds, tool IDs,
argument strings and tool-result messages are preserved. Tool schemas/choices
are data only; the parent never executes tools. The ordinary tau orchestrator
and evaluator are not replaced or modified by this change.

Before reservation, a **parent-only count input** must bind the manifest,
role, exact complete request digest, tokenizer/framing hashes, prompt count and
context. It must satisfy both the existing input reservation and
`prompt_tokens + reserved_output_tokens <= context_tokens`. Missing, stale,
cross-role or mismatched inputs reject. This validates the input contract; it
does **not** establish that a tokenizer measured the count or an endpoint has
that context. Tests supply synthetic counts. A reviewed, authenticated count
producer remains absent, and live dispatch remains unavailable.

## Primary-source contract

Tau revision is `fc0055dc4e0a316c3f83133267fbd6faaa770992`, already authenticated by
[the unchanged source/wheel lock](../../bench/tau-runtime-lock.json):

- [llm_utils.py](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/utils/llm_utils.py)
  converts messages/tool schemas, supplies auto tool choice when tools exist,
  and funnels `generate` through the shared completion hook. Source SHA-256:
  `8c1f1c297e879b2a2d48ac65394cdd497bbd10f42fefa0ce770e7a65ce42bf95`.
- [LLMConfigMixin](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/agent/base/llm_config.py)
  places the seed in `llm_args`; the normal orchestrator seeds agent and user.
- [NL assertions evaluator](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_nl_assertions.py)
  uses the same generate hook and its independently configured evaluator role.
  The existing Bridge's assertion correspondence checks remain authoritative.

vLLM revision is `5df63a7203cdb9328d824a27b682e77c7b9928be`:

- [Chat protocol](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/protocol.py)
  defines the mapped fields; `build_chat_params` propagates reasoning settings
  and `build_tok_params` binds input/output to max_model_len. Independently read
  source SHA-256: `8744d5e2004b2d376e34cf17de51c919bca6d81b0cc8392da138db1ef05a7c61`.
- [V4.1 tokenizer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py)
  and [served encoder](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41_encoding.py)
  determine actual tool/system/history framing. This PR does not execute or
  duplicate them. The current lab context is 4096; no tau request is asserted to
  fit. Tau's locked tokenizers 0.22.1 is not presumed equivalent to the measured
  engine's 0.23.3 / Transformers 5.19.0.

## Verification and remaining work

```bash
python3 -B -m unittest discover -s scripts -p 'test_tau_transport.py'
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
make check
```

Regressions cover fragmented frames, oversized input/output, duplicate JSON,
unknown roles/kwargs, replay, reentry, partial EOF, backpressure, request/trial
deadlines, cancellation, known versus unknown usage, truncation, tool round
trips, evaluator assertions, content canaries and exactly one ledger.

No transcript/frame capture or new result format is added. Future container
wiring must preserve the network-denying worker audit, divert ordinary
stdout/stderr as before, consume this content-bearing protocol without raw
capture, and return only the existing content-free aggregate inputs. That
wiring, actual count/runtime closure, endpoint identity, credentials, paid
role/resource approvals and any live trial need a separate reviewed change.
