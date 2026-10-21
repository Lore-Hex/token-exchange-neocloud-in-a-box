# Native Pinned Tools Runtime

Status: the seven-tool native runtime probe passed on October 21, 2026;
the additional PID-isolation run failed and remains unqualified.
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
