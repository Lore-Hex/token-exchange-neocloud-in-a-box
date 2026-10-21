# Package Execution Boundary

The prepared mkosi source now wraps every `Apt.invoke` operation in the
[already exercised Linux privilege boundary](p3-t1-native-pinned-tools.md#final-confinement).
This is preparation and synthetic testing, not an actual package installation.

The trusted, pinned mkosi sandbox first establishes private mounts and the
tools filesystem. Only then does `setpriv` drop capabilities and enable
`no_new_privs`. The fixed Python worker requires the isolated interpreter,
root in the user namespace, a loopback-only network namespace, and an exact
`apt-get` target. It installs the existing seccomp policy, verifies the
capability/FD/read-only checks, and replaces itself with `/usr/bin/apt-get`.
Package hooks inherit those restrictions. No interpreter search, arbitrary
worker command, host package installation or network access is introduced.

The insertion applies to the exact pinned upstream
[Apt.invoke](https://github.com/systemd/mkosi/blob/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/installer/apt.py)
after the existing service-denial fix. Both intermediate and final bytes are
recorded separately; their hashes are pinned. Upstream's LGPL-2.1-or-later
license and original source remain in the prepared bundle. No fuzzy patching
or fallback to the unguarded installer is allowed.

The authenticated source-closure limit increases from 16 to 24 paths to cover
the four new fixed inputs in the existing nested native-probe closure. It
does not permit arbitrary paths, dirty source, symlinks or uncommitted code.

Still required: the combined cgroup/user-namespace build runner, actual
package installation, configured-state checks, explicit bounded DKMS build,
two-build reproducibility, measured boot and real end-to-end attestation.
