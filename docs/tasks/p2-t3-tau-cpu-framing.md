# CPU support-model transport

Status: explicit llama.cpp adapter tested against synthetic local HTTP. No model,
GPU, tau trial, independent tokenizer measurement or confidential execution was
performed by this change. The current live tau runner remains vLLM-only; this
library cannot admit these CPU candidates or manufacture runtime receipts.

The [selected-target plan](p2-t3-tau-selected-target.md) needs distinct simulator
and evaluator models while DeepSeek occupies its GPU allocation. This step adds
the CPU protocol needed before measuring that proposed topology. Small Qwen
models are operational candidates, not validated substitutes for tau's
recommended simulator/evaluator or evidence of benchmark quality.

## Implementation

`scripts/tau_local_llama.py` supports only the two explicitly named Qwen candidates,
the pilot's 4096-token context and at most 256 output tokens. Its shared
`LocalChatHTTP` base retains vLLM's numeric IPv4 loopback sockets, fixed paths,
no DNS/proxy/redirect/retry behavior, bounded responses, whole-call deadlines,
content-free errors and unmodified response/usage validation. The vLLM subclass
retains its original tokenize contract. No external transport or launch flag is
added to either CLI.

For the already validated parent text request, the CPU adapter:

1. Requires non-thinking mode and the reviewed auto-tool subset; rejects media
   input and unknown generation overrides before network access.
2. Sends the same generation body to `/apply-template`, with explicit
   `enable_thinking=false`, and requires one nonempty prompt string.
3. Calls `/tokenize` with that prompt and both `add_special=true` and
   `parse_special=true`, matching the pinned generation path, not the tokenize
   endpoint's different default. Token IDs must be integers and fit the declared
   context. Both calls share the original request deadline.
4. Preserves messages, tools, sampling and output limit for chat generation.
   Context overflow rejects before generation. Exact response model and input
   usage must match; reasoning output and count disagreement reject. Truncation
   remains truncation for the existing parent accounting, never repaired success.

The caller-declared context is not an observed engine limit. Server-rendered
text and server-tokenized IDs are engine reports, **not independent full-framing
parity**. Real admission still needs independently rendered/template-tokenized
cases, measured process/socket/model provenance, no-content logging proof and
bounded owner lifecycle in the same private namespace. Existing receipt schemas
are not widened to accept llama.cpp by this increment.

## Pinned sources

All engine references use the existing pilot's llama.cpp commit
`620d2225cc436ab0b001e3200e590e3b286ef9ab`, with archive SHA-256 in
`scripts/pilot/pins.json`:

- [server-context.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server-context.cpp):
  apply-template and chat both invoke `oaicompat_chat_params_parse`; the generation
  path tokenizes with both special-token flags true.
- [server-common.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server-common.cpp):
  the shared parser accepts `reasoning_effort=none`, merges template kwargs and
  uses complete messages/tools when rendering.
- [server-schema.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server-schema.cpp):
  `max_completion_tokens` aliases the bounded generation count.
- [server-task.cpp](https://github.com/ggml-org/llama.cpp/blob/620d2225cc436ab0b001e3200e590e3b286ef9ab/tools/server/server-task.cpp):
  OpenAI-shaped usage contains total prompt tokens, including cached tokens.

`scripts/tau_cpu_support_models.json` records two separate one-file GGUF inference
inventories; each GGUF embeds weights and tokenizer metadata. The 0.6B pin equals
the existing pilot. The official [1.7B inventory at its exact revision](https://huggingface.co/api/models/Qwen/Qwen3-1.7B-GGUF/tree/90862c4b9d2787eaed51d12237eafdfe7c5f6077)
reports `Qwen3-1.7B-Q8_0.gguf`, 1,834,426,016 bytes and LFS SHA-256
`061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a`.
This is publisher metadata inspected on 2026-10-22, not a local download/hash or
memory/latency measurement. These candidate pins are not deployable model packs.
Do not equate disk bytes with runtime memory or assume a 4096-token context fits
the original tau prompts.

## Verification and next step

```sh
python3 -B -m unittest discover -s scripts -p 'test_tau*.py'
python3 -B scripts/check-doc-links.py
make check
```

Tests use actual local sockets with synthetic responses and retain the existing
vLLM/session/accounting tests. CPU tests cover render/generate identity, full
tool history, tokenizer flags, malformed responses, exact model and usage checks,
context overflow, deadlines, media rejection and closed endpoint paths.

Next: a committed bounded CPU-only producer must verify the selected GGUF files,
independently render and tokenize full histories, start/observe/stop both actual
engines, measure resource limits and test structured evaluator output. Only then
add an explicit reviewed runtime kind to live admission and integrate with the
owned DeepSeek namespace. No score or complete tau run is claimed here.
