# Offline Runtime Installation

Status: implementation of a bounded directory-build candidate, not a bootable
guest. The [runtime inputs](p3-t1-runtime-inputs.md) have passed a real offline
APT simulation. An actual 212-package installation has not yet been run.
No GPU, server, secret, production endpoint or existing installation is changed.

## Explicit Runtime Target

The manual, main-only `mkosi-native-build.yml` workflow accepts `guest_runtime`
(default false). It selects the pinned 212-package closure instead of the
original 145-package kernel/driver closure. The same installer, namespace
checks, resource bounds, service-denial overlay and independent cleanup apply.
Optional driver compilation and installation still require their explicit flags.
No mode loads a module into the builder kernel or boots the resulting root.

Before runtime preparation, the host requires the committed APT pass to match
the exact lock hash, all 212 planned installs/configures, zero removals and
confirmed cleanup. It re-hashes every package and every reviewed control/data
hook against the fixed policy below. Changed inputs require another reviewed
policy and a matching real APT rehearsal; they cannot reuse old evidence.
The worker then requires every expected package at its exact version with
`install ok installed` state. Neither a partial install nor exit zero alone passes.

```sh
python -I -B scripts/review_guest_hooks.py --guest-runtime \
  --archives /absolute/private/pinned-deb-cache \
  --output /absolute/private/new-runtime-hook-result
```

Actual installation is only through the existing source-bound GitHub runner
launcher, not an ordinary root shell or the local workstation:

```sh
sudo -- /usr/bin/python3.10 -I -S -B host/run-mkosi-build.py \
  --downloads /absolute/private/inputs \
  --archive /absolute/private/mkosi-source.tar.gz \
  --output /absolute/private/new-runtime-install-result \
  --guest-runtime --compile-driver --install-driver
```

## Runtime Hook Review

The [review policy](../../image/inputs/guest-runtime-ubuntu2404-hook-review.json)
binds 212 packages, 257 control hooks and 17 kernel/initramfs/DKMS data hooks.
The original 145 packages are byte-identical to the
[earlier source review](p3-t1-guest-hook-review.md). This delta adds 67 packages,
77 control hooks and six data hooks. Review inspected the actual authenticated
public archive contents without executing them. This is source review, not
independent review or proof that arbitrary package hooks are safe.

| Added family | Observed behavior | Required containment / later image policy |
|---|---|---|
| systemd, systemd-sysv | Check merged-/usr; create machine ID, persistent journal directory, tmpfiles and users; enable getty, remote-fs and pstore; upgrade-only reexec/restart | Fresh guest root, private PID/proc/run and no host manager socket. Do not deploy this raw root. Later hardening must remove login/pstore/persistence and normalize machine-specific state. |
| udev | Compile hardware database, create users/tmpfiles, guarded daemon reload/start; initramfs trigger | Private `/run`, synthetic devices, no physical device or host udev control socket. No initramfs generator is in this candidate. |
| Podman | Enable API socket, restart/cleanup and auto-update units; tmpfiles and old-conffile migration | Service denial and no running manager during install. Final release must use an explicit unit allowlist, no Podman API socket or auto-update timer. Installing Podman does not authorize starting containers. |
| NVIDIA container toolkit | Normalize guest runtime TOML with `nvidia-ctk`; try udev rule reload; enable CDI refresh path/service | No GPU devices, no host udev socket, denied service startup. CDI device enumeration belongs to the verified boot pipeline, not this build. |
| nftables | Maintain unit enablement state; guarded reload/restart | Private network namespace and service denial. Installation must not change host firewall state. Runtime firewall validation remains separate. |
| cryptsetup-bin, dmsetup | tmpfiles and update-initramfs triggers | No host block devices or device mapper access. No volume activation or initramfs build in this task. |
| ca-certificates, OpenSSL | Debconf selection; certificate symlinks/bundle; deferred certificate-update hooks | Noninteractive install and guest-only trust store. No host certificate or private key is copied. This is not an attestation-vendor root-policy review. |
| GPG agent, dirmngr, keyboxd, pinentry | Enable user sockets; alternatives and conffile maintenance | No user manager or host home/socket. These package dependencies must not accidentally become release services. |
| GLib and library triggers | Compile schemas/module caches and run ldconfig | Guest-only library paths, private process state and bounded output. |

The six additional data hooks are dmsetup and udev's initramfs hooks plus
systemd's `50-depmod`, `55-initrd`, `90-loaderentry` and `90-uki-copy` hooks.
They copy guest files or update guest kernel/boot metadata when explicitly
invoked. No initramfs generator, host `/boot`, host module tree or real block
device is exposed. UKI construction needs its own deterministic input policy.

Package control hooks are not the complete transitive executable graph. All
archive bytes are pinned, while the hook inventory explicitly covers only the
documented entry-point directories. Existing syscall/device/network/mount/PID
containment remains mandatory, including on failures. Cleanup discards the
owned sandbox; it never purges packages from the host.

## Remaining Work

Run and preserve an actual runtime installation and driver-build result after
CI and review. Then minimize and harden the root, add the real guest services
and pinned attestation appraisers, build UKI/verity artifacts twice, compare
them, boot supported confidential hardware, and verify live hardware evidence.
This candidate does not satisfy those later acceptance criteria.

Primary source inputs are the exact signed-index package records in the
[runtime lock](../../image/inputs/guest-runtime-ubuntu2404.json), its linked
authenticated-index evidence, and the pinned mkosi source/patches already
reviewed for [native installation](p3-t1-mkosi-build-inputs.md). No upstream
hook source is copied into this Apache-2.0 repository.

The committed checker passed against all real pinned archives at producer
`735dd67074674c6ef9af320229dc8c817304c387`. Unmodified
[run](../../evidence/p3/2026-10-22-runtime-hook-review/run.json),
[result](../../evidence/p3/2026-10-22-runtime-hook-review/result.json) and
[inventory](../../evidence/p3/2026-10-22-runtime-hook-review/inventory.json)
are preserved. This records a hash recheck of the source review, not package
execution or independent approval of the changes.
