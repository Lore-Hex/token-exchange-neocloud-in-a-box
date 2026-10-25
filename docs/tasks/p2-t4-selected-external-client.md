# External Selected-Service Smoke

Status: software tests only. No external endpoint or GPU has been exercised by
this change. Complete CI, independent final-head review, an approved finite
service run and a separately reviewed target are required before execution.

[probe-selected-service.py](../../scripts/probe-selected-service.py) checks the
ordinary selected service from a separate client. It does not reuse the
in-container process ownership claim: a remote TLS peer cannot establish that
claim. Instead, the operator provides a numeric global IPv4 address, port,
hostname, exact certificate-file SHA256 and private key-file path. No DNS lookup,
HTTP proxy, redirect, arbitrary model, prompt or path is supported.

The certificate and API key use the existing bounded private-file reader:
owner-only file and parent directory, no symlink traversal or hard links,
stable identity and length. The certificate file must contain one certificate
and match its expected byte hash. TLS requires certificate and hostname
validation, then an exact DER leaf-certificate hash match before a request.
The real API key is not even read until the authenticated request's own TLS
handshake and pin check pass. This is ordinary pinned TLS, not E2EE or attestation.
Python's [TLS client contract](https://docs.python.org/3.14/library/ssl.html#ssl.PROTOCOL_TLS_CLIENT)
and [peer certificate API](https://docs.python.org/3.14/library/ssl.html#ssl.SSLSocket.getpeercert)
define those checks. No verification is disabled for self-signed test certificates.

## Fixed Request Budget

One invocation makes one fixed `GET /healthz`, two negative-auth requests and **at most one
authenticated streaming request**, with the existing selected model identity,
short synthetic greeting, temperature zero and 256-token output cap. Missing
and wrong keys must receive the exact reviewed 401 body. Actual upstream
non-forwarding is not measured by this external client. It remains a separately
reviewed server property, not an inference from HTTP status alone.

The health request also requires its own pinned TLS peer and sends no credential.
It accepts only HTTP 200, one `Content-Length: 0`, no transfer/content encoding,
an empty body and complete EOF. Pinned
[trcs health](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/8a87d9765ceabe76bbfa5e56a72c91da6d8290d5/internal/shim/server.go#L122-L143)
checks its listener audit and forwards a bounded health request to the guard;
the [guard](../../internal/pilotapi/handler.go) checks engine `/health`.
A successful auth rejection alone does not check the engine. This is one
point-in-time health observation, not ownership or continued readiness proof.

There are no retries, readiness polling, automatic restart or credential
fallback. Run only after the owner has separately established readiness. The
operator supplies a fixed UTC cutoff at most 150 seconds away. TLS connect is
bounded at five seconds; health and negative checks have five-second total deadlines and
the sole inference has at most 120 seconds, all clipped to the original cutoff.
The existing main-thread alarm and cancellation restoration bound slow/trickled
responses, including header parsing. Every socket and response is closed.

The client reuses the existing strict HTTP chunk decoder and streaming
measurement parser, not a second SSE implementation. It checks status, media
type, lengths, framing, model, finish, usage and DONE/EOF, with a one-MiB response
limit. A valid usage chunk is retained even if later framing or EOF fails;
missing usage is unknown, never synthesized as zero. Prompts, response text,
headers, credentials, addresses and raw exceptions are never printed or saved.

## Operator Invocation

The following is a template, not a record of execution. Keep all deployment
values and files outside Git and use a clean checkout of the exact reviewed
source. The certificate is supplied out of band, not downloaded from the
unverified peer. The private API key file must already be installed securely
on the client; never copy the server TLS private key to the client.

```sh
python3 -I -S -B scripts/probe-selected-service.py --run \
  --address "$APPROVED_IPV4" --port "$APPROVED_PORT" \
  --hostname "$EXPECTED_TLS_NAME" \
  --certificate "$PRIVATE_CLIENT_DIRECTORY/cert.pem" \
  --certificate-sha256 "$APPROVED_CERTIFICATE_FILE_SHA256" \
  --api-key-file "$PRIVATE_CLIENT_DIRECTORY/api-key" \
  --deadline "$FIXED_UTC_CUTOFF"
```

The single JSON result binds Git HEAD and exact committed hashes for the named
probe/parser/input helpers. These are provenance fields, not a complete measured
software closure. `tls_checks` records each completed peer check;
`tls_peer_verified` requires all four. Request attempts, passed checks and
known/unknown usage remain distinct. The stream measurement is one observation,
not a capacity or uptime claim. Preserve the original result on failure and
investigate before any new authorized run.

PASS proves only that this client observed the fixed auth/stream interaction
with the pinned TLS endpoint. It does not prove the physical inference host,
network isolation, an ownership receipt, the full provider-check gate, model
quality, safe public activation or confidential computing. Those remain separate
checks; all such qualification fields stay false.

[Tests](../../scripts/test_selected_external_smoke.py) use synthetic sockets,
HTTP/SSE bytes and private-data canaries. They cover peer pinning before secret
access, fixed request budgets, no redirects/retries, unknown usage, retained
usage after a malformed ending, cutoff rejection and closed diagnostics. They
make no real-handshake, network or model claim.
