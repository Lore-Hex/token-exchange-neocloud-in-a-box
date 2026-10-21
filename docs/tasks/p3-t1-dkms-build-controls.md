# Prepared DKMS Build Controls

The mkosi input preparer now includes a build-only skeleton in the nonbootable
candidate recipe. Its three files are included in the source-bound receipt and
rechecked during bundle verification. Nothing is installed or compiled by this
step. These controls must not be mistaken for a verified module or release image.

- `etc/dkms/no-autoinstall` separates package configuration from explicit builds.
- `etc/dkms/framework.conf.d/99-nib-build.conf` sets two jobs and clears
  `modprobe_on_install` and `build_environment`.
- The exact NVIDIA/version/kernel/architecture override replaces the vendor's
  CPU-count command with two jobs, kernel `6.8.0-147-generic`, its explicit header
  directory, GCC 13's architecture-qualified executable, and `ld.bfd`.

The override clears the vendor's compiler-mismatch, Xen and PREEMPT_RT ignore
variables. Setting an ignore variable to `0` is insufficient: the pinned
`conftest.sh` checks non-emptiness. Likewise, DKMS treats a nonempty
`modprobe_on_install="false"` as enabled, so this value must be empty.
Module-signing policy is not overridden. The existing vendor IBT argument is
retained; its eventual compile and runtime properties still require verification.

## Source Review

Read October 21, 2026 from the exact authenticated archives in the
[guest package lock](../../image/inputs/r595-ubuntu2404.json):

| Package | Version | Archive SHA-256 |
| --- | --- | --- |
| DKMS | `1:3.4.3-1ubuntu1` | `6cb516fc913f1afbe8387078b357e15619e19291b9398e0b3cb6d0beae99e2b4` |
| NVIDIA DKMS | `595.91.07-1ubuntu1` | `6644db06281a3f1f460fbe90d606d9e961f7d0f1a28c362bfddbf28c30ab1246` |
| NVIDIA source | `595.91.07-1ubuntu1` | `5e83b871c85b9abbca569feb4e1367198ce8f70975a1c62b4c1bd826464cd371` |

DKMS `usr/lib/dkms/common.postinst:113-116` checks `no-autoinstall` before
enumerating kernels or adding/building a module. Kernel post-install and
header hooks call `dkms_autoinstaller`, which delegates to `dkms autoinstall`;
`usr/sbin/dkms:2976-2980` checks the same guard. The kernel-install hook also
delegates to this path. These are source observations, not executed hook tests.

The inspected `usr/sbin/dkms` SHA-256 is
`a7a45df4e9f8a4fbea258a54b31d21760121560fe0b6187e34d5b39944e2767b`;
`usr/lib/dkms/common.postinst` is
`5641c9b79d5c7c992b070307a571763447f7aeb46cb58e734f5a45a56cb44247`.
The main script loads per-module overrides after `dkms.conf` at lines 634-646.
Its build command only injects `-j` when the command starts with unquoted `make`.
NVIDIA's quoted `'make' -j$(nproc)` therefore needs an explicit replacement;
setting DKMS's generic job count alone does not constrain that vendor command.

The preparer rejects a changed or missing reviewed DKMS, driver, compiler or
header version. Other versions require a new source review, not automatic reuse
of these controls. The ordinary package-preparation recipe remains byte-identical
when no skeleton is requested.

## Remaining Gates

The future runner must put this skeleton into the target before every package
transaction, retain the executable service-denial overlay, and use a separately
reviewed namespace policy with no host devices or network. Manual `dkms add`,
build and install must name the exact target; they must not remove the guard or
use `autoinstall`. Verify installed configuration before invoking any build.

Empty ignore variables alone do not prove compiler compatibility. NVIDIA's
source also bypasses its compiler check when the kernel's `compile.h` is absent.
An independent compiler/header identity check and actual conftest/LKCA evidence
remain required, as do a complete maintainer-hook review, two clean builds,
bounded execution, cleanup, boot and hardware attestation. No gate is cleared
by merely emitting this skeleton.
