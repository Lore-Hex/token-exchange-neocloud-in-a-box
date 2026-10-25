# Selected-service live protocol subset

This is an explicit, bounded software hook after the existing
[inference smoke](p2-t4-selected-service-inference.md) passes. It uses the same
owned engine, guard, TLS listener, private input snapshots and original absolute
cutoff. It adds no listener, configurable URL, engine flag or retry. No hardware
result, public service, full provider gate, quality or privacy claim is made.

## Reuse and scope

The lab's [provider session](../../scripts/provider_conformance_session.py) runs
an isolated Python 3.12.13 checker against its bare HTTP engine. The
[Qwen proof](../../scripts/pilot/run.py) invokes the checker CLI over TLS with
catalog checks. Neither is silently repurposed as the selected owner: the
selected image remains Python 3.12.3, and every live socket must pass its existing
owned-peer check **before** sending a credential.

The new [helper](../../scripts/selected_service_conformance.py) reuses:

- The existing [offline payload builder](../../scripts/provider_conformance.py),
  its 17 hash-locked wheels and its payload verifier. The checker is unchanged
  `trustedrouter-provider-check==0.1.8`, wheel SHA-256
  `591a11295d3f1e0ef20fbc51c788cabfbedf176e79ce56de8378252bd13adecd`.
- That pinned wheel's `GatewayClient.chat_body`, catalog JSON Schema,
  `Draft202012Validator`/`FormatChecker`, `_validate_vendored_rules`, and all seven
  `run_streaming_checks` assertions. These are the primary-source interfaces
  inspected for this adapter, not assumptions about a later checker version.
- The [inference TLS connection](../../scripts/selected_service_inference.py),
  [strict HTTP stream framing](../../scripts/stream_transport.py) and
  [bounded stream/usage parser](../../scripts/stream_measurement.py).

The checker entrypoint is deliberately **not** invoked: its catalog routine
opens a separate HTTP client and can fetch a public schema; its gateway client
can retry transient requests. This hook supplies an owned, single-use stream
response to its unchanged stream assertions and validates the owned catalog
with its unchanged offline rules. No checker HTTP client is instantiated and no
schema URL is fetched. This is a **catalog and Tier-4 subset**, not the complete
17-check provider run or a replacement for its independent full gate.

## Fixed sequence

`--conformance-smoke` is mutually exclusive with `--inference-smoke` on both the
host recorder and container owner. It first runs all three existing inference
cases, unchanged. Only their source-bound PASS permits these four new requests:

1. Streaming body without a key: exact guard HTTP 401 rejection.
2. Same streaming body with a fixed wrong key: same rejection.
3. Public `GET /v1/models`: pinned catalog validation, exactly the canonical
   selected ID, context/output limits, false tool/reasoning capabilities,
   configured provider facts/prices and original retirement timestamp.
4. One authenticated stream: the checker's synthetic PONG request and request
   builder, with the existing explicit 256-token cap and `include_usage`.

Every assertion must pass; warnings and skips cannot produce hook PASS. The
strict existing parser additionally checks canonical stream identity, usage
types/totals/bounds, finish, DONE and HTTP EOF. The stream must contain visible
content. No text or upstream measured/error dictionaries enter the receipt.
HTTP 401 does not measure zero engine calls; nonforwarding remains the separately
reviewed guard implementation and its mocked transport-count tests.

## Packaging and limits

The existing image preparer's optional `--checker-wheelhouse` takes a local
directory containing exactly the existing lock's 17 wheels. No dependency is
downloaded during preparation or image build. The resulting `checker/` subtree
is included in the existing prepared hash inventory and read-only seal, with
the same file/count/byte limits. Default preparation without this option remains
unchanged. A conformance-mode owner rejects an image without the payload during
the closed `prepared` stage, before starting the engine. Existing artifacts are
never upgraded in place; a new exact prepared receipt and approved image are
required for this mode.

Pinned wheels include empty package markers. Receipt validation permits zero
bytes only below `context/service/checker/packages/`, with the empty-file hash,
the pinned checker inventory and the complete prepared source/hash join all
validated. Empty files elsewhere remain rejected; no size limit is relaxed.

The new helper has a 160-second total budget, 5 seconds for each rejection and
catalog request, and at most 120 seconds for the authenticated stream. Every
bound is clipped to the original cutoff minus the unchanged 375-second cleanup
reserve. Response buffers are bounded: rejection 4 KiB, catalog 64 KiB and
stream 1 MiB, with the existing 64 KiB SSE event bound. The main-thread absolute
alarm also bounds parsing, imports and finalization; socket timeouts never renew
the lifetime. There is one invocation and at most one new authenticated request.

Cancellation, helper failure, partial results and cleanup failure remain sticky.
The owner immediately uses its existing front-first drain/reap after either
smoke outcome. Both validated inference and stream token accounting survive
later identity, close, deadline or cleanup failure. Attempted requests lacking
valid usage remain explicitly unknown; valid zero counts on a failed response
are not silently converted into unknown or success.

## Closed receipts and verification

Opt-in owner and host envelopes use v3 and `mode: conformance-smoke`; existing
v1/default and v2/inference modes retain their behavior. The new report joins
the prepared hash, fixed conformance-contract hash and exact prior inference
report hash. Only closed case/status enums, booleans and token counts escape.
Upstream Python/native output is suppressed and private exception chains are
never projected. All deployment, hardware, full-provider, quality, streaming
capability and privacy claim flags remain false; case results are not claims.

The [regressions](../../scripts/test_selected_service_conformance.py) cover
packaging/tampering, mode and source joins, actual pinned checker assertions on
synthetic in-memory HTTP chunks, private canaries, usage on late failure,
single-use cancellation/deadlines and unchanged front-first cleanup. There are
no listening ports or hardware calls. The checker-dependent tests run in a
disposable environment installed from `scripts/e2e/requirements.lock` with
`--require-hashes`; without that environment they are explicitly skipped.

The required `Check` workflow separately installs that same lock, wheels only,
into a fresh CPython 3.12.13 environment and runs
[`check_checker_tests.py`](../../scripts/check_checker_tests.py). This gate
verifies every locked package version and executes all tests in
`PinnedCheckerTests` and `PinnedProtocolTests`, including future methods in
those classes. Missing dependencies, test import errors, empty classes, skips,
expected failures and incomplete execution fail this required step. Ordinary
local discovery retains its explicit optional skips; a green local discovery
without the checker environment is not evidence that these tests ran. The
separate Docker E2E checker run does not substitute for this coverage.

Focused command from `scripts/`, using that environment's Python:

```sh
python -B -W error::ResourceWarning -m unittest -q test_selected_service_conformance test_selected_service_inference test_selected_service_hook test_selected_service_image test_selected_service test_selected_service_process test_selected_service_smoke
```

Actual image/runtime compatibility, hardware conformance and the complete
provider gate remain unproven. Independent final-head review and full CI are
required before any separately authorized private hardware run.
