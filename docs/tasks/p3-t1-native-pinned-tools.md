# Native Pinned Tools Runtime

Status: the seven-tool runtime and additional PID-isolation probe both passed
on October 21, 2026. Earlier failed attempts are preserved below.
This builds on the [native namespace proof](p3-t1-native-mkosi-probe.md), not
on a relaxed Docker policy. It does not install packages or build a guest.

The [unaltered result](../../evidence/p3/2026-10-21-native-pinned-tools/result.json)
and [run record](../../evidence/p3/2026-10-21-native-pinned-tools/run.json) are from
[workflow 39617825016](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39617825016),
merged main `a56acf50019c4ae35fc463e33ec767396b8787bc`. All seven startups,
namespace checks, process cleanup and temporary-payload removal passed.

## Inputs and Isolation

The manual **Native pinned mkosi tools probe** workflow runs only on merged
`main`, on an ephemeral Ubuntu 22.04 runner without credentials or host policy
changes. Its ten-minute fetch budget selects only the committed 203-package
Ubuntu 24.04 tools graph and its six pinned indexes. The indexes use Ubuntu's
content-addressed `by-hash/SHA256` paths. Package URLs come from the existing
authenticated lock, not a new resolver. Each response must match the exact
size and SHA-256; old inputs disappearing is an error, never a floating upgrade.

The downloader accepts only HTTPS on `archive.ubuntu.com` and
`security.ubuntu.com`, with no curl configuration, proxy, redirects, credentials
or retries. Each request has a deadline and output limit; a shared deadline
bounds the whole fetch. Nothing is published until all bytes verify. The
existing preparer then rechecks metadata, package/control bytes and the exact
member policy before reconstructing the tools tar and inventory.

The native runner materializes only that reconstructed tar. It checks every
member against the inventory, writes files before creating links, rejects
devices/hard links/traversal and preserves the executable modes without setuid
or setgid. Absolute package symlinks are never followed on the host. A private
temporary directory is removed after success or failure.

The authenticated mkosi 27.1 sandbox uses the host's system Python 3.10 only
as its launcher. Inside the sandbox, `/` is the read-only pinned tools tree;
the child is the tree's Python 3.12, not a host/tool-cache interpreter. Only
the verified code payload, its namespace handles, minimal devices and private
temporary storage are added. No checkout, home, credentials, host `/usr`, full
host `/proc` or network interface is exposed.

The same namespace checker runs before the seven existing startup checks:
Python, APT, dpkg, bubblewrap, bsdtar, systemd-repart and awk. The outer process
group has a 90-second deadline and 64 KiB output ceiling. Only the exact success
marker is accepted; no raw subprocess diagnostics are persisted. Evidence
contains lock/root/payload hashes, the fixed outcome, counts and cleanup state.

## PID Isolation

The separate `--pid-isolation` mode executes the pinned tools tree's
`/usr/bin/unshare --pid --fork --mount-proc=/proc --kill-child=KILL` before the
same worker. No additional host tool, sudo, sysctl or policy relaxation is used.
The native workflow exposes only a boolean `pid_isolation` input; it never
interpolates user-controlled arguments into a shell command.

The worker must be PID 1 with parent PID 0 in a different PID namespace. Its
fresh procfs must expose exactly PID 1 before the tool subprocesses begin;
`/proc/self` and `NSpid` must agree. A host procfs, unchanged namespace, extra
process or malformed identity stops execution before any tool starts. Both
successful PID proof and all seven tool startups are required for the v2
report's process-isolation claims. The original v1 mode is unchanged.

The flag and lifetime semantics are from the authenticated Ubuntu
`util-linux 2.39.3-9ubuntu6.6` package's `unshare(1)` manual and
[upstream implementation](https://github.com/util-linux/util-linux/blob/v2.39.3/sys-utils/unshare.c).
`--mount-proc` implies a new mount namespace; `--kill-child` kills the forked
child when the launcher dies, and exiting PID 1 terminates its PID namespace.
The existing outer deadline and process-group cleanup still apply. This is
not yet proof of safe package hooks, a complete installation sandbox, resource
limits for a build or reproducible outputs.

### Actual PID Run

[Run 39619866911](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39619866911)
at main `cc299743125b3221297a1847e13e092f7c6f66e3` failed on October 21.
The [unaltered result](../../evidence/p3/2026-10-21-native-pid-denied/result.json)
and [run record](../../evidence/p3/2026-10-21-native-pid-denied/run.json) show
verified inputs and successful process/payload cleanup, but no namespace or
tools success. The old result did not distinguish launcher rejection from a
worker check failure, so the exact cause is not yet established.

Subsequent runs retain only fixed failure categories: launcher permission,
missing path, invalid argument or other exit; worker preflight, namespace, PID,
input or tools checks; timeout; startup; or invalid result/cleanup. Exception
text and subprocess output are discarded. Exact worker markers are required;
extra or malformed bytes cannot become a success or a trusted worker label.
No mount, capability, check or host policy has been relaxed.

## Scope and Next Gates

A pass establishes these fixed programs can start within this native isolation
boundary. It does **not** establish complete ABI compatibility, a package
installation, maintainer-hook safety, DKMS compilation, UKI/verity
generation, reproducibility, boot or attestation. The host kernel, initial
launcher and transfer/decompression tools remain unpinned builder dependencies.
The Ubuntu 22.04 runner is a diagnostic host, not a release-builder commitment.

The earlier requirement to review installation hooks, isolate installation
processes and enforce explicit kernel/compiler/DKMS controls still applies.
No package-manager installation command is invoked by this probe.

Sources: the existing [tools lock and archive review](p3-t1-mkosi-build-inputs.md),
[exact member policy](p3-t1-mkosi-tools-members.md), pinned
[mkosi sandbox](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/sandbox.py),
and [Debian repository by-hash format](https://wiki.debian.org/DebianRepository/Format#A.22by-hash.22_acquisition).

Tests: `python -B -m unittest discover -s scripts -p 'test_native_tools.py'`.
## PID Procfs Mount Order

The source-bound diagnostic on main `ed5ab563149c26abf60177b0fce22f646526633a`
failed before worker execution with `unshare-permission-denied`. Cleanup and
payload removal passed. Original evidence is retained under
`evidence/p3/2026-10-21-native-pid-permission-denied/`. It does not identify the
exact denied syscall or establish any successful PID isolation.

The previous command retained only `/proc/self/ns` after pivoting into its
tools root. Linux's [mnt_already_visible and mount_too_revealing checks](https://github.com/torvalds/linux/blob/v6.8/fs/namespace.c#L4981-L5061)
reject a new procfs in an unprivileged mount namespace when no complete procfs
is already visible. A subtree bind does not satisfy that condition. This is a
source-backed explanation to test, not a claim of kernel tracing from the run.

PID mode now gives the pinned util-linux launcher a read-only complete `/proc`
bind, then requires `unshare --pid --fork --mount-proc=/proc --kill-child=KILL`
to replace it before executing the fixed worker. There is no caller command,
shell, package installation, or automatic non-PID retry in between. The worker
still rejects unless its PID is 1, parent is 0, PID namespace differs from the
host, procfs contains only process 1, and all existing namespace/readonly/network
checks pass before any tool invocation. The non-PID mode retains its narrower
namespace-only bind. No host policy, capability, or privilege setting changes.

This is not yet a general-purpose hostile-workload sandbox: the trusted launcher
temporarily sees host procfs, and worker namespace root can administer its own
mounts. Installation requires a separately qualified final mount/capability
boundary, UID ownership and resource limits. Actual execution must prove this
candidate before any installation or image qualification claim.

The corrected command passed in [run 39624972686](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39624972686)
at merged main `80e0db46d72a48d0d755ffd7ee7755f3c36d11ac`. Original
[result](../../evidence/p3/2026-10-21-native-pid-isolated/result.json) and
[run](../../evidence/p3/2026-10-21-native-pid-isolated/run.json) prove private PID
namespace/procfs, all seven startups and process/payload cleanup. This supports
the procfs mount-order diagnosis; it is not kernel syscall tracing.

## Mapped Ownership

`--mapped-ownership --pid-isolation` tests existing subordinate UID/GID support
without installing packages or changing host policy. It requires root-owned,
non-writable sub-ID files and setuid mapping helpers, one non-overlapping range
for the current user, and at least 65535 subordinate identities. Missing or
ambiguous ranges fail; no sudo, new range, chown suppression or single-ID retry
is used. Host identities are kept in the temporary payload, not evidence.

The verified tools tree's ELF loader starts its pinned util-linux 2.39.3
`unshare` with explicit `inner:outer:count` maps and the caller mapped to root.
Its [upstream implementation](https://github.com/util-linux/util-linux/blob/v2.39.3/sys-utils/unshare.c)
invokes the existing `newuidmap`/`newgidmap` helpers; those host binaries remain
unpinned builder dependencies whose hashes are recorded, not release inputs.
The same mkosi namespace and pinned PID launcher follow. Before starting tools,
the child verifies both complete maps and actual independent UID/GID changes on
one private-tmpfs file, including identities 65534 and 65535, then removes it.

The mapped-ownership run passed on October 21 in
[workflow 39628955797](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39628955797)
at merged main `28e40cf38ab2f0002e23d16b69e64fb85665c20a`. The original
[result](../../evidence/p3/2026-10-21-native-mapped-ownership/result.json) and
[run](../../evidence/p3/2026-10-21-native-mapped-ownership/run.json) show exact
mapping, actual ownership changes, seven tool startups and cleanup passed.
No package installation or image qualification is inferred.

## Final Confinement

`--final-confinement` requires both ownership and PID modes. After the pinned
launcher creates the namespaces and replaces procfs, pinned util-linux
`setpriv` retains only CHOWN, DAC_OVERRIDE, FOWNER, FSETID, SETGID, SETUID,
SYS_CHROOT and SETFCAP. Inheritable and ambient sets are empty;
`no_new_privs` is irreversible. The child rejects unless effective, permitted
and bounding sets match exactly. No host directory descriptor may survive.

Before tool execution, pinned libseccomp prevents new namespaces, namespace
entry, mount changes, ptrace/process-memory access, file-handle opens, BPF,
performance/userfault interfaces and io_uring creation. Ordinary clone is
allowed but every namespace clone flag is denied. clone3 returns ENOSYS so
libc can use ordinary clone; a pointer-based clone3 policy cannot inspect its
flags. The native-only architecture filter rejects other ABIs. Unknown syscall
names, failed filter setup or missing libraries fail closed. Negative unshare
and mount probes must return EPERM; the tools root must still return EROFS.
The filter and privilege restriction are inherited by child tools.

Sources: [util-linux 2.39.3 setpriv manual](https://github.com/util-linux/util-linux/blob/v2.39.3/sys-utils/setpriv.1.adoc)
and [implementation](https://github.com/util-linux/util-linux/blob/v2.39.3/sys-utils/setpriv.c),
[libseccomp 2.5.5 API](https://github.com/seccomp/libseccomp/blob/v2.5.5/include/seccomp.h.in),
[rule-array contract](https://github.com/seccomp/libseccomp/blob/v2.5.5/doc/man/man3/seccomp_rule_add.3),
and Linux 6.8 [clone flags](https://github.com/torvalds/linux/blob/v6.8/include/uapi/linux/sched.h)
and [capabilities](https://github.com/torvalds/linux/blob/v6.8/include/uapi/linux/capability.h).
Executable/library bytes remain the existing authenticated tools lock.

The full boundary passed on merged main
`6e4c4d2a412e4a1678234c26416e8c3af13f5a57` in
[workflow 39633495751](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39633495751).
The unaltered [result](../../evidence/p3/2026-10-21-final-confinement/result.json)
and [run record](../../evidence/p3/2026-10-21-final-confinement/run.json) show
namespace, ownership, capability/syscall checks, seven tool startups and cleanup
passed. No package hooks or installations ran.

The separate [resource-controller probe](p3-t1-build-resource-probe.md) also
passed, on a different lab host. Combining both boundaries in the actual
installer, exact build controls and two independent builds remain required
before image qualification. Two separate probes are not a completed builder.
