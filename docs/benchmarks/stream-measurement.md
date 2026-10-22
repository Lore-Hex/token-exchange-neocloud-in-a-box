# Stream Measurements

Implementation: [stream_measurement.py](../../scripts/stream_measurement.py).
Tests: [test_stream_measurement.py](../../scripts/test_stream_measurement.py).
This is a tested measurement component, **not a hardware benchmark result** or
a complete live benchmark runner. It opens no connection and writes no file.

## Measurement Contract

For one `n=1` text chat request, the controller records monotonic elapsed time
from request dispatch and passes transient response bytes to `feed(bytes,
elapsed_seconds)`. It calls `close(elapsed_seconds)` at EOF, or supplies a fixed
failure category when the transport fails. No retry or reconnection is allowed
inside a measured request. The collector accepts CR, LF and CRLF line endings,
split UTF-8 and multiline `data` events; comments are discarded. This strict
text benchmark subset rejects named events, tools and refusals rather than
silently treating them as generated text.

Success requires all of the following:

- Exact configured model identity and one choice with integer index zero.
- At least one nonempty content or reasoning delta. Role-only deltas do not count.
- A terminal `stop` or `length`, final nonnegative integer usage within the
  configured prompt/output limits, exact input-plus-output total, and `[DONE]`.
- No conflicting terminal, duplicate JSON key, trailing data, premature EOF,
  invalid clock, expired deadline or oversized stream/event. `length` is a
  valid throughput sample, not proof of a complete or correct answer.

TTFT measures arrival of the first complete nonempty content/reasoning event.
First visible content is a separate metric, absent for reasoning-only output.
Role, finish, usage and DONE events never advance the output clock.

`mean_output_token_interval_estimate_seconds` uses
`(last_output_arrival - first_output_arrival) / (completion_tokens - 1)`.
It is an **estimate** because an SSE event can contain several tokens and
network buffering can coalesce events. It is null for a single token, a single
output event or equal first/last arrival times. There is no invented
instantaneous token rate or per-token timestamp series.

The collector preserves reported cache reads when present, including zero;
absence remains null. Prefix reuse and a successful warmup do not prove a cache
hit. Missing usage never triggers local retokenization or a one-token fallback.
Valid final usage survives a subsequent failure so failed work is not hidden,
but failed responses do not enter successful-output throughput or latency
percentiles. Unknown usage is counted explicitly, not declared free.

The cohort summary uses all measured wall time, including failures and drains:
successful output tokens / wall seconds / allocated GPU count. It preserves
scheduled, started, completed, failed and not-run counts, error rate among
started requests, and noncompletion rate among scheduled requests. Latency
percentiles report their sample counts and are null if no eligible observations
exist. Percentiles use linear interpolation at `(n-1)*p`. Warmups and readiness
calls require separate request/token accounting, outside the measured cohort.

## Bounds And Remaining Integration

The caller sets timeout, prompt and output limits before dispatch. Maximums are
1,800 seconds, 1,048,576 input tokens and 131,072 output tokens. Parsing is bounded
to 64 KiB per input read or event and 64 MiB per response. The collector retains
only an unfinished event transiently; completed output, errors and comments are
not accumulated or emitted. Failure categories contain no upstream text.

The future controller must still verify HTTP status/media type, authenticate the
owned engine and exact runtime/checkpoint, validate tokenizer/workload identity,
enforce real socket and cohort deadlines, freeze concurrency and token budgets,
exclude environment proxies, and perform verified cleanup. Passing bytes to this
component supplies none of those guarantees. In-process samples are not an
untrusted evidence format or a substitute for original source-bound receipts.

The existing 4,096-context, one-sequence-per-DP-rank lab cannot run the planned
32K/128K/concurrency-256 sweep unchanged. Larger profiles require separate
source-backed resource review and live validation; no limits changed here.

## Source Review

Read October 22, 2026, at the lab's pinned vLLM revision
`5df63a7203cdb9328d824a27b682e77c7b9928be`:

- [Chat benchmark client](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/benchmarks/lib/endpoint_request_func.py):
  lines 376-473 treat any nonempty choices list as a token event and do not
  require terminal usage/DONE. Git blob `778a4e50d334752bff5be389e60899ad7fba3c8b`.
- [Metric calculation](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/benchmarks/serve.py):
  lines 574-783 fall back to retokenization/one token and zero-valued missing
  latency. The CLI can print raw request errors. Git blob
  `01a0f62aeee2569597696745073a3552d8dc7c3c`.
- [WHATWG SSE parsing](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation):
  UTF-8, CR/LF/CRLF, multiline data and no partial-event dispatch at EOF.

These are source observations, not proof of installed image file identity or
measured hardware behavior. The ordinary upstream benchmark CLI is not invoked
by this component.
