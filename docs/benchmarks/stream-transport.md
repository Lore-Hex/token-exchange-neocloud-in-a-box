# Owned Engine Text Streaming Transport

Implementation: [stream_transport.py](../../scripts/stream_transport.py).
Tests: [test_stream_transport.py](../../scripts/test_stream_transport.py).

This is a reusable, single-request HTTP collector, tested with local synthetic
servers. It is **not wired into the lab controller**, has not made a model request
and does not complete P2.T4. No throughput, quality, deployment or confidential
qualification is claimed. It consumes the existing
[stream measurement component](stream-measurement.md) without changing it.

## Entry Contract

The owning lab controller must first authenticate its source/model/runtime,
complete the existing exact tokenizer parity gates, and construct the existing
`tau_live_identity.Endpoint` for that live process. It supplies the locally
measured prompt count and retains ownership of process startup, whole-cohort
budgets, evidence and cleanup. A historical observation or an endpoint model
name alone is not admission.

`OwnedStreamHTTP(endpoint).collect(messages, prompt_tokens=...,`
`max_output_tokens=..., deadline=...)` returns an immutable `Sample`, never a
response body. Invalid caller arguments are rejected before opening a socket.
Once collection begins, errors become fixed failure categories; valid reported
usage survives a later framing, identity, timeout or transport failure.

- Only numeric `127.0.0.1:8000` and model `deepseek-v4.1-flash-1017` are accepted.
  There is no arbitrary URL, host, port, path, model, credential or guard callback.
- The collector must run in the owner's existing UID and PID/network namespace,
  where the existing `Endpoint.check` can verify process start/boot, executable,
  argv, loopback listener and the established peer socket before sending HTTP.
  It checks identity before connection and again after completion. It does not
  share a scorer PID namespace, run a scorer as root, add capabilities, alter
  network isolation or grant access to `/proc`.
- One user message, optionally preceded by one system message; no tools or
  arbitrary request options. `n=1`, nonthinking, temperature zero, streaming
  usage requested. No retries or proxy/DNS lookup. Only transient request/SSE
  buffers exist; there are no content logs, response files or content labels.
- Existing lab context stays 4096 tokens. Prompt plus requested output must fit;
  output is at most the existing correctness limit of 512 tokens. The caller's
  deadline is clipped to the existing correctness request limit of 60 seconds,
  not the generic lab HTTP limit of 120 seconds. Final prompt usage must equal
  the supplied locally measured count, and output usage must fit the request.
- Main thread only, one request per instance. `worker.alarm` covers identity,
  connect, headers, all incremental reads and EOF, including slow byte drips.
  Socket/response cleanup and the existing outer signal timer are preserved.
  The owner still needs a separate bounded engine lifecycle and cohort budget.

Arrival times are client-observed: the request timer includes live identity and
connection checks before the first event. Total elapsed time includes EOF and
the final identity recheck, not just engine decoding time.

No UDS path is introduced: the reviewed engine currently listens on loopback.
Concurrent scheduling, other models, long-context profiles, tools and a controller
entrypoint require separate review. This increment changes no controller,
launcher, source admission, benchmark limits or default execution path.

## HTTP Boundary

`HTTPResponse.read1` supplies available bytes without waiting for an entire HTTP
chunk or buffer. Each read is capped at 64 KiB and timestamped before parsing.
Existing event/stream limits remain 64 KiB/64 MiB. Only status 200, identity
content encoding and UTF-8 `text/event-stream` are accepted. Duplicate framing
headers, conflicting length/transfer encoding, other transfer encodings and
folded header values fail. Standard-library parsing first bounds each header line
to 64 KiB and header count to 100; accepted headers are additionally restricted to
32 fields and 64 KiB total. Redirects/errors are not followed or consumed as SSE.

The small `HTTPResponse` subclass retains standard-library decoding but requires
CRLF around chunks, bounded nonnegative hexadecimal sizes, no chunk extensions
and no trailers. It also retains the buffered tail until actual connection EOF,
rejecting bytes past declared Content-Length or the zero chunk. EOF, final usage
and `[DONE]` are all required before success; DONE does not stop the read loop.
A peer that ignores `Connection: close` fails at the whole deadline. Missing
usage remains unknown, never reconstructed by tokenizing output or assuming zero.

## Source Audit

These are source checks, not observations of a new installed runtime. Existing
source/runtime admission is still required when the controller integrates this
collector. No vendor module is imported by the tests.

- CPython [3.12.3](https://github.com/python/cpython/blob/v3.12.3/Lib/http/client.py)
  (`a353716a8506e65ae9ac9f06581ad17397e2f2a2`) is the upstream version corresponding
  to the lab's required Python version;
  [3.12.13](https://github.com/python/cpython/blob/v3.12.13/Lib/http/client.py)
  (`70451d67d4cd48814f544fbe95c55633a39869e1`) is the local test interpreter version.
  The audited `read1`, `_read1_chunked`, `_get_chunk_left` and trailer behavior
  agree. Both silently return EOF on a short Content-Length, discard unchecked
  interchunk CRLF, and accept an absent final trailer CRLF by default; the collector
  adds explicit checks. Distribution patches are not proven by version alone.
- vLLM revision `5df63a7203cdb9328d824a27b682e77c7b9928be`:
  [chat serving](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/serving.py)
  (`b291ec6e8aae7cb92cc10dca01b518fe29d9e01e`) emits initial role/content,
  text and finish chunks using `exclude_unset=True`; final usage additionally
  uses `exclude_none=True`, followed by DONE. The
  [parser engine](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/parser/engine/parser_engine.py)
  (`c74be931d2569b1792531228e4bec395210c3a3e`) sets content/reasoning fields only
  when present, and tool_calls only for actual tool deltas. The source-shaped
  fixture includes null top-level/choice metadata, empty initial content, finish,
  usage details and DONE; null tool/refusal fields inside delta still fail.
  The [API route](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/entrypoints/openai/chat_completion/api_router.py)
  (`aecf5bba448340cde72a369e571159937a664009`) selects `StreamingResponse` with
  `text/event-stream` media type.
- The [original image inventory](../../evidence/p0/2026-10-19-official-vllm-image-diagnostic/output.log)
  records Starlette 1.8.0 and Uvicorn 0.54.0.
  [Starlette responses](https://github.com/Kludex/starlette/blob/1.8.0/starlette/responses.py)
  (`52320dd1ca2a5385e0cf3b712c30e7d29ef6480d`) adds UTF-8 charset and ends the
  ASGI stream with `more_body=False`.
  [Uvicorn's httptools HTTP implementation](https://github.com/Kludex/uvicorn/blob/0.54.0/uvicorn/protocols/http/httptools_impl.py)
  (`dc95e23d6485853562f1fbdd5851b11758f8294f`) writes ordinary hexadecimal chunks,
  a terminal `0\r\n\r\n` and closes non-keepalive connections. These source shapes
  justify the narrow decoder; a differing installed protocol fails closed.

## Verification

`PYTHONPATH=scripts python -m unittest test_stream_transport test_stream_measurement`
runs only synthetic local HTTP. Fixed port 8000 must be free; an occupied port
produces an explicit skip rather than contacting an existing engine. Tests mock
the existing endpoint guard, not a production bypass, and do not prove Linux
ownership or admit any runtime. Existing identity tests cover that guard separately.

Coverage includes byte-fragmented output within a single HTTP chunk (the server
waits until the collector sees the first event), close-delimited/chunked/length
bodies, source-shaped text deltas, wrong model/count, missing usage/DONE,
duplicate/conflicting headers, invalid/oversized chunks, missing/bad CRLF, trailers,
post-terminal excess, slow headers/body/chunk framing/EOF, known usage on failures,
identity drift before body and after completion, cancellation, fixed-endpoint and
request bounds, lock/socket cleanup, outer alarm restoration and content canaries.
