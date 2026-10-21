# P3.T1 bounded native package installation

This adds one manual, main-only native installation experiment. It is not a
bootable image, DKMS compilation, reproducibility result or confidential release.
No installation result is claimed by the unit tests.

The fixed GitHub-hosted Ubuntu 22.04 runner fetches the original public indexes
from the [input archive](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/releases/tag/build-input-indexes-20261020).
Its SHA-256 and every size/hash-locked member are mandatory. All 269 tools and
guest archives are verified before execution. The pinned mkosi source remains
at `ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b` with the reviewed service-denial and
[package confinement](p3-t1-package-boundary.md) overlays.

`host/run-mkosi-build.py` uses root only to create a fresh bounded cgroup, join
the trusted launcher, and drop to the existing runner UID/GID. It changes no
parent controllers or host accounts. The child verifies limit readback. Limits
are 8 GiB memory, no swap, two CPUs, and 256 processes. The supervisor kills the
entire cgroup, including detached descendants, and verifies it empty and removed.
It writes an independent cleanup receipt to an already-open, exclusive file.

The builder reconstructs read-only pinned tools and source trees. Installation
runs with private user/mount/network/IPC/PID namespaces, a private procfs,
loopback only, and tmpfs work storage. It exposes no checkout, credentials, host
home, sockets, or writable host filesystem except one fresh diagnostics folder.
Every APT transaction applies the final capability/syscall restrictions after
its own namespace setup. DKMS automatic compilation and module loading remain
disabled by the existing pinned controls.

The fixed directory-only build has a 30-minute inner deadline, bounded output,
and no incremental cache. A pass requires exactly the 145 locked package
versions with dpkg state `install ok installed`, successful launcher cleanup,
and no OOM kill. Failures retain typed stage information and bounded package-only
diagnostics. Diagnostic artifacts expire after seven days and must be scrubbed
before committing public evidence. The temporary installed tree is discarded.

Verification: `python -m unittest discover -s scripts -p test_mkosi_build.py` and
`make check`. These tests cover rejection of inventory drift, sandbox command
construction, privilege dropping, timeout cleanup, OOM/cleanup failure, receipt
symlink replacement, committed source closure and workflow restrictions. They
do not execute package hooks or mutate test-host cgroups.

Next: run this workflow, fix any source-backed installation incompatibility,
record actual evidence, then add explicit pinned-kernel DKMS compilation and
two clean output comparisons. UKI, verity, measured boot and hardware attestation
remain separate gates.

## First native run: payload rejected

[Run 39650473707](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39650473707)
at `5571de118ba7d03b9efea33bd9478ab14b92e8a8` authenticated the inputs,
then failed during preparation before a package worker was created. Original
[result](../../evidence/p3/2026-10-21-native-install-payload-rejected/result.json),
[run](../../evidence/p3/2026-10-21-native-install-payload-rejected/run.json) and
[resource receipt](../../evidence/p3/2026-10-21-native-install-payload-rejected/resources.json)
are preserved. Independent cgroup cleanup verified zero remaining processes and
removed the group; no OOM kill occurred. The inner process-cleanup flag remains
false because that launcher was never started, not rewritten to imply success.

The shared file publisher rejected `@`, present in the pinned mkosi path
`mkosi/resources/mkosi-initrd/mkosi.extra/usr/lib/systemd/system/systemd-cryptsetup@.service.d/credential.conf`.
A test publishing every pinned regular-file name with synthetic bytes reproduced
the rejection. Allowing literal `@` fixes that concrete incompatibility without
changing exact source membership/hash validation, traversal rejection, exclusive
creation or descriptor-relative no-follow traversal. The original coarse stage
does not prove that this was the run's only issue. New fixed stages distinguish
tools materialization, payload publication and upstream modes/links; a separate
test proves publication rejection cannot reach package execution.

## Second native run: private build identity

[Run 39661661824](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39661661824)
passed payload preparation and entered the isolated installation worker. It
failed in pinned mkosi `config.py:4716`, where `getpass.getuser()` fell back to
`pwd.getpwuid(0)` and the minimal extracted tools tree had no passwd entry.
The original [run](../../evidence/p3/2026-10-21-native-install-build-identity/run.json),
[result](../../evidence/p3/2026-10-21-native-install-build-identity/result.json),
[diagnostic](../../evidence/p3/2026-10-21-native-install-build-identity/installer.stderr)
and [resource receipt](../../evidence/p3/2026-10-21-native-install-build-identity/resources.json)
are preserved. Process and cgroup cleanup passed, no OOM occurred, and no
package-installation success is claimed.

The installer's fixed environment now declares `USER=root` and `LOGNAME=root`
only after the existing namespace/UID/GID checks. Python's documented
[`getpass.getuser`](https://docs.python.org/3.12/library/getpass.html#getpass.getuser)
uses these before passwd lookup. The pinned mkosi call is in
[`create_argument_parser`](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/config.py#L4716).
No host passwd/group database is mounted, no account is created, and no
privilege, capability, network or filesystem permission is broadened. A test
reproduces missing-passwd failure, then verifies the fixed isolated identity
without calling the host account lookup or inheriting ambient variables.

## Third native run: candidate tools path

[Run 39664917892](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39664917892)
passed the identity boundary but rejected `tools-tree does not exist`. The
[result](../../evidence/p3/2026-10-21-native-install-tools-path/result.json),
[diagnostic](../../evidence/p3/2026-10-21-native-install-tools-path/installer.stderr)
and [cleanup receipt](../../evidence/p3/2026-10-21-native-install-tools-path/resources.json)
preserve the failure; installation, boot and DKMS remain unproven.

Pinned mkosi's
[`parse_path`](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/config.py#L736)
validates the candidate's `ToolsTree=tools-tree` while reading configuration;
the later `--tools-tree=/` override does not avoid that validation. The worker
now supplies the missing relative alias, pointing to `/` inside its existing
private mount namespace. That root is already the authenticated extracted
tools tree, not the host filesystem. Candidate bytes, offline package sources,
namespace boundaries, resource limits and the final command are unchanged.

## Fourth native run: offline resolver mountpoint

[Run 39669380453](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39669380453)
reached the next mkosi check and rejected the tools tree's missing
`/etc/resolv.conf`. The original
[diagnostic](../../evidence/p3/2026-10-21-native-install-resolver/installer.stderr),
[result](../../evidence/p3/2026-10-21-native-install-resolver/result.json) and
[resource cleanup](../../evidence/p3/2026-10-21-native-install-resolver/evidence-resources.json)
record zero installed packages, no OOM and confirmed cleanup. No boot or
successful installation is claimed.

Pinned mkosi's
[`ensure_tools_tree_has_etc_resolv_conf`](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/__init__.py#L5017)
requires the mountpoint even for this offline build. Tools-tree preparation now
adds a deterministic empty regular file, mode 0644, before producing its tar,
inventory and hash receipt. The prepared file is explicitly reported apart
from package-owned data. Existing members and aliased ancestors are rejected,
never overwritten. No host resolver, DNS server, search domain or account data
is read. The network namespace remains isolated and package sources remain
local. Tests cover tar/inventory consistency, deterministic bytes and modes,
repeated creation, source collisions and alias rejection. Full `make check`
and a fresh bounded native run remain required.

## Fifth native run: bounded diagnostics

[Run 39672987060](https://github.com/Lore-Hex/token-exchange-neocloud-in-a-box/actions/runs/39672987060)
failed in the installation stage without returning an installer exit code or
diagnostic. The original
[result](../../evidence/p3/2026-10-21-native-build-diagnostic/result.json) and
[resource receipt](../../evidence/p3/2026-10-21-native-build-diagnostic/evidence-resources.json)
prove cleanup and no OOM, but do not prove the underlying installer failure.
Inspection found that an output-limit exception discards the entire bounded
prefix. This is a possible cause, not a confirmed attribution for that run.

The process supervisor now raises a typed output-limit exception carrying only
the bounded prefix and fixed stream name. The offline package worker preserves
those public source/package diagnostics and still fails. The stdout limit,
64 KiB stderr limit, timeout, process cleanup and resource limits are unchanged.
Real subprocess tests cover both overflowing streams, exact prefix bounds and
process-group absence. Worker tests prove diagnostic capture does not convert
either overflow or nonzero exit to success. A fresh native run is required;
installation, DKMS, boot and reproducibility remain unproven.

The shared supervisor is source-pinned by the engine recorder. Its four
transitive source-hash constants are refreshed to the exact reviewed bytes;
historical receipts and producer-specific replay fixtures are unchanged.
