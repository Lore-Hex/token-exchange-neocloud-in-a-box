# P2.T3: Long context and isolated prefix-cache candidate

Status: implemented candidates, **not full GPU qualification or a public capacity claim**.
The explicit `long128k-eager` and `long128k-graphs` profiles keep the selected
abliterated checkpoint, B300 TP4 x DP2, one admitted request, GPU-resident Engram,
512 GiB host-memory bound and zero swap. The graph candidate changes the execution
configuration, not the limits below. Existing profiles remain unchanged.

| Bound | Candidate |
|---|---|
| Context, including generated tokens | 131,072 tokens |
| Maximum output, when requested | 16,384 tokens |
| Default output when omitted | 2,048 tokens |
| Prefill scheduling budget | 4,096 tokens per iteration, chunking enabled |
| HTTP request body | 4 MiB |
| Completion deadline | 900 seconds |
| Guard/TLS shutdown grace | 910 seconds |
| Owner drain per frontend | 930 seconds |
| Admission cutoff before child hard stop | 1,960 seconds |
| Total admitted requests | 1 |

Context is not an input-only allowance. The engine's tokenizer must verify that
the prompt plus requested output fits. The byte bound is a separate resource
limit, not a tokenizer or truncation policy. Text, tool results, reasoning and
tool arguments can round-trip larger replies within the aggregate body bound;
message count, tool count, JSON depth, duplicate-key rejection and authentication
are unchanged. The guard never truncates a request to make it fit.

The long profile coordinates the request deadline, guard/TLS shutdown grace,
owner drain and admission cutoff. It requires the pinned TLS shim's bounded
`--shutdown-grace` option. Other profiles retain their 120-second requests and
375-second cleanup reserve. Both frontend drains and process-group cleanup fit
inside the long profile's 1,960-second reserve; the host's additional cleanup
reserve stays unchanged. Wall-clock jumps can shorten, never extend, the bound.
The 16K value is a request ceiling, not a promise that every prompt/output
combination finishes within 900 seconds. Do not advertise the paired maxima
until measured. Retirement must preserve an active stream and usage completion;
forced termination is a failure, not a successful response.

## Pinned engine settings

The engine revision remains
[`5df63a7203cdb9328d824a27b682e77c7b9928be`](https://github.com/vllm-project/vllm/tree/5df63a7203cdb9328d824a27b682e77c7b9928be).
No claim about a newer engine's performance applies to this candidate.

- `--max-model-len 131072`, `--max-num-batched-tokens 4096`, and
  `--enable-chunked-prefill` use the pinned
  [scheduler](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/scheduler.py)
  and [argument parser](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/engine/arg_utils.py).
  A large context does not require allocating a whole-context prefill batch.
- `--enable-prefix-caching` explicitly enables the pinned
  [cache configuration](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/cache.py).
  The KV cache used within a generation and automatic prefix reuse across
  generations are different properties. Neither successful startup nor low
  latency alone proves cross-request cache hits.
- `--enable-prompt-tokens-details` is defined by
  [FrontendArgs](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/launchers/cli_args.py).
  The [chat serving code](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/serving.py)
  exposes actual `usage.prompt_tokens_details.cached_tokens`, including in the
  final usage chunk when streaming usage is requested. The guard forwards it
  unchanged. An absent field must never be interpreted as a measured cache hit.
- `long128k-graphs` removes `--enforce-eager` and selects
  `FULL_DECODE_ONLY` with `cudagraph_capture_sizes: [1]`, retaining
  `pass_config.fuse_allreduce_rms: false`. Pinned
  [compilation configuration, lines 610-631](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/compilation.py#L610-L631)
  defines decode-only graphs; the
  [candidate builder and dispatcher](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/worker/gpu/cudagraph_utils.py#L238-L548)
  select the one-request, one-token decode shape. Prefill stays chunked and
  outside CUDA graphs. Removing `--enforce-eager` also restores the pinned
  engine's normal compiler/JIT defaults; it does not prove that prefill is
  uncompiled or isolate any speedup to CUDA graph replay alone. See
  [eager overrides and compilation defaults](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/vllm.py#L1691-L1756).
  This does not enable DSpark, increase admission or change FP8 KV, cache
  namespaces, DP2 affinity or any deadline. Backend compatibility, actual graph
  capture/replay, memory fit and a throughput improvement still need measurement.
  Configured graph mode is not evidence that every decode used a graph.

The new source files are hash-pinned in `scripts/deepseek_tuning.py`; startup
rejects source drift. The prepared payload, image, guard, catalog, observed
context and selected execution-profile receipt must agree. Use the existing
selected-service prepare/build/run path with `--execution-profile long128k-eager`
or the explicit opt-in `--execution-profile long128k-graphs`
and `--tokenizer-parity --tokenizer-materialization --runtime-observation`.
Do not relabel an old image or start another engine on occupied GPUs.

Both profiles use the same `--long-qualification` hook and ten-request schedule,
including the paired 114,688-prompt / 16,384-output final request. Its 3,600-second
stage, 900-second request bound, 1,960-second cleanup reserve and 180-second host
reserve do not increase for graphs. Host admission still requires more than
7,540 seconds before the original absolute cutoff. The report, prepared source,
owner execution profile and watchdog reserve must agree; an eager report cannot
be relabeled as graph qualification. Missing final usage stays unknown, and a
timeout cannot establish 16K actual output or a successful qualification.

## Cache isolation and routing

The provider API has one long, private gateway credential, not end-user keys.
The trusted gateway must overwrite any end-user `cache_salt` and derive an
opaque 64-character lowercase hexadecimal namespace from the authenticated
workspace. The guard domain-separates that namespace before forwarding it.
The same workspace can reuse its prefix; different workspaces cannot share it.
If no namespace is supplied, the guard generates fresh randomness per request,
preserving isolation at the expense of cross-request reuse. Invalid namespaces
are rejected, not ignored. Do not enable reuse by accepting end-user-selected
tenant IDs. Gateway-side namespace injection is a separate integration gate.

vLLM's pinned [block hashing](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/v1/core/kv_cache_utils.py)
incorporates `cache_salt` in the first block and propagates it through descendant
hashes. Cache reuse is local to an engine replica. DP2 does not imply a shared
cache. The long-profile guard hashes its normalized cache namespace to select
one of the two ranks using the pinned
[`X-data-parallel-rank` interface](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/generate/base/serving.py).
It strips caller-supplied rank headers; all other profiles retain their previous
routing. The namespace is normalized once and reused for the body and affinity,
so omitted namespaces remain isolated and do not accidentally acquire a global
cache. This is routing configuration, not observed per-request GPU-rank evidence.
One busy workspace can concentrate load on one replica; session-scoped affinity
or weighted routing needs separate load qualification. Never use content-derived
metrics labels or retain prompts/completions for these tests.

## Required live qualification

1. Build and run the reviewed source-bound image after all software gates pass.
   Drain the old owner first and preserve its rollback artifacts.
2. Confirm allocated KV-cache capacity and engine settings from content-free
   observations. Reject insufficient-memory or startup failures; do not
   silently shorten the context, offload weights or disable cache safeguards.
3. Tokenize synthetic long prompts with the pinned tokenizer. Test progressively
   at 32K, 64K and near 128K, reserving output inside the total context. Also test
   a requested 16K generation. Record actual generated tokens and finish reason:
   accepting `max_tokens=16384` is not evidence of a 16K completion.
4. With fresh workspace namespaces, record cold and repeated warm runs, actual
   cached-token counts, TTFT and per-replica cache statistics. Change namespaces
   as a negative control. Zero or missing counters fail the reuse claim; do not
   manufacture a speedup from timers. Include tool-loop prefixes and independent
   checks that reuse has not changed answer/tool correctness.
5. Measure concurrency separately. More context and cached prefill do not imply
   faster decode, sustained throughput or unlimited concurrent long requests.
   Leave DSpark and larger graph/batch recipes as separate controlled experiments.

The catalog deliberately keeps `prompt_caching=false` and `cached_input=null`
until live qualification and customer billing terms exist. This candidate does
not establish 1M context, a cache discount, model quality or confidentiality.

Tests: `scripts/test_selected_service_long.py` covers source-bound preparation,
argument tampering, profile/observation mismatches and admission;
`internal/pilotapi/long_profile_test.go` covers guard/body/output bounds, cache
namespace isolation, preserved deadlines and unchanged usage. These are software tests,
not hardware evidence.
`scripts/test_selected_long_graphs.py` additionally replays the existing CLI,
hook and cleanup tests for the closed graph profile, checks legacy argv equality,
rejects cross-profile report substitution, and exercises pinned graph dispatch
without importing CUDA. No graph-profile hardware run is claimed by these tests.
