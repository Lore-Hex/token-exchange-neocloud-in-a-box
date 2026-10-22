# Guest Image

**Status, 2026-10-22:** the offline Ubuntu 24.04 package installation and
NVIDIA driver compilation and installation are proven on a native Linux build runner. There
is no bootable or production-qualified confidential guest image yet.

| Component | Verified state |
| --- | --- |
| Public inputs | Authenticated, hash-pinned kernel/driver graph and 203-package tools graph; no floating package resolution during builds. |
| Build boundary | Offline user/mount/PID/network namespaces, fixed capabilities, seccomp and cgroup limits, with independent cleanup evidence. |
| Guest package installation | All 145 expected package versions configured successfully in the private root. |
| NVIDIA R595.91.07 | Five modules compiled for kernel 6.8.0-147; strong references to the three required Linux crypto interfaces verified. |
| Driver installation | All five installed modules match the compiled objects; dependency indexes and independent cleanup verified in a native Linux build. No loading or boot. |
| Release image | UKI, dm-verity root, full guest runtime closure, reproducibility and boot validation remain incomplete. |
| Confidential service | Runtime LKCA, CPU/GPU attestation, measured workload/TLS binding and deployment qualification remain incomplete. |

The [raw linkage proof](../docs/tasks/p3-t1-raw-linkage-evidence.md) retains the
original symbol table and cleanup receipts. The
[driver installation proof](../docs/tasks/p3-t1-driver-install-pass.md) records
the successful opt-in run; installation alone is not module loading or boot.
The [prerequisite audit](../docs/tasks/p3-t1-guest-image-feasibility.md) records
remaining full-image inputs and source references. Earlier task documents
describe historical checkpoints, not the current aggregate status.

The separate [runtime input candidate](../docs/tasks/p3-t1-runtime-inputs.md)
extends the pinned package graph with systemd, Podman/CDI, firewall and verity
tooling. Its real offline APT rehearsal passed for all 212 exact package versions,
with no removals and confirmed cleanup. It cannot silently replace
the proven kernel/driver installation input. It is not yet a bootable image.
The explicit [runtime installer](../docs/tasks/p3-t1-runtime-install.md) reuses
the same isolation boundary and verifies the matching APT proof and reviewed
package-hook inventory before execution. Its actual installation proof is pending.
The subsequent [startup policy](../docs/tasks/p3-t1-runtime-startup-policy.md)
masks package-provided login and background-management paths and disables
journal persistence/forwarding inside the private runtime candidate only.
This is not a complete release image or a verified boot policy.
The [appraiser CLI probe](../docs/tasks/p3-t1-appraisal-cli.md) tests loading the
installed vendor executable offline; it cannot collect GPU evidence or grant
a confidential verdict.

The [measured-image decision](../docs/decisions/0004-measured-guest-image.md)
remains authoritative: immutable release/debug outputs must differ, the UKI
must authenticate the selected verity root, and two clean builds must reproduce
the published hashes. A package build, GPU CC label or working inference smoke
does not satisfy those requirements.
