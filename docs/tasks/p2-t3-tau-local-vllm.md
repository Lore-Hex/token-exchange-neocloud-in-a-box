# Bounded local vLLM benchmark transport

Status: implemented and tested against synthetic local HTTP endpoints. **No
DeepSeek generation, tau quality score, paid API call or confidential result.**
This continues [the parent transport](p2-t3-tau-parent-transport.md), without
changing its closed-network upstream worker or scripted runner defaults.

`scripts/tau_local_vllm.py` adds an explicit `LocalVllmSession`. It uses the
existing pipe framing, frozen non-thinking smoke manifest, request validation,
Bridge and one all-role Budget. The original `ParentSession` still dispatches
scripted records only. The shared session delegates record preparation;
neither worker packets nor the original runner can select a live endpoint.

## Request sequence

1. The trusted parent selects a fixed IPv4 loopback port for each role. No
   arbitrary URL, credential, path, DNS resolution, environment proxy,
   redirect, retry or external API is supported. Candidate and reference ports
   must differ, and neither can be the simulator or evaluator port.
2. Validate the worker's text messages, tools and generation settings. The
   initial supported path is no tools, or `tool_choice=auto`. Other choices
   are rejected before HTTP rather than silently changing their semantics.
3. POST `/tokenize` on that same local engine, with the full history and tools,
   `reasoning_effort=none`, `enable_thinking=false`, no extra special tokens,
   and the normal generation prompt. Require integer count, matching
   token-array length and the manifest's exact context limit. Token IDs and
   conversation content stay in memory and are not exported.
4. Require `prompt_tokens + max_completion_tokens <= context_tokens`, plus
   the frozen per-request input ceiling. Only then reserve worst-case
   generation cost/tokens in the existing Budget.
5. POST `/v1/chat/completions` once. Tokenization, generation and delivery
   share one per-request deadline bounded by the trial deadline. A whole-call
   signal deadline prevents slow headers or body chunks resetting the timeout.
   Response framing and size are bounded; error bodies are neither logged
   nor included in exceptions.
6. Require the exact model name and the same prompt count returned by
   `/tokenize`. Preserve assistant/tool content unchanged; the existing Bridge
   validates tool-call JSON, evaluator assertions, usage and finish reasons.
   Nonempty reasoning, refusal, audio or legacy function-call output is not
   silently discarded. Truncation settles known usage but is a failure.
   Invalid or uncertain generation keeps the reservation and usage unknown,
   with no retries or further dispatch on the failed session.

## Primary sources

Reviewed vLLM revision: `5df63a7203cdb9328d824a27b682e77c7b9928be`.

- [Tokenization protocol](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/serve/tokenize/protocol.py):
  SHA-256 `e887a771afbc7ae3c709691d31e34da8f3d63a627eab55cc3d97d05625901c07`.
- [Tokenization service](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/serve/tokenize/serving.py):
  SHA-256 `194f12a7f986aac45d23f8252eefcbb8a10d976710000b36614b08cf3359903c`.
- [Shared online renderer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/online_renderer.py):
  SHA-256 `a8f90b4d1d41b2194557992a12105f65bf720d743384e18ee01c2870a75a7c72`.
- The [chat protocol and DeepSeek encoder](p2-t3-tau-parent-transport.md#primary-source-contract)
  establish corresponding non-thinking chat fields. Tokenization and chat use
  shared preprocessing. This source-backed contract does not prove that an
  arbitrary process on a local port runs the reviewed code.

## Verification

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau_local_vllm.py'
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
make check
make test-e2e
```

Synthetic tests exercise actual loopback HTTP, both framing modes, no
DNS/proxy/redirect behavior, malformed/oversized/slow responses, deadline
restoration, count/context disagreements, budget exhaustion, reservation
before generation, sticky unknown usage, truncation, tool continuation and
content-free errors/accounting. They do not execute vLLM.

## Remaining boundaries

- Loopback is not runtime identity or attestation. Before real runs, verify
  the operator-controlled engine image, overlay, weights, tokenizer, framing
  and serving arguments against the frozen manifest. Counts are
  **engine-reported**, not independently verified tokenizer measurements.
  A generation/count disagreement aborts, but does not retroactively prove
  pre-dispatch bounds for a malicious or misconfigured engine.
- The manifest remains `quality_eligible=false`. This module cannot produce
  scored-comparison evidence or silently turn the benchmark into a
  non-thinking evaluation.
- [The explicit live runner](p2-t3-tau-live-runner.md) now supplies container-worker
  wiring and content-free run recording using this session. Actual reviewed
  deployment/runtime/tokenizer receipts, independent role deployment/approval,
  GPU runs and the complete evaluation remain required. The original CLI still
  exposes only the closed-network scripted integration operation.
- This is local lab HTTP, never public or confidential transport. No ports
  are opened or published by this change; no production routing is modified.
