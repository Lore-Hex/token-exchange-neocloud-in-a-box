# Selected native tool API prerequisite

Implemented and tested with synthetic mock transport only. This is the first,
API-only increment toward selected-service tool round trips, not a new live run
mode, successful model tool call, tau result, or tool-capability qualification.
The selected catalog still declares `tools=false` and `reasoning=false`. Privacy,
confidentiality, deployment and public-serving claims remain unchanged.

## Existing pinned engine support

No engine flags or launcher bytes change. The committed selected non-owner
`tau8k-eager` contract already supplies `--tool-call-parser deepseek_v41`,
`--enable-auto-tool-choice`, `--reasoning-parser deepseek_v41`, and
`--tokenizer-mode deepseek_v41`. The new regression builds that contract through
the existing lab/profile helpers and checks those exact arguments. Canonical
model identity, 8192 context, 256 output tokens, one admitted request, DP2/TP4,
owned loopback upstream and existing deadlines remain unchanged.

Primary source is vLLM revision
`5df63a7203cdb9328d824a27b682e77c7b9928be`. The following local source files were
compared byte-for-byte against the official exact-revision raw files during
implementation; Git blob IDs record the inspected source, not a hardware proof:

| Source | Verified Git blob |
| --- | --- |
| [Tool registry](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tool_parsers/__init__.py) | `4b9cb33502e5be21bf28313d095104046921f6e5` |
| [V4.1 parser](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/parser/deepseek_v41.py) | `84216005102aa0af5843bd5156f656e07d228be1` |
| [Underlying parser](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/parser/deepseek_v4.py) | `49ef63736531d05210bb6e214120f2f7c36bd159` |
| [Chat protocol](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/protocol.py) | `79dd9605e66906ce36c6692ee8b4ab936ec5e905` |
| [Chat serving](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/serving.py) | `b291ec6e8aae7cb92cc10dca01b518fe29d9e01e` |
| [Tokenizer normalization](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py) | `7ff1f5cc3b83b7c55958120c9bf1aa30dddb64e5` |
| [Tool/history encoder](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41_encoding.py) | `6851d42f91f55acba5af8ea2ad36946fdfff68f5` |

The native tokenizer consumes declarations, assistant `tool_calls`, result
`tool_call_id`, and separate `reasoning`; it encodes tool results itself. The
API does not generate DSML, repair argument JSON, execute a tool, or reconstruct
model output. Required/named choices and strict structured-output generation
are intentionally outside this increment.

## Closed request boundary

Only `selected-deepseek` enables the new validation. Qwen retains its original
message/options path, catalog bytes, request byte fixtures and tool rejection.
Shared authentication, retirement, admission, sampling/output limits, transport
and reverse-proxy response handling are not duplicated or changed.

- At most 16 distinct function declarations with names matching
  `[A-Za-z_][A-Za-z0-9_-]{0,63}`; required `name` and object-typed `parameters`,
  optional string `description` of at most 4096 bytes. No other function fields.
- Parameter schemas are bounded JSON data passed unchanged to the native
  encoder. This is not a general JSON-Schema validator, argument-to-schema
  checker, or a guarantee that generated arguments satisfy the declaration.
- Explicit `tool_choice` is only `auto` or `none`, with declarations required.
  Explicit `parallel_tool_calls` is only false. Absent fields remain absent;
  native defaults are not silently replaced. Up to 16 historical calls per
  assistant message are accepted, regardless of the next turn's choice.
- Assistant calls require declared functions, unique bounded ASCII IDs,
  exact function fields, and argument strings containing a single JSON object
  of at most 32768 bytes. Malformed, duplicate-key, array, null, double-encoded,
  or nonfinite parsed-number arguments are rejected, never repaired.
- Tool results must be text and match a pending call exactly once. All pending
  calls must resolve before a non-tool message or end of request. IDs cannot
  be reused across turns. No tool execution occurs in the API.
- `content=null` is permitted only for an assistant with nonempty calls.
  Optional assistant `reasoning` is a separate string of at most 32768 bytes;
  it is forwarded unchanged, not folded into arguments/content. Alternate
  history aliases and unknown message fields are rejected.
- The existing 64 KiB HTTP body and 128-message limits remain. Selected JSON
  additionally rejects duplicate keys, invalid UTF-8, trailing documents and
  nesting beyond 32 levels. This bounded parsing is not a token-count estimate;
  the owned engine still enforces its 8192-token context.

## Verification and next increment

Focused tests exercise both JSON and fragmented tool-delta SSE bytes through
the same authenticated handler and fixed upstream, then a matching tool-result
continuation. They check separate reasoning, unchanged native finish/usage
bytes, invalid histories/options, private error canaries, unchanged Qwen
goldens and still-false capability declarations. These are synthetic responses,
not model inference or TLS/container lifecycle evidence.

The next separately reviewed owner smoke mode must reuse the existing owned
TLS transport, deadline/accounting and cleanup joins. It should issue exactly
one deterministic synthetic function request and one continuation, without
retries or a new endpoint. It must assemble deltas by call index, validate the
complete JSON object/name/ID, keep reasoning separate, and require finish,
usage, DONE and EOF. Only then may it feed the fixed synthetic result back to
the same owned front door. Unknown usage or cleanup remains failure.

The pinned non-streaming serving path can emit `tool_calls` after native length
termination. As in the existing selected tau guard, a tool-call completion at
the reserved output cap must be rejected as ambiguous, even with valid JSON;
native `length` must fail as truncated. The follow-up must preserve settled
usage and the original finish, not relabel either as success. This API-only PR
does **not** implement response qualification or stream assembly: malformed or
capped engine replies remain byte-preserved and unqualified. No caller should
treat API admission as authorization to execute model-generated tools.

No full suite, live run, image build or hardware action was performed for this
increment. Independent final-head review and software gates remain required.
