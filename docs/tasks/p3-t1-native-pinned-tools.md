# Native Pinned Tools Runtime

Status: implemented and synthetic-tested; a real workflow run is required.
This builds on the [native namespace proof](p3-t1-native-mkosi-probe.md), not
on a relaxed Docker policy. It does not install packages or build a guest.

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

## Scope and Next Gates

A pass establishes these fixed programs can start within this native isolation
boundary. It does **not** establish complete ABI compatibility, a package
installation, maintainer-hook safety, PID isolation, DKMS compilation, UKI/verity
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
