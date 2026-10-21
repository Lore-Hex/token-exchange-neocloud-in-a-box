# Guest Maintainer Hook Review

Status: source review of the fixed 145-package directory-installation candidate,
not an installation or a bootable guest. This does not authorize an installer.
The [review policy](../../image/inputs/r595-ubuntu2404-hook-review.json) binds
the entire [guest lock](../../image/inputs/r595-ubuntu2404.json) and a canonical
inventory of all 180 control hooks (94 distinct byte sequences) and 11 data
hooks. No package hook was executed to perform this review.

## Recheck

```sh
python -I -B scripts/review_guest_hooks.py \
  --archives /absolute/private/pinned-deb-cache \
  --output /absolute/private/new-review-result
```

The script reads exact locked archive sizes and SHA-256s, reuses the existing
control-member validator, and scans the package payloads for kernel, DKMS and
initramfs hook directories. It compares the complete inventory, including
packages without hooks, to the reviewed digest. A new, removed or changed hook,
package version, archive, mode or lock fails. It does not extract package files
to the host, run a shell, install packages or generate an image. Public package
decompression uses the existing bounded helper; the host decompressor remains
a trusted preparation dependency. The scan is bounded to 600 seconds, 512 MiB
of compressed archives, 1 GiB expanded per archive and 100,000 members per tar.
These are application limits, not a claimed OS resource sandbox.

## Findings and Required Controls

| Hook family | Observed behavior | Required installation control |
|---|---|---|
| base-files, base-passwd, login, passwd, adduser | Populate users/groups, merged-/usr links and logs; assign root, shadow, mail and utmp ownership; migrate old configurations | A new empty guest root and genuine multi-ID ownership mapping, not fake successful chown. No host account files or existing root may be reused. |
| libc6, libpam0g, libpam-modules | Upgrade-only service restart and process checks; libc6 also considers init re-exec during configure | Private PID namespace/procfs; no host init socket or `/run/systemd/system`; executable service-denial policy; noninteractive debconf and closed stdin. Chroot detection is not the only boundary. |
| libc-bin and library triggers | ldconfig rebuilds guest loader state | Guest-only root and library paths; no host loader-cache mutation. |
| bash, dash, gawk, tar, xz-utils, util-linux, perl, debianutils, readline | Alternatives, diversions, shell list and conffile changes | Writes confined to the new guest root. The reviewed `update-shells` helper updates guest files atomically. |
| libpam-runtime, libpam-modules, passwd, libselinux1 | Configure PAM/environment/shadow files; optional tmpfiles creation | Guest-only account data and `/run`; no inherited PAM or host IPC. Guest accounts remain non-login by final image policy. |
| base-files, dpkg, util-linux, libpam-modules-bin, nvidia-persistenced | Enable timers/services; guarded startup or daemon reload | No running init in the installation namespace, service-denial overlay for every APT transaction, then a separate explicit final-image unit allowlist. |
| kernel image, headers, modules, linux-base | depmod, guest boot symlink updates, kernel trigger files and run-parts | Only the locked kernel/header tree; no host `/boot` or modules; empty initial trigger state and exact discovered hook inventory. |
| dkms, nvidia-dkms-open | Automatic builds can select `uname -r` or newest guest kernel; kernel hooks delegate to DKMS | The existing [DKMS controls](p3-t1-dkms-build-controls.md) must be present before any transaction and verified afterwards. Build explicitly for 6.8.0-147-generic with GCC 13 and two jobs. Never compile automatically or load a module into the builder kernel. |
| nvidia-modprobe | postinst sets setuid and setgid on its helper | Do not inherit those permissions into a release by accident. A final-image policy must remove the helper or justify and verify its privilege boundary. |
| NVIDIA/kmod initramfs hooks | Copy module tooling/configuration and NVIDIA modules into an initramfs when invoked | No initramfs generation in the first directory rehearsal. UKI/initramfs construction needs its own reviewed input policy. |
| removal, purge and abort hooks | Remove diversions/configuration, possibly stop services, update initramfs or remove DKMS modules | The same containment applies on failed installation. Never run a package purge on the host as cleanup; discard the owned sandbox root instead. |

The 11 data hooks are eight DKMS entries, the kmod initramfs hook, linux-base's
`xx-update-initrd-links` and NVIDIA's framebuffer hook. DKMS's autopkgtest helper
is inventoried but is **not** an installation entry point and must not be run:
it intentionally installs/removes packages and clears `no-autoinstall`.
`common.postinst` exits early when `no-autoinstall` exists; kernel/header hooks
delegate to the same protected autoinstall path. The existing DKMS source review
records the corresponding exact script hashes and call sites.

This is a review of package-controlled entry points, not a formal audit of all
transitive system utilities or a claim that package scripts are trustworthy
without containment. The pinned tools tree is reconstructed from payloads;
its separate maintainer scripts are never executed by that reconstruction.

## Remaining Installation Gates

The native tools runtime passed, but the first PID-isolation run failed; see
the [native tools task](p3-t1-native-pinned-tools.md). Full ownership mapping,
actual resource limits, a successful PID/proc boundary, verified compiler/header
identity and containment of every package transaction remain required.
After those gates, run two fresh bounded directory builds, verify the exact
configured package set and compare normalized inventories. Do not assert
reproducibility, DKMS/LKCA compilation, UKI/verity, boot or confidentiality from
this source-review record.

Sources are the exact authenticated Debian/Ubuntu/NVIDIA archives named in the
guest lock, its signed-index evidence, and the [service-denial overlay review](p3-t1-mkosi-build-inputs.md).
No third-party hook source is copied into this Apache-2.0 repository.
