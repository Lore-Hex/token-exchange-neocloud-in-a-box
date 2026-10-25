# Selected reasoning controls

This is an API-boundary change, not a model-quality or hardware result. The
selected DeepSeek profile forwards a validated native `reasoning_effort` instead
of rejecting every reasoning option. Qwen's existing compatibility behavior and
wire fixtures remain unchanged.

## Pinned engine semantics

The current pinned vLLM revision is
`5df63a7203cdb9328d824a27b682e77c7b9928be`:

- [Chat request parameters](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/protocol.py#L621-L647)
  forward `reasoning_effort` to the tokenizer and enable thinking unless it is
  `none`. Verified upstream Git blob: `79dd9605e66906ce36c6692ee8b4ab936ec5e905`.
- [DeepSeek V4.1 tokenizer](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41.py#L70-L92)
  makes `none` disable thinking and accepts `low`, `high`, `xhigh` and `max`.
  Verified upstream Git blob: `7ff1f5cc3b83b7c55958120c9bf1aa30dddb64e5`.
- [Effort encoding](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41_encoding.py#L183-L210)
  maps `low` to 50, `high` and `xhigh` to 75, and `max` to 100. These are engine
  prompt controls, not measured token budgets or distinct quality guarantees.
  In particular, `high` and `xhigh` are equivalent here.
  Verified upstream Git blob: `6851d42f91f55acba5af8ea2ad36946fdfff68f5`.

The API accepts exactly one of:

- `reasoning_effort`: `none`, `low`, `high`, `xhigh`, or `max`;
- `reasoning: {"effort": "..."}` with that same set;
- `thinking: {"type": "disabled"}`, normalized to native effort `none`.

Only the native top-level field reaches the owned engine. Multiple forms in
one request are rejected, even if apparently compatible; no map-iteration
precedence chooses between conflicting instructions. Null, booleans, numbers,
unknown levels, extra nested fields, native template overrides and arbitrary
token budgets are rejected. An absent option remains absent, preserving the
engine default. The existing 256 output-token limit includes generated
reasoning; it is not increased. Reasoning can consume that limit without a
visible answer, so this remains a bounded test profile.

## Validation boundary

Unit and mock-transport tests assert normalization, actual upstream forwarding,
rejected ambiguity, unchanged model/output bounds, exact usage pass-through,
and unchanged Qwen behavior. The catalog reasoning capability remains false
until a separate live test verifies the serving path. Existing historical
protocol receipts and their WARN results are not rewritten. A fresh image and
bounded live run are still required; this change does not qualify tools,
quality, public deployment, ZDR or confidential computing.
