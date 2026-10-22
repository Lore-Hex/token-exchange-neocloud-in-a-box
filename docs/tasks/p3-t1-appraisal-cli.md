# Guest Appraiser CLI Probe

The native runtime installer now executes three fixed `nvattest --help`
operations in the installed Ubuntu 24.04 root. This closes a gap between an
installed package inventory and proof that its executable and dynamic libraries
actually load together. It is not GPU evidence collection or attestation.

The authenticated runtime lock already contains `nvattest` and `libnvat`
`2.0.0.1790976895-1`. Their Ubuntu 24.04 archives differ from the lab's Ubuntu
26.04 archives. Read-only inspection found that the executable and shared-library
payload hashes match, but this does not establish ABI compatibility. The native
run must demonstrate that independently; no success is inferred from the hashes.

The probe checks the exact executable/library payloads and SONAME link, rejects
loader preloads, and runs help for the root CLI, `attest`, and `verify-token`.
Each operation has a ten-second timeout and separate 64 KiB output limits. It
requires a successful exit, no stderr, and the documented option names. The
receipt retains only output hashes/lengths, not arbitrary error text.

References: the pinned [NVIDIA SDK source](https://github.com/NVIDIA/attestation-sdk/tree/11b2bfeb42a1158aa08744603ecb239ccb3d2265)
and [CLI reference](https://docs.nvidia.com/attestation/nv-attestation-sdk-cpp/latest/sdk-cli/command-reference.html),
checked October 22, 2026. The existing lab CLI checks use the same documented
options; the guest probe does not import the lab's installation/readiness logic.

## Boundary

Only explicit `--guest-runtime` builds run this check, after package installation
and startup-policy application. Kernel-only builds remain unchanged. The helper
is included in the committed, hash-bound source closure.

The existing private PID/network namespaces, seccomp policy and cgroup limits
remain. The child sees a read-only guest root, private tmpfs and synthetic
devices; no GPU device, service key or network connection is provided. After
the confinement check and chroot, all remaining capabilities are dropped before
executing the vendor binary. There is no attestation command without `--help`,
no arbitrary-command entry point and no retry or relaxed-loader fallback.

The runtime installer fails if this probe fails. Its receipt always says that
GPU access, networking, appraisal and boot validation were not performed.
Production guest backends remain fail-closed. Native execution evidence is
pending; fixture tests do not prove binary compatibility or confidentiality.
