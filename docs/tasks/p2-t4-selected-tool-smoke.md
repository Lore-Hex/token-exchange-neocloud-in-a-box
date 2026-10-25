# Owned selected tool round-trip smoke

Implemented and tested with synthetic owned transports only. No model run,
hardware qualification, tau result, tool-capability promotion, public service or
confidential-computing claim. The catalog and all receipt capability/privacy
flags remain false. This builds on the
[selected native tool API prerequisite](p2-t4-selected-service-tools.md).

## Closed sequence

The existing private host owner and canonical container entrypoint accept one
new mutually exclusive `--tool-smoke` option. Default, inference, conformance and
protocol modes are unchanged. No configurable URL, prompt, function, result,
callback or executable is added. The image still runs nonroot with no network
or published port; the probes use its owned loopback TLS front door.

1. Finish the existing three inference/auth checks. A failed prerequisite stops
   before any tool request.
2. Send one fixed streamed request asking for `lookup_synthetic_value` with
   `{"key":"sample"}`. Only `auto` choice and explicit no-parallel calls are
   requested. Assemble one native call at index zero, including fragmented name
   and argument strings, a bounded ASCII call ID and separate reasoning.
3. Require complete framing, native `tool_calls` finish, known usage, DONE and
   HTTP EOF. Reject output at the 256-token cap, even when its argument JSON is
   valid. Require exactly the fixed function and argument object: no repair,
   schema coercion, extra keys, duplicate keys or arbitrary execution.
4. Only then send the original assembled assistant call and its matching ID
   with the fixed synthetic result `{"value":"NIB_TOOL_OK"}`. Tool choice is
   now `none`; the response must contain exactly that value after whitespace
   trimming, native `stop`, known usage below the cap, DONE and EOF. Another
   call, reasoning-only response or truncated completion fails.
5. Retire TLS, guard and engine through the existing front-first cleanup, even
   on error. This is ordinary post-request retirement, **not** an active-stream
   drain test. Unreturned/invalid helper reports or unclosed resources cannot
   confirm cleanup. The host also requires its existing container/watchdog joins.

This is at most five HTTP requests total (three authenticated), no retries and
one in-flight request. Both tool legs request native `reasoning_effort: "none"`,
temperature zero and 256 output tokens. Fixed inputs do not promise a model will
call the tool or return the expected answer; a deviation fails without retry.
Reasoning is never mixed into content or arguments; if returned, it is bounded
and forwarded only as native assistant history, never retained in the receipt.

## Reused boundaries

`selected_service_conformance.response` owns authenticated TLS, peer identity,
fixed route and socket deadlines; no checker wheel or independent HTTP client
is used. `stream_transport.StreamResponse` and `response_length` retain strict
chunk framing/status/EOF checks. The new narrow delta collector inherits
`StreamMeasurement` SSE framing, timing and size bounds. Tool-specific usage
validation mirrors the legacy integer arithmetic locally; the source-bound
legacy collector, transport and measurement bytes remain unchanged. Existing
text-only parsing still rejects tools. Native response bytes/finish reasons are
not rewritten.

The tool phase is bounded by the minimum of 250 seconds and the original owner
cutoff minus its cleanup reserve. Each request also retains the existing
120-second absolute bound. Startup does not refresh the owner expiry. Response
bytes are limited to 1 MiB per leg, events to 64 KiB, arguments/reasoning/content
to 32 KiB each, function name to 64 bytes and call ID to 128 ASCII characters.
The continuation must fit the existing 64 KiB API body. Engine context/output/
admission limits remain 8192/256/1; this is not a new token estimator.

The closed `nib.selected-service-tool/v1` receipt binds contract, prepared source
and successful prerequisite hashes. It retains per-leg attempts, check results,
original observed finish, integer usage/unknown-usage and resource closure only.
Known usage survives malformed final arguments, cap rejection, late identity or
deadline failures and failed teardown; unobserved usage is never zero-filled.
There are no tool names, IDs, argument/content/reasoning strings, exception text,
private paths or credentials in the report. Lifecycle/host v5 mode joins cannot
reinterpret older receipts. All claim flags stay false even for synthetic PASS.

## Primary source and limits

The existing pinned vLLM revision is
`5df63a7203cdb9328d824a27b682e77c7b9928be`; no parser flags or engine changes are
introduced. The exact local source was inspected:

- [Chat serving, stream generator](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/serving.py#L752)
  emits native deltas, tool-call finish on normal tool stop, terminal usage and
  DONE. Native length remains a failure here; cap saturation is also rejected
  rather than relying solely on finish spelling.
- [V4 parser](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/parser/deepseek_v4.py)
  and [V4.1 parser](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/parser/deepseek_v41.py)
  supply the already configured native tool/reasoning path.
- [Native history encoding](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/tokenizers/deepseek_v41_encoding.py#L62)
  consumes function arguments and joins tool results to assistant call IDs.
  This smoke does not generate DSML or bypass the API validator.

Focused tests exercise real HTTP/SSE decoding on owned fake sockets, every split
point, malformed and oversized deltas, parallel/duplicate IDs, invalid argument
JSON, cap saturation and native length on both legs, finish/usage/DONE/EOF,
reasoning separation, identity/deadline/cancellation, private canaries, source
binding, once-only execution, host nonzero-exit accounting and front-first
cleanup. No listeners, Docker, GPU or full suite are required by these tests.

A fresh reviewed source preparation/image and separately authorized bounded run
are still required. Even a future single synthetic PASS would not establish
general tool reliability, full quality, a qualified simulator, active-stream
drain, or confidential serving. Existing failed evidence is not reinterpreted.
