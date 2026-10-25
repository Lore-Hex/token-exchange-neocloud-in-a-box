# External Selected-Service Smoke

Status: software tests only. No external endpoint or GPU has been exercised by
this change. Complete CI, independent final-head review, an approved finite
service run and a separately reviewed target are required before execution.

[probe-selected-service.py](../../scripts/probe-selected-service.py) checks the
ordinary selected service from a separate client. It does not reuse the
in-container process ownership claim: a remote TLS peer cannot establish that
claim. Instead, the operator provides a numeric global IPv4 address, port,
hostname, a closed TLS trust mode and private key-file path. Pinned mode remains
the default and requires an exact certificate-file SHA256. No DNS lookup,
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

## Explicit System Roots

`--tls-mode system-roots` instead uses the client's installed root trust store,
without any supplied certificate or leaf pin. Both `--certificate` and
`--certificate-sha256` must be absent; there is no custom CA, insecure switch or
fallback to pinned mode. OpenSSL's `SSL_CERT_FILE` and `SSL_CERT_DIR` environment
overrides (including empty values and the runtime's configured override names)
are rejected. TLS key logging is not enabled, including by `SSLKEYLOGFILE`.

The shared helper uses `SSLContext(PROTOCOL_TLS_CLIENT)` with TLS 1.2 or newer,
`CERT_REQUIRED`, hostname checking and `load_default_certs(SERVER_AUTH)`.
The [Python 3.12 trust-store API](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext.load_default_certs)
loads platform defaults; the
[OpenSSL default-path contract](https://docs.openssl.org/3.0/man3/SSL_CTX_load_verify_locations/)
defines the environment overrides. The server must present its usable chain;
the client does not download missing intermediates. Chain, hostname, validity
and HTTP checks precede any API-key read, including a fresh TLS verification on
the authenticated request's own connection. Verification errors never retry or
switch trust mode. The readiness waiter reuses this exact helper.

This mode establishes chain and hostname acceptance by this client's installed
roots, not universal browser trust, revocation checking, ownership or deployment
approval. Administrators control those roots and may install private CAs. Their
integrity and the client clock remain trusted inputs. The root store is not
modified or claimed as a source-hashed input. No public endpoint, certificate
issuance or live system-root observation is established by this software change.

## Fixed Request Budget

One invocation makes one fixed `GET /healthz`, two negative-auth requests and **at most one
authenticated streaming request**, with the existing selected model identity,
short synthetic greeting, temperature zero and 256-token output cap. Missing
and wrong keys must receive the exact reviewed 401 body. Each 401 also requires
a single bounded canonical Content-Length, application/json, no transfer or
content encoding, exactly the declared bytes and trailing EOF. Truncation,
duplicate/conflicting lengths and extra bytes reject before reading the real
key. Health and authentication share this bounded fixed-body reader. Actual upstream
non-forwarding is not measured by this external client. It remains a separately
reviewed server property, not an inference from HTTP status alone.

The health request also requires its own verified TLS peer and sends no credential.
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

For a separately approved public-CA observation, use the same command and finite
target/cutoff, replace both certificate arguments with `--tls-mode system-roots`,
and retain the private API-key argument. This is still only one authenticated
request, with no automatic dispatch, DNS action or service change.

The single JSON result binds Git HEAD and exact committed hashes for the named
probe/parser/input helpers. These are provenance fields, not a complete measured
software closure. `tls_checks` records each completed peer check;
`tls_peer_verified` requires all four. Request attempts, passed checks and
known/unknown usage remain distinct. The stream measurement is one observation,
not a capacity or uptime claim. Preserve the original result on failure and
investigate before any new authorized run.

Pinned mode retains the v1 receipt and startup target digest unchanged.
System-root mode emits `nib.selected-external-smoke/v2` with
`tls_mode: "system-roots"`; TLS counters mean completed chain/hostname checks,
not exact leaf-pin matches. All other result fields and fixed budgets are
unchanged. A failed result can retain this requested mode without claiming a
successful handshake; inspect the result and individual TLS checks.

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
The [system-root tests](../../scripts/test_selected_public_ca.py) additionally
perform real in-memory OpenSSL handshakes using temporary synthetic certificates
and an isolated, test-only installed-root loader. They cover fullchain success,
missing intermediate, expired/untrusted chains and wrong hostname at every
probe connection, key-read ordering, permanent readiness rejection, no fallback,
key-log prevention and the unchanged limits. They create no listener, change no
system trust store and exercise no public CA or remote endpoint.
