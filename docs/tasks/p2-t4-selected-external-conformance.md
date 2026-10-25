# External selected-service conformance

[The runner](../../scripts/check-selected-service.py) observes an explicitly
approved external selected endpoint through system-root TLS. It reuses the
[owned protocol assertions](p2-t4-selected-service-protocol-drain.md), not the
owned-peer or process identity boundary. This implementation has synthetic test
coverage only: no external request, hardware operation or public deployment is
authorized or claimed by this change.

## One closed sequence

There are at most 13 single HTTP exchanges, serially, with zero retries,
redirects, alternate targets or discovery:

1. One credential-free `/healthz` request.
2. Missing-key and fixed wrong-key authentication negatives.
3. One `/v1/models` request, checked against the complete expected catalog.
4. Eight unchanged pinned JSON requests: callability, PONG/default response,
   temperature, four forwarded-field probes and explicit max-token spelling.
5. One pinned streaming request, with all seven upstream streaming assertions.

Exactly nine requests can read the real bearer key. Every connection completes
system-root certificate and hostname verification before any key read. The
numeric public IPv4 target and SNI hostname are fixed by the input; there is no
DNS lookup, proxy, supplied CA file or certificate-pin fallback. OpenSSL trust
environment overrides are rejected, including empty `SSL_CERT_FILE` and
`SSL_CERT_DIR`. This is validation against the observer's installed system roots,
not a claim that every client trusts the service or that its deployment is safe.

The primary assertion source remains the hash-locked
`trustedrouter-provider-check==0.1.8` wheel, SHA-256
`591a11295d3f1e0ef20fbc51c788cabfbedf176e79ce56de8378252bd13adecd`:
`http.py::GatewayClient.chat_body`, `checks/catalog.py::run_catalog_checks`,
`checks/callability.py::run_callability_checks`,
`checks/chat.py::run_chat_checks` and
`checks/streaming.py::run_streaming_checks`. The adapter invokes those assertions
through the shared protocol helpers, never `GatewayClient` network transport
(which has retries/redirects), nor the catalog checker's independent HTTP client.
The optional upstream `catalog.declared-v2` observation remains `skip`; its
vendored schema and rules are instead applied offline to the observed catalog.
The separate mandatory 17-ID set retains every original pass/fail/warn/skip.
WARN, SKIP, unknown usage or an incomplete run cannot become overall PASS.

## Explicit input and limits

The operator supplies a private regular JSON file plus its exact SHA-256. Its
closed `nib.selected-external-conformance-input/v1` object has these fields:

| Field | Meaning |
|---|---|
| `schema` | The exact input schema above |
| `runner_git_sha` | Exact local committed source; loaded toolkit files must match Git |
| `service_git_sha` | Expected service producer only, not remotely proven |
| `preparation_sha256` | Expected preparation only, not remotely proven |
| `checker_payload_sha256` | Exact `trial.json` hash of the sealed checker payload |
| `target` | Exactly `address`, `hostname`, `port`; no URL |
| `catalog` | Complete expected native catalog including provider, prices, limits and lifecycle |
| `deadline` | Absolute UTC `YYYY-MM-DDTHH:MM:SSZ` cutoff, at most 480 seconds away |
| `admission_stop_at` | Expected catalog retirement cutoff; the run must end no later |

The expected catalog is schema-validated before network access and must equal
the observed catalog after canonical JSON encoding. This initial profile is the
single canonical selected model at 8192 context / 256 output tokens, with only
streaming advertised. It does not enable graph C8, tools or capability promotion.
The catalog retirement must equal `admission_stop_at`. The receipt retains hashes
of the target, provider and catalog, not their raw identity or content. An exact
catalog match is an observation, **not proof of the server's source or ownership**;
both corresponding receipt flags remain false.

The immutable monotonic budget can only shorten when the wall clock advances;
rolling the clock backward cannot extend it. Individual generation exchanges
are limited to 120 seconds, other exchanges to five seconds, all clipped to the
original cutoff. JSON/catalog bodies have a 64 KiB limit; streaming has the
existing 1 MiB total and 64 KiB event bounds. Non-streaming responses require one
canonical Content-Length, no transfer/content encoding and exact EOF; streaming
reuses the existing strict HTTP/SSE parser, finish/usage/DONE/EOF requirements.
The stream must have visible content; buffered delivery retains its upstream
warning. No parser repair, relaxed status or silent request rewrite is added.

The checker deliberately omits a max-token field in six of its JSON probes. The
existing selected guard must enforce its advertised 256-token ceiling; the
runner does not rewrite the upstream recipe. Valid known usage is retained even
if EOF, cancellation or connection close later fails. A request that may have
been sent without authoritative usage is explicitly unknown, never zero. This
is client-observed accounting, not a billing ledger or server-side call count.

## Software preparation and execution gate

The checker payload is the existing optional prepared `checker/` subtree with
its 17 hash-locked packages, for the observer's OS/Python ABI. The supplied
`trial.json` hash is checked before any package import, then the complete pinned
inventory is verified by the existing loader. Relative payload paths, symlinks,
extras and byte/hash drift are not accepted. Import paths/modules are restored
afterward; upstream stdout/stderr and exception chains never reach the receipt.
No package installation or download occurs during a run.

An explicitly authorized operator can use this form, substituting private
absolute paths and an independently approved input digest:

```sh
python3 -I -S -B scripts/check-selected-service.py --run \
  --input "$PRIVATE_INPUT" --input-sha256 "$INPUT_SHA256" \
  --checker-payload "$SEALED_CHECKER_PAYLOAD" --api-key-file "$PRIVATE_KEY"
```

Only the closed JSON receipt is printed. Failures preserve completed observations
and emit a fixed category, never requests, responses, headers, paths, addresses,
credentials or raw exception text. PASS requires all 17 assertions, all four
prechecks, known usage for all nine generation requests and closed connections.
Ownership, runtime identity, continued readiness, measured negative upstream
call count, quality, deployment eligibility, confidential and E2EE flags remain
false. Active-stream drain, sustained load and a qualified quality benchmark are
separate gates; this observer never retires or controls the service.

[Regression tests](../../scripts/test_selected_external_conformance.py) run the
real pinned assertions over synthetic sockets. They exercise the exact 13-call
sequence, TLS-before-key, catalog/source/payload drift, trust overrides,
redirects, forward/backward clock jumps, cancellation, malformed/late framing,
unknown usage and private canaries. Existing owned protocol, external TLS,
readiness and frozen stream tests remain unchanged. Run with the native,
hash-locked checker environment:

```sh
PYTHONPATH=scripts python -B -W error::ResourceWarning -m unittest \
  test_selected_external_conformance test_selected_service_protocol \
  test_selected_service_conformance test_selected_external_smoke \
  test_selected_public_ca test_selected_health_wait
```

Independent final-head review, full software checks and a separately authorized
operational input/run are still required. No live conformance result exists for
this runner yet.
