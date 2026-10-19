<!-- SPDX-License-Identifier: Apache-2.0 -->
# ADR 0007: Reuse tr-attestation with complete device verification

Status: accepted design; no hardware confidential verdict has been demonstrated.
Records PLAN D7.

## Decision

Use the pinned `trcs` implementation and
[tr-attestation/v0 protocol](https://github.com/Lore-Hex/trustedrouter-confidential-stack/blob/05787fadca260dbe27f6750f51f5e0ffbdadf8f3/docs/attestation-protocol.md).
Make required protocol/shim improvements upstream, then pin the reviewed merge
commit here. Do not fork a second proxy or cryptographic transcript.

At boot the guest must verify every assigned GPU and each required fabric
device before readiness. On attestation requests it collects fresh CPU and
device evidence bound to the relying party's challenge and the serving TLS
key. The relying party independently verifies signatures, measurements, device
inventory, mode, freshness, revocation and release policy before sending data.

## Rationale and limits

The protocol binds CPU report data to the same-connection SPKI and a fresh
nonce, and derives a separate device nonce. Its `workload` and `confidential`
JSON fields are hints, never trusted decisions. A collector returning a bundle
does not prove the signature, GPU mode, completeness or actual worker identity.
Expected device identity must be tied to measured guest assignment and secure
device sessions, not only host-supplied labels.

[go-tdx-guest verification options](https://github.com/google/go-tdx-guest/blob/23b3eac9db39ba4e1df1426bacb3bfa3a7018e97/README.md#verify)
require explicit collateral and CRL enabling. GPU vendor evidence needs the
approved NVIDIA verifier, signed reference measurements and revocation checks;
matching an envelope's nonce is not enough. All required failures remain closed.
See the [full verification contract](../architecture.md#evidence-and-relying-party-verification).

## Verification and revisit

P1 uses golden vectors and field-by-field mutation tests against fake evidence.
Its positive result is labelled fake/plumbing-only and cannot qualify a route.
Release builds must reject fake inputs and must not contain fake backends.

P3 repeats CPU/GPU signature, nonce, SPKI, measurement, debug and revocation
negative controls with genuine evidence. P5 adds the complete multi-GPU/fabric
inventory and operational tests. Never treat missing real GPU cryptography in
an early implementation as an optional production check.

Review protocol extensions in P4.T1 and keep compatibility/version semantics
explicit. A change to signed/bound identity is a protocol change, not merely a
new display field.
