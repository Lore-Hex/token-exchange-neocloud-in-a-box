# P2.T3 runtime tokenizer parity prerequisite

This opt-in mode replaces the eight correctness cases with four fixed synthetic
chat probes: text, system framing, tools, and a tool continuation. It does not
alter the default lab commands, engine arguments, historical evidence, model
pack, image, TP/DP selection, network isolation or resource limits.

## Method and sources

The primary source revision is vLLM
`5df63a7203cdb9328d824a27b682e77c7b9928be`. The helper checks exact selected-runtime
source hashes, in addition to the existing application inventory and complete
510,311,045,295-byte model-pack check. It also checks the exact sizes and hashes
of `tokenizer.json` and `tokenizer_config.json` before and after local use.

- [DeepseekV41Tokenizer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py)
  wraps `TokenizersBackend.from_pretrained`, normalizes messages and tools,
  applies the V4.1 encoder, and encodes without additional special tokens.
  `reasoning_effort=none` disables thinking. The local child invokes this
  class independently of the server, with `local_files_only=True` and
  `trust_remote_code=False`, under the existing sealed lab bootstrap.
- [Tokenize request fields](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/serve/tokenize/protocol.py)
  and [serving](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/serve/tokenize/serving.py)
  support chat messages, tools and template kwargs, returning token IDs/count.
  Both API paths normalize tools using the pinned `ChatCompletionToolsParam`.
- [Renderer selection](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/registry.py)
  maps V4.1 to `DeepseekV4Renderer`. Its
  [implementation](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/deepseek_v4.py)
  passes original messages to the tokenizer. The
  [online renderer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/online_renderer.py)
  renders text then tokenizes with the request's special-token policy.
- [Chat protocol](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/protocol.py)
  carries reasoning/template settings. The non-streaming
  [serving result](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/serving.py)
  computes `usage.prompt_tokens` from the engine's final prompt token IDs.

Each probe uses non-thinking chat, an added generation prompt, no continuation
prefill and no additional special tokens. Tools are retained in the prompt;
generation uses `tool_choice=none`. The continuation is entirely fixed input,
not a model-selected tool execution. For each probe the supervisor compares
the SHA-256 of the canonical JSON token-ID sequence and its count with
`/tokenize`, then generates one token and checks `usage.prompt_tokens` against
that count. A same-length token-ID mismatch fails before generation.

Only hashes, counts, fixed statuses and timing leave the comparison. The
local tokenizer child redirects ordinary/native stdout and stderr to
`/dev/null` and emits a bounded numeric/hash receipt on a private pipe. Token
strings, prompts and completions are not retained in evidence. HTTP stays on
the existing container loopback address; no new listener or published port is
introduced.

## Bounds and invocation

Preparation only, after the usual offline application inputs are available:

```sh
python3 -I -S host/record-deepseek-lab.py \
  --prepare-local-correctness-b300 "$PRIVATE_BUNDLE" --tokenizer-parity
```

The existing `--prepare-local-correctness-b300-dp2` selector can be used instead
for eight devices. Other profiles and run-time overrides reject the flag.
The ordinary explicit `--run` command consumes the source-bound mode in the
prepared receipt, using its existing approved deadline and private paths.
See the [lab runbook](p2-t3-deepseek-lab.md) and
[eight-GPU profile](p2-t3-eight-gpu-lab.md).

The local tokenizer process has a 60-second limit. At most eight HTTP requests
run serially, each with a 30-second whole-request deadline, no retries and at
most one generated token. The request phase is bounded to 300 seconds, while
the existing overall deadline and 90-second teardown reserve remain binding.
Comparison stops at the first failure. Existing startup, exact-device checks,
process-group termination and independent owned-container cleanup are reused.

The experiment uses schema `nib.deepseek-lab-tokenizer-parity/v1`. Its
`functional_pass=true` means only these four parity checks completed and the
engine stopped; require the outer cleanup confirmation too. It does not mean
the usual eight correctness cases ran. The enclosing lab remains `BLOCKED`,
`result=fail`, non-deployable and non-confidential.

## Evidence boundary

The authorized eight-B300 run on 2026-10-22 passed text, system and tools
comparisons, then failed at the tool-continuation `/tokenize` protocol step.
The engine stopped and owned-container cleanup was confirmed. See the
[original evidence and limits](p2-t3-replacement-parity-evidence.md).
No all-case parity pass is claimed. Unit tests use synthetic interfaces and do
not execute the real tokenizer or engine.

Even a hardware pass proves only four fixed non-thinking text/tool probes on
one selected runtime. It is not universal tokenizer equivalence, an independent
tokenizer implementation, multimodal/thinking coverage, a live tau accounting
adapter, quality acceptance, native/JIT provenance or confidential qualification.
The two invocations deliberately share pinned tokenizer code and model files;
this tests runtime/API framing parity, not the correctness of that code itself.

Focused checks:

```sh
python3 -B -m unittest discover -s scripts -p test_engine_tokenizer_parity.py
python3 -B -m unittest discover -s scripts -p test_deepseek_lab.py
python3 -B -m unittest discover -s scripts -p test_engine_local_correctness.py
```
