<!-- SPDX-License-Identifier: Apache-2.0 -->
# ADR 0001: One node, one trust domain, one model

Status: accepted design for P1-P6; not hardware validated. Records PLAN D1.

## Decision

The first node runs one model pack on one physical server inside one Intel TDX
VM. All inference replicas and assigned GPUs belong to that VM. For the first
DeepSeek profile, two TP4 replicas are an intended scheduling arrangement, not
separate tenants or security boundaries. Do not add multi-node inference or
cross-VM GPU sharing to this release.

This is our deliberate scope, not a claim that every confidential-computing
platform requires an entire physical server for each VM. The accepted hardware
profile must declare the complete GPU and fabric inventory actually assigned.

## Rationale and limits

A single measured guest lets the verifier bind the TLS terminator, launch
configuration, model volume and device policy to one release. It avoids an
additional protocol for trusting downstream workers. Every device used for
inference still needs attestation and authenticated device-session enforcement;
CPU attestation alone is insufficient. The host remains able to deny service.

See [architecture: trust boundaries](../architecture.md#node-and-trust-boundaries)
and NVIDIA's [R595 trusted-computing release notes](https://docs.nvidia.com/595trd1-trusted-computing-solutions-release-notes.pdf)
for the supported platform/mode matrix. Treat any future multi-node transport
as a new security design, not an extension implicitly covered by this ADR.

## Verification and revisit

P1 tests reject missing, repeated and unexpected device declarations. P3/P5
must prove vendor evidence and actual device assignment agree, and that no
non-loopback engine or unverified remote worker is reachable. P5 records the
real replica layout, memory use and complete fabric evidence.

Revisit when a supported multi-node confidential mode and a verifiable worker
identity/transport protocol are available, with a separate ADR and tests.
