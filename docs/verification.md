<!-- SPDX-License-Identifier: Apache-2.0 -->

# Verifying a node

**P1 software only. No confidential hardware has been validated.** The release
verifier cannot currently approve a confidential node: NVIDIA GPU/fabric
cryptographic appraisal remains P3 work. A valid CPU quote with structurally
complete GPU envelopes produces `partial`, `confidential: false`, and exit 1.
Anything invalid produces `fail`. Neither result authorizes inference.

## Acceptance policy

Obtain a release manifest and hardware profile through an independently trusted
channel, with their SHA-256 digests. Never take acceptance measurements or file
digests from the endpoint being verified. Signed releases and transparency-log
verification are P8 work; the current CLI requires explicit local digest pins.

```sh
nib verify --endpoint https://node.example \
  --release release.json --release-sha256 <trusted-sha256> \
  --profile profiles/b200x8-tdx.yaml --profile-sha256 <trusted-sha256>
```

The CLI validates both files against the repository schemas and their hashes.
It rejects debug releases, non-confidential profiles, missing profile bindings
and malformed measurements. The release supplies expected MRTD and RTMR0-2;
the hardware profile supplies expected GPU and NVSwitch counts. These are
acceptance policy, not measurements learned from the server. RTMR3 is reserved
by the current protocol and is not assigned an implicit value.

## Connection and CPU checks

The client generates a fresh 32-byte nonce, connects once over TLS 1.3, and
fetches only `/.well-known/trustedrouter/attestation`. It hashes the actual peer
certificate's SPKI on that connection. It sends no credentials, cookies or
inference data. Proxy environment variables, redirects, retries and connection
pooling are not used. Ordinary Web PKI is not the trust root for this bootstrap;
the key must instead be authenticated by the quote and pinned measurements.
The bootstrap connection is closed after collecting evidence. A future
inference client must retain its own verified connection rather than treat a
prior CLI check as authorization for a different TLS session.

The independently reproduced public protocol binding is:

```text
report_data = SHA256(SPKI) || SHA256("tr-attestation/v0" || 0x00 || nonce)
gpu_nonce   = SHA256("tr-attestation/v0/gpu" || 0x00 || report_data)
```

Three golden vectors match the upstream protocol. The release path accepts raw
TDX quotes from `configfs-tsm` / `tdx_guest`; agent JSON wrappers are not
silently treated as quotes. Quote V4/V5 parsing and Intel signature-chain
verification use Apache-2.0
[go-tdx-guest commit 23b3eac9db39](https://github.com/google/go-tdx-guest/tree/23b3eac9db39ba4e1df1426bacb3bfa3a7018e97).
The code explicitly enables collateral retrieval, CRL checks, TCB-status checks,
TD-debug rejection and migratable-TD rejection, and validates nonempty report
data and MRTD/RTMR0-2 policy. It uses the library's embedded Intel trust root,
not quote-provided roots. See the pinned
[verification implementation](https://github.com/google/go-tdx-guest/blob/23b3eac9db39ba4e1df1426bacb3bfa3a7018e97/verify/verify.go)
and [validation options](https://github.com/google/go-tdx-guest/blob/23b3eac9db39ba4e1df1426bacb3bfa3a7018e97/validate/validate.go).

## Resource and privacy boundaries

- One 30-second evidence fetch and one 30-second verification deadline.
- HTTP headers at most 64 KiB, response at most 16 MiB, CPU quote at most 1 MiB,
  auxiliary CPU data at most 4 MiB, total decoded GPU evidence at most 8 MiB.
- Bounded JSON nesting/field counts and at most 64 devices; duplicate, unknown
  and case/Unicode-aliased fields are rejected before typed decoding.
- Intel collateral requests use an exact origin/path/query allowlist, normal
  certificate validation, no proxy or redirects, at most eight requests,
  ten seconds per request and 4 MiB per response. Quote-controlled URLs cannot
  fetch arbitrary destinations. Expired, revoked or missing collateral fails.
- Output is a small verdict with locally pinned release/profile IDs. Errors do
  not echo remote response bodies, headers, URLs, credentials or local paths.

Device envelopes must name distinct devices, match the profile's GPU/fabric
counts, contain evidence and bind to `gpu_nonce`. **These structural checks do
not verify NVIDIA signatures, vendor identity, RIM, revocation, firmware, mode
or the execution path.** P3 must authenticate all of those before any production
GPU-verified/confidential result can be returned. Remote `confidential` and
workload-description fields are untrusted hints and never grant acceptance.

## Fake integration

Only `go build -tags nibfake` includes the private `NIBFAKE-*` formats. This
variant accepts only explicitly fake CPU/device evidence and returns
`fake_pass`, `fake_checks_passed: true`, and all hardware/confidential booleans
false. It is not a replacement for a vendor verifier and must not be used for
real inference. Release builds reject these bytes even if relabeled as TDX.
There is no runtime fake switch or injectable appraiser in the public API.

CI runs both tagged and untagged race suites. It builds Linux release and fake
CLI binaries, checks release symbols exclude the fake appraiser, and checks
the fake binary contains both appraisers as positive controls. Mutation tests
cover key/nonce binding, all four measurements, truncated quotes, debug and
migratable flags, device nonce/identity/count/kind, malformed JSON, redirects,
timeouts, and collateral URL/budget limits. Local TLS tests cover fresh nonces
and same-connection SPKI binding through a complete fake verification.

Real CPU quote/collateral validation, authenticated per-device/fabric evidence,
independently predicted boot measurements, and post-boot execution guarantees
still require the P3/P5 hardware acceptance runs. No fake test proves them.
