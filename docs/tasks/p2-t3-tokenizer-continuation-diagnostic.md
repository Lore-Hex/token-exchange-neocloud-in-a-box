# CPU Tool-Continuation Diagnostic

This opt-in extension of the [CPU entry harness](p2-t3-cpu-entry.md) investigates
the first `/tokenize` failure for the unchanged
[parity continuation](p2-t3-tokenizer-parity.md) at producer
`6fd01351158e3a2f0ff66d44abd0a6b2c3379ccc`. The GPU receipt's `protocol` label
and null server measurement do not identify its exception or HTTP status.
Historical artifacts and all default lab commands remain unchanged.

## Pinned Path

Primary vLLM source revision: `5df63a7203cdb9328d824a27b682e77c7b9928be`.

- [TokenizeChatRequest](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/serve/tokenize/protocol.py)
  validates `messages` through the generic chat message union.
- [ChatCompletionRequest](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/protocol.py#L583)
  has `_materialize_tool_calls_after`, explicitly converting Pydantic's
  `ValidatorIterator` back into lists. The tokenize request lacks this
  after-validator. The diagnostic additionally requires the same continuation
  to normalize through the actual chat-completion request model.
- [Chat parsing](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/chat_utils.py)
  materializes assistant `tool_calls` into a separate conversation and parses
  function arguments into dictionaries.
- [DeepseekV4Renderer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/renderers/deepseek_v4.py)
  passes both that conversation and the **original** messages to the tokenizer.
- [DeepSeek normalization](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py)
  deep-copies the original messages before encoding. The
  [encoder](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41_encoding.py)
  accepts both string and dictionary arguments; that conversion alone is not
  the proposed failure mechanism.

The hypothesis is an API-validated lazy tool-call iterator that cannot be
deep-copied, even after the parser has consumed it. The diagnostic uses the
actual selected request class, sync and async generic parsers, normalizer and
encoder from the fully verified applied image. It does not replace those
functions, install packages, modify vendor code, or consume/materialize the
original tool-call iterator as a workaround.

## Bounds And Observations

```sh
python3 -I -S scripts/record-engine-cpu-entry.py \
  --prepare /private/tmp/nib-tokenizer-cpu-payload --tokenizer-continuation
python3 -I -S scripts/record-engine-cpu-entry.py \
  --slug tokenizer-continuation-cpu --tokenizer-continuation
```

The second command is explicit local CPU execution, not a GPU authorization.
It requires the existing offline source inputs and raw OCI metadata in
`build/engine-image-metadata`. The container retains the immutable cached image,
five-stage application and inventories, sealed loader, no network/devices/ports,
all-capability drop, no-new-privileges, 128 PIDs, 2 CPUs, 8 GiB RAM, bounded
noexec tmpfs, 720-second application deadline, 300-second child deadline,
1,080-second attach deadline and independent owned-container removal.

The existing **Native CPU engine entry** manual workflow also accepts
`tokenizer_continuation` (boolean, default false). It remains main-only on the
ephemeral Ubuntu 24.04 hosted runner, with the same image, action pins and
read-only repository permissions. Both the recorder (which prepares its payload
internally) and the final `--check-result` receive the same optional flag.
False produces the original command arguments. Invalid environment values
reject before invoking either operation. Failure artifacts are still uploaded;
a reproduced normalization exception intentionally leaves the final success
check red rather than treating a known failure as successful entry.

Only in this explicitly source-bound mode, checkpoints 12-16 mean:

1. 12: all four unchanged synthetic probes passed request-model construction.
2. 13: the first three normalized; plain continuation and dictionary-argument
   encoding agreed; the chat-completion continuation materialized its tool calls
   and normalized; the tokenize continuation has a non-list iterator.
3. 14: sync/async generic parsers agreed and their conversations normalized,
   including one preserved tool call with dictionary arguments.
4. 15: the original continuation was parsed asynchronously and the sealed state
   check passed; the next operation is normalization of those original messages.
5. 16: that normalization returned. It is not evidence of token parity.

The existing fixed exception packet retains only an allowlisted exception
category and bounded source SHA-256/line coordinates. All ordinary/native output
is discarded before imports. No bodies, exception strings, tokens, rendered
prompts or tool arguments are retained. The normal CPU mode keeps its checkpoint
meanings; receipts in this mode additionally bind `diagnostic=tokenizer-continuation`.

No BPE/model loading, renderer object, HTTP server, inference, usage accounting,
GPU or remote operation occurs. The parser receives a text-only configuration
stand-in with only `enable_prompt_embeds=False`; additional configuration access
fails rather than introducing guessed settings. This exercises the actual
request/parser/normalizer dependency path, not an end-to-end server. A matching
failure can establish a concrete mechanism for these exact inputs, but cannot
recover the unretained historical server exception. No quality, confidentiality
or successful hardware retry is claimed. Evidence envelopes remain `result=fail`.

## Checks

```sh
python3 -B -m unittest discover -s scripts -p test_engine_tokenizer_diagnostic.py
python3 -B -m unittest discover -s scripts -p test_engine_cpu_entry.py
python3 -B -m unittest discover -s scripts -p test_engine_tokenizer_parity.py
```

Unit tests use synthetic interfaces and do not prove the runtime hypothesis.
Actual CPU observations, if recorded, must name their exact producer and evidence.
