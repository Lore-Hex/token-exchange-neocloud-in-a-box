<!-- SPDX-License-Identifier: Apache-2.0 -->
# ADR 0003: Explicit bare-metal TDX launch

Status: accepted design; actual launch commands are deferred to hardware review.
Records PLAN D3.

## Decision

Use the Canonical TDX host stack and an explicit, pinned QEMU/TDVF launch for
the first bare-metal node. Do not introduce Kubernetes, CoCo, dstack, an external
KMS, or a host-controlled workload scheduler into this profile. Host launch
tools are untrusted; their output must satisfy the verifier's measured policy.

The host can forward TCP to the guest front door but cannot terminate its TLS.
The guest generates its own private key and never exports it. Each hardware
profile fixes the launch inputs needed to reproduce the accepted measurements.

## Rationale and limits

One server and one model do not yet need a fleet orchestrator. This choice
limits configuration and update paths we must audit. It does not make QEMU or
the operator trustworthy, nor does it prove the chosen guest software secure.
The [Canonical TDX source and setup documentation](https://github.com/canonical/tdx)
and [NVIDIA deployment guidance](../hardware.md) are inputs to P5's exact pinning
and command review, not permission to run floating installation scripts.

One guest image design can target GCP and bare metal, but their firmware and
measurements differ. If GCP rejects the custom image, record the limitation;
running packages on its stock image does not validate our measured image.

## Verification and revisit

P3/P5 reconstruct the actual boot event log, compare predicted registers to
quotes, and independently alter firmware, launch shape, effective command line
and image to ensure rejection. Test a TLS-terminating intermediary as a negative
control. No host-selected alternate kernel or executable overlay may escape
measurement checks.

Revisit at fleet scale under P9. PLAN's managed dstack fallback requires its own
approval and platform-specific policy; it cannot claim this bare-metal profile
was validated. See [architecture](../architecture.md#measurement-contract).
