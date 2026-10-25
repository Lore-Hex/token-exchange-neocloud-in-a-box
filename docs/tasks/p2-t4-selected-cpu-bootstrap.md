# Disposable CPU preparation for the selected NAT proof

Status: software only; tested with temporary files and mocked processes. No
installation, namespace execution, host firewall changes or venue qualification
is claimed. This prepares prerequisites for the separately approved
[closed-NAT experiment](p2-t4-selected-nat-proof.md), not a public service.

## Narrow changes

[prepare-selected-cpu.py](../../host/prepare-selected-cpu.py) accepts only a new,
explicitly identified Ubuntu 24.04 amd64 CPU venue. It requires root, cgroup v2,
the SHA-256 of that venue's exact `/etc/machine-id` bytes, no existing Docker or
containerd tools/state, and a new fixed `/opt/nib-selected-nat-tools` directory.
Never run this on the retained-cache CPU, GPU host or a production machine.

There is **no apt/dpkg installation, upgrade, package removal, service start,
daemon configuration, firewall command, GPU operation or proof execution**.
Existing nftables, unshare, mount, iproute2, iptables-nft and OpenSSL executables
must match authenticated, pinned Ubuntu package payload bytes; exact installed
package revisions must also match. Missing or different prerequisites stop
before mutation. `dpkg-deb --fsys-tarfile` only reads a private verified archive.
It never executes maintainer scripts. Base-image libraries/kernel remain venue
inputs, not a claimed hermetic toolchain or security qualification.

After all checks, `--install` extracts pinned CPython 3.12.13 privately, verifies
the interpreter and all eight static Docker executables, and publishes the Docker
tools at `/usr/bin` with atomic no-replace links. This location matches the
proof's fixed PATH. It neither replaces Ubuntu Python nor creates a Docker
service. The only Docker execution is `dockerd --version`, not daemon startup.

An explicit `--regular-resolver` flag permits replacing the existing
`/etc/resolv.conf` symlink only when it resolves to one of systemd-resolved's two
known files. It preserves the original link and exact resolver bytes privately
under the prefix, then atomically publishes the same bytes as a root-owned 0644
regular file. The running resolver is not stopped or reconfigured. This
preserves the current DNS settings; future automatic changes to the original
target will no longer update this copy. This is suitable only for the short-lived
disposable venue. The proof's nonsymlink guard is unchanged.

## Sources and pins

The [committed pins](../../host/selected-cpu-bootstrap-pins.json) contain exact
URLs, byte sizes and SHA-256 digests. Research on 2026-10-25 downloaded and hashed:

- [Astral Python build 20260414](https://github.com/astral-sh/python-build-standalone/releases/tag/20260414),
  generic x86_64 GNU/Linux CPython 3.12.13 `install_only_stripped`; archive digest
  matches the release API asset digest. This is an Astral binary, not a claim of
  reproducible equivalence to PSF source. Its whole prefix must remain intact.
- [Docker's official static 29.1.3 archive](https://download.docker.com/linux/static/stable/x86_64/),
  eight ELF amd64 binaries. Digests are calculated from the official HTTPS bytes,
  not represented as vendor-signature verification. Docker documents static
  binaries for [testing](https://docs.docker.com/engine/install/binaries/), not
  this experiment as a supported production configuration.

Ubuntu package hashes come from the official
[Noble main index](https://archive.ubuntu.com/ubuntu/dists/noble/main/binary-amd64/Packages.xz)
and [Noble updates index](https://archive.ubuntu.com/ubuntu/dists/noble-updates/main/binary-amd64/Packages.xz).
The nftables, util-linux, mount and OpenSSL hashes also match existing
[guest-runtime pins](../../image/inputs/guest-runtime-ubuntu2404.json). No ambient
repository is consulted on the venue. Only the committed hashes authorize
input bytes; this task did not repeat the Ubuntu InRelease signature audit.

## Reviewed command sequence

Use a clean checkout of this task's final reviewed commit. Downloads can be
prepared elsewhere and transferred with the checkout. Each invocation has one
600-second monotonic budget, including a 25-second command cleanup reserve;
downloads/tools are individually capped at 120 seconds with bounded output.
SIGINT/SIGTERM stop new work, retain the original budget and permit child reaping.

```sh
python3 -I -S -B host/prepare-selected-cpu.py --download --inputs "$NEW_INPUTS"
```

On the **approved new CPU only**, inputs and ancestors must be root-owned,
nonsymlink and not group/world writable; use a new private directory under
`/root`. Obtain `MACHINE_ID_SHA256` from the parent's independently identified
new venue, never by automatically accepting the local machine at installation.
Keep that binding and all SSH addresses private. Preserve each original JSON
stdout and command exit code independently, including failures.

```sh
python3 -I -S -B host/prepare-selected-cpu.py --check --inputs "$INPUTS" \
  --machine-id-sha256 "$MACHINE_ID_SHA256" --regular-resolver
python3 -I -S -B host/prepare-selected-cpu.py --install --inputs "$INPUTS" \
  --machine-id-sha256 "$MACHINE_ID_SHA256" --regular-resolver
```

A mismatch is a stop, not permission to loosen pins or update the host.
Cancellation/failure after `mutation_started=true` may leave partial installation;
no rollback or blind retry is claimed. Keep the private prefix and receipt for
review. New destination existence blocks another install. Do not publish the
prefix: it includes original resolver settings. A passing preparation receipt
still sets `venue_qualified=false` and `namespace_proof_executed=false`.

Before separate proof approval, inspect the actual kernel/module availability,
new host's retirement deadline, preparation receipt and offline image archive.
Use `/opt/nib-selected-nat-tools/python/bin/python3.12 -I -S -B` for that proof.
No namespace command is invoked by this preparation script. No public ingress,
provider registration or external DNS change is authorized here.

## Local verification limits

All eight input downloads were checked against their exact sizes and hashes.
Python's 4,769 members and Docker's nine members passed bounded path/type/link
validation, and the interpreter plus eight Docker executable hashes matched.
None of those Linux binaries was executed. Full Python extraction on the local
case-insensitive macOS filesystem rejected the upstream case-distinct terminfo
alias `N/NCR260VT300WPP` to `n/ncr260vt300wpp`; no archive policy was loosened.
A focused case-sensitive extraction regression must execute in Linux CI. Actual
whole-prefix extraction and interpreter execution on Ubuntu remain installation
checks, not claimed local results.
