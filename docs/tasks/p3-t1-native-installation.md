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
