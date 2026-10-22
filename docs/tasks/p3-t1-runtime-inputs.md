# Guest Runtime Inputs

This adds a separate, explicitly selected runtime **build-input candidate**.
It does not replace the proven 145-package kernel/driver graph or enable a
production guest backend. Installation, boot and confidentiality remain unproven.

The lock contains 212 exact packages, 506,686,262 archive bytes, from the same
seven authenticated Ubuntu 24.04/NVIDIA indexes observed on 2026-10-20. Every
old kernel/driver package, including its source index and hash, is unchanged.
The existing parser checks dependency versions, alternatives, virtual provides,
conflicts, essential packages and reachability; the independent APT rehearsal
must also accept exactly the locked package/version set without removals.

## Runtime Additions

| Purpose | Selected input |
| --- | --- |
| PID 1, devices, networkd and verity helpers | systemd/udev 255.4-1ubuntu8.18 |
| UKI stub input, not an installed boot policy | systemd-boot-efi 255.4-1ubuntu8.18 |
| Container process supervision | podman 4.9.3+ds1-1ubuntu0.2, crun 1.14.1-1, conmon 2.1.10+ds1-1build2 |
| Podman dependency provider | netavark; inclusion does not enable a bridge or permit container egress |
| Firewall tooling | nftables 1.0.9-1ubuntu0.1 |
| Block-integrity tooling | cryptsetup-bin 2:2.7.0-1ubuntu4.2 and dmsetup |
| GPU container interface | NVIDIA Container Toolkit 1.20.1-1 and its exact three dependencies |
| TLS roots and module compression | ca-certificates and zstd |

NVIDIA's [installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#with-apt-ubuntu-debian),
checked 2026-10-22, specifies the four matching 1.20.1-1 toolkit packages and
recommends CDI for Podman. This lock does not configure CDI or grant GPU access.
The [prior prerequisite audit](p3-t1-guest-image-feasibility.md) records the
appraisal package/source distinction. Successful package resolution does not
prove undeclared dynamic-library dependencies or runtime GPU appraisal.

Ubuntu noble's authenticated `systemd` package contains the cryptsetup and
veritysetup helpers. There is no separate `systemd-cryptsetup` package in this
snapshot. Do not invent that package or import a different distribution's
dependency set. The pinned
[mkosi initrd rules](https://github.com/systemd/mkosi/tree/ecf7f5702bb2052b7a69cab3fe7d37bc3b9ad77b/mkosi/resources/mkosi-initrd)
also distinguish noble from distributions with a separate package.

No initramfs generator, SSH server, DNS resolver daemon or time-sync daemon is
added. The kernel build compiler and essential shells remain in this candidate;
it is **not a minimized release root**. Release/debug policy, service masking,
readonly root construction, authenticated verity-root selection and application
binaries still need separate implementation. Package presence is not permission
to execute a service, expose a port or alter boot measurements.

## Verification

From a committed checkout, using private absolute output directories:

```sh
python3 -I -S -B scripts/fetch_mkosi_tools.py --guest-runtime \
  --metadata /absolute/private/metadata --output /absolute/private/runtime-inputs
python3 -I scripts/prepare_guest_tools.py --target guest-runtime \
  --metadata /absolute/private/runtime-inputs/metadata \
  --archives /absolute/private/runtime-inputs/archives \
  --output /absolute/private/runtime-plan
python3 -I host/simulate-guest-apt.py --run --target guest-runtime \
  --metadata /absolute/private/runtime-inputs/metadata \
  --archives /absolute/private/runtime-inputs/archives \
  --bundle /absolute/private/runtime-plan --output /absolute/private/runtime-result
```

The fetcher retains the existing four-transfer, 600-second aggregate deadline,
host allowlist, byte/hash checks, bounded transport retries and all-or-nothing
publication. Runtime selection is mutually exclusive with the original guest
selection. A scope mismatch is rejected, never guessed from arbitrary input.
Both native installation and default APT simulation still use the old graph.

The simulation reuses the existing Linux/amd64 non-root, network-disabled,
read-only container with no mounts, devices or capabilities, 1 GiB memory,
two CPUs and 64 PIDs. Only the fixed APT tools execute. Target maintainer
scripts, runtime executables and services do not execute. Receipts bind the
selected runtime lock and producer commit; container and image cleanup are
required for a passing result.

Regression tests cover additive package preservation, exact dependency closure,
explicit scope selection, wrong-lock rejection, hash/mirror rejection and
refusal to promote a dependency-plan result to installation or boot success.
Real rehearsal evidence will be linked here after the committed producer runs.
