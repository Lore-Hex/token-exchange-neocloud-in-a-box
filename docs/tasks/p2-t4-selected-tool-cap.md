# Selected Tau Tool-Response Cap

The selected operational candidate rejects a `tool_calls` response whose
validated completion usage reaches its frozen 256-token cap. The check runs
after settlement: measured prompt/completion usage remains known, no retry or
tool execution follows, no reward is accepted, and the existing owner finish
and teardown paths remain responsible for cleanup. No context, quota, generation
setting, parser or image changes are made.

## Pinned Cause

At vLLM `5df63a7203cdb9328d824a27b682e77c7b9928be`, the
[non-streaming response builder](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/serving.py#L1099-L1119)
sets `finish_reason="tool_calls"` whenever automatic tool calls were parsed,
including when the engine's original finish reason was `length`. The
[parser result builder](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/parser/engine/parser_engine.py#L1096-L1146)
can retain a named tool from an unfinished generation. Therefore valid JSON
arguments and a `tool_calls` finish reason do not establish complete generation.
The existing source pin for `serving.py` is
`5e619c0a6866758a247c76e7b8c472f6e008ddbc720f7efe47cc6776cb986d4f`.

## Failure Meaning

Cap saturation is ambiguous: a natural completion exactly at the cap is also
rejected. It reports the existing `protocol_error` status, not a claim that
native truncation was observed. Explicit `length` still reports `truncated`.
The schema has no finer failure-detail field; neither raw output nor a guessed
native finish reason is added. Below-cap candidate tools, candidate text ending
in `stop`, CPU roles and legacy/probe modes retain their existing behavior.
Malformed or out-of-budget usage remains unknown and reserved, as before.

This is a selected-mode safeguard, not a broad parser correction or proof that
retail task 2 fits its 8K candidate and 4K CPU contexts. No hardware run, model
quality, matched-reference result or confidentiality is claimed.

## Verification

Committed synthetic-response regressions in `test_tau_selected_session.py` and
`test_tau_selected_driver.py` cover cap/below-cap boundaries, role and mode
separation, explicit-length distinction, invalid usage, sticky failure,
non-delivery to the scorer, real subprocess-pipe cleanup, owner finish joins
and reward suppression. Run with `PYTHONPATH=scripts python3 -B -m unittest
test_tau_selected_session test_tau_selected_driver`.

Full checks, hosted CI and independent final-head review remain required before
merge. No full suite or hardware is launched by this task.
