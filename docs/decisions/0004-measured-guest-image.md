<!-- SPDX-License-Identifier: Apache-2.0 -->
# ADR 0004: Immutable measured guest image

Status: accepted design; image construction and hardware tests are P3/P5 work.
Records PLAN D4.

## Decision

Build with mkosi using the supported Ubuntu 24.04 guest base. Produce a Unified
Kernel Image and a read-only, dm-verity-protected root filesystem for each
`(model pack, engine, hardware profile, variant)` release. Pin every component,
dependency and executable/configuration input. A change requires a new reviewed
release identity, not a mutable host override.

The authenticated boot path must bind the effective kernel, initrd and command
line, including the trusted rootfs verity root. The protected rootfs binds the
engine image, flags, egress policy and model-volume verity root. Release builds
exclude fake backends, SSH, serial shells and debugging tools. Debug images
must have different measurements and remain outside release allowlists.

## Rationale and limits

A UKI file digest alone does not establish what the machine booted. The actual
firmware and loader must enforce the intended chain with no unmeasured override.
[systemd-stub documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd-stub.html)
describes UKI boot behavior;
[dm-verity](https://docs.kernel.org/admin-guide/device-mapper/verity.html)
describes block integrity once a trusted root is supplied. Neither independently
chooses the release a relying party should accept.

Writable state is limited to the explicit volatile/encrypted scratch design.
Do not create a writable executable overlay, plaintext swap, crash dump or
runtime package/model-download path that bypasses the protected rootfs.

## Verification and revisit

P1 tests compile-time fake/release separation. P3 performs two clean builds and
requires identical UKI and rootfs verity hashes, then tests effective boot inputs
against real quotes. P5 repeats on the chosen bare-metal profile and checks
two reboots. Tampering must fail before inference is accepted.

Reject verity modes that ignore corruption or verify data only once. Any future
mutable update mechanism needs a new ADR, measured enforcement and rollback
tests. See the [measurement contract](../architecture.md#measurement-contract).
