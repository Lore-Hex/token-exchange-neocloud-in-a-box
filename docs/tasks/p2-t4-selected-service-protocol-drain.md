# Selected-service protocol and active drain

`--protocol-smoke` adds one explicit, bounded sequence on the same private
selected engine, guard and TLS owner. It is mutually exclusive with the existing
inference and conformance modes, whose behavior is unchanged. No hardware result,
public service, quality qualification or confidential capability is claimed.

## Pinned assertions, unchanged statuses

The [helper](../../scripts/selected_service_protocol.py) reuses the
[prepared checker payload and owned transport](p2-t4-selected-service-conformance.md).
Primary source is the unchanged `trustedrouter-provider-check==0.1.8` wheel,
SHA-256 `591a11295d3f1e0ef20fbc51c788cabfbedf176e79ce56de8378252bd13adecd`:
`checks/catalog.py::run_catalog_checks`,
`checks/callability.py::run_callability_checks`,
`checks/chat.py::run_chat_checks`, `checks/streaming.py::run_streaming_checks`,
and `http.py::GatewayClient.chat_body`. No protocol parser or checker assertion
is reimplemented. The existing strict HTTP/SSE parser additionally bounds and
settles the streaming observations.

The sequence is closed, with no retries or configurable URL:

1. Run the three existing inference cases unchanged.
2. Run the existing four-request subset: two streaming authentication negatives,
   owned catalog validation and the seven pinned streaming assertions.
3. Read the native catalog and run the pinned one-model callability and chat
   checks through the same owned TLS transport. These eight JSON requests cover
   callability, PONG/default response, temperature zero, four forwarded-field
   probes and explicit max-token spelling. Request defaults, omissions and
   assertion statuses are unchanged. The selected guard retains its existing
   256-token ceiling; the adapter does not insert a cap where the checker omits it.
4. Start one fixed synthetic long stream; after first visible content, request
   whole-owner retirement and read that same connection to completion.

The drain-only request explicitly uses `reasoning_effort: "none"`, bound in
protocol contract v2. The selected guard forwards this native chat-mode setting
as described in the [source-backed reasoning controls](p2-t4-selected-reasoning-controls.md).
The eight upstream JSON probes and seven subset requests are unchanged. The
original 676c2a7 run passed all 17 checker assertions but reached 256 completion
tokens before an eligible first-content retirement point. Its receipt does not
record reasoning-token counts or transport chunk boundaries, so default reasoning
is a possible cause, not a proven diagnosis. Requesting chat mode makes this
shutdown probe target visible content without increasing its cap or accepting a
completed/buffered response as an active stream. A reasoning-only or already
finished stream still fails and preserves known usage. This change has no new
hardware result; the original failed receipt remains unchanged.

This is at most 17 HTTP requests total, 11 authenticated, counting the existing
three inference requests. The projected checks are the existing 17-ID mandatory
set. The optional remote declared-catalog request is not made: the owned catalog
was already checked with the pinned offline rules. No schema fetch, separate
checker HTTP client, provider retry or model alias rewrite is introduced.

`checks` retains each upstream `pass`, `fail`, `warn` or `skip` exactly.
`checker_result` summarizes them with fail/warn/skip precedence; `all_17_pass`
is true only for 17 passes. The initial selected API rejected reasoning
hints; the [native-control follow-up](p2-t4-selected-reasoning-controls.md)
changes that boundary but does not rewrite older receipts. Any
`chat.forwarded-fields` WARN still remains visible. **That WARN still permits the drain
observation, but cannot produce overall PASS.** The receipt can therefore show
checker WARN, completed drain and confirmed cleanup together without discarding
the useful observations or claiming full conformance. All unknown accounting
also prevents overall PASS, even if the checker does not inspect that request's
usage. Tools, reasoning support and quality remain separate work.

## Explicit retirement phase

The [owner](../../scripts/selected_service.py) changes from ready to draining
only after the hook has observed first content and started its single reader.
As in [Qwen's shutdown-during-stream proof](../../scripts/pilot/run.py), it then
retires the entire service front-first. TLS TERM is sent before the reader is
released; guard and engine remain owned until TLS drain/reap and reader join.
Only the owner signals its children. The reader cannot open another connection,
signal a process or restart a request.

All ordinary identity, mount, private-input and deadline checkpoints remain
active before the explicit transition. A retired TLS listener is not fabricated
as live afterward. The established reader uses its existing authenticated socket,
strict framing, positive usage, finish, DONE and HTTP EOF. If the first read
already contains termination, the active-drain observation fails rather than
passing on a previously completed stream. This proves the client-observed
first-content/retirement ordering, not a separate server-side active-request
counter or a sustained-load guarantee.

External cancellation is separate from intentional retirement. SIGINT/SIGTERM
remains latched, forces overall failure, and is never cleared to continue work.
The main-thread hook alarm is fully unwound before the reader starts. The
[shared peer proof](../../scripts/selected_service_inference.py) is reused without
retaining a main-thread signal context across the drain phase; ordinary exchanges
retain their original enclosing alarm.

## Bounds and closed joins

After the unchanged subset, checker plus drain has at most 480 seconds, always
clipped to the original absolute cutoff minus the unchanged 375-second cleanup
reserve. Authenticated requests and the entire drain stream have at most 120
seconds each. Catalog responses are bounded to 64 KiB; JSON/stream responses to
1 MiB; SSE events retain the existing 64 KiB bound. There is no deadline renewal.
The main owner also bounds TLS drain/wait with that same absolute work deadline;
failure interrupts only the owned reader socket before process reaping. This
also bounds slow partial HTTP reads that cannot rely on an idle socket timeout.

Reader join has two waits of at most five seconds, clipped to the original hard
cutoff. Between them, only the already-owned socket may be shut down. A reader
that remains alive or a failed resource close makes cleanup unconfirmed. Thread
observations never mutate an already-finalized receipt. Known integer usage
survives later framing, identity, cancellation or cleanup failure; missing usage
stays explicitly unknown, not zero.

The opt-in owner/host envelopes use v4 and `mode: protocol-smoke`. The nested
report binds the prepared source hash, fixed contract, prior inference and
subset. Closed statuses, flags and token counts are the only observations
retained. Raw responses, prompts, headers, upstream diagnostics, exception
chains, paths, addresses and credentials are not projected. Every deployment,
provider-conformance, hardware-validation, streaming-capability, tool/reasoning,
quality and privacy claim flag remains false.

## Verification and remaining gates

[Focused tests](../../scripts/test_selected_service_protocol.py) execute the
real pinned assertions against synthetic HTTP wires and exercise the actual
owner/helper/host joins. They cover all-pass and WARN with successful drain,
missing accounting, external cancellation, missing finish/DONE/EOF, buffered
termination, absolute deadlines, thread-start/close/join failures, source
tampering and private canaries. They create no listener or hardware process.
Run with the existing 17-package hash-locked native checker environment:

```sh
PYTHONPATH=scripts python -B -W error::ResourceWarning -m unittest -q test_selected_service_protocol test_selected_service_conformance test_selected_service_inference test_selected_service_hook test_selected_service_image test_selected_service test_selected_service_process test_selected_service_smoke
```

A fresh optional checker preparation, exact image rebuild, independent final-head
review and green full gates are required before a separately authorized private
run. The old subset-only image is not silently upgraded. No hardware operation
or public endpoint is authorized by this implementation.

Checker-bearing Dockerfiles now run a GPU-free import check after sealing, under
`USER 65532:65532`, `--network=none` and the exact image interpreter's `-I -S -B`.
It revalidates the complete prepared inventory, imports all checker tiers plus
native Brotli/rpds, and exercises their fixed in-memory operations under a
30-second alarm. A missing module or ABI mismatch fails the build before any
GPU load. Upstream output is suppressed. This is not a host-Python compatibility
substitute or runtime proof. The no-checker Dockerfile is byte-identical; the
optional generated Dockerfile remains joined by the preparation validator.
